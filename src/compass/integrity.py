"""Проверки целостности бэктеста: ловят заглядывание в будущее и нарушения правил исполнения.

Идея из Freqtrade (lookahead-analysis): правило должно давать на свече i тот же ответ, видит ли оно
ряд целиком или только свечи до i включительно. Если ответ меняется — правило подглядывает вперёд,
и красивая доходность бэктеста недостижима в жизни.

Проверки:
- causality — решение стратегии не зависит от будущих свечей (усечение ряда в нескольких точках);
- data_order — время свечей строго растёт, цены целы и не противоречат друг другу;
- execution_timing — вход и выход «по сигналу» исполнены по открытию свечи, СЛЕДУЮЩЕЙ за сигнальной;
- no_overlap — сделки не пересекаются, количество положительно (long/flat без плеча);
- equity_consistency — итог капитала сходится с суммой результатов сделок;
- ambiguity — сколько раз порядок событий внутри свечи был неизвестен (информационная).

Это проверки самого движка и правила, а не стратегии как идеи: прохождение не говорит, что правило
прибыльно или устойчиво — только что бэктест не получил результат нечестным путём.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from compass.backtest import BacktestResult, Rules
from compass.strategies import Strategy

CUT_FRACTIONS = (0.5, 0.7, 0.85, 0.95)
MIN_CUT = 30  # меньше свечей до среза — у индикаторов ещё прогрев, сравнивать нечего
_TOL = 1e-9


def _check(cid: str, title: str, status: str, detail: str) -> dict[str, str]:
    return {"id": cid, "title": title, "status": status, "detail": detail}


def check_causality(strat: Strategy, params: dict[str, int], df: pd.DataFrame) -> dict[str, str]:
    """Решение на свече i не должно зависеть от свечей после i. Сравниваем ответ на усечённом ряде с ответом
    на полном: любое расхождение — заглядывание вперёд."""
    n = len(df)
    full = strat.target(df, params).to_numpy()
    cuts = sorted({int(n * f) for f in CUT_FRACTIONS if int(n * f) >= MIN_CUT})
    if not cuts:
        return _check("causality", "Правило не смотрит в будущее", "skipped",
                      f"Слишком мало свечей для проверки (нужно хотя бы {MIN_CUT * 2}).")
    for c in cuts:
        part = strat.target(df.iloc[:c].reset_index(drop=True), params).to_numpy()
        bad = np.flatnonzero(part != full[:c])
        if len(bad):
            i = int(bad[0])
            return _check(
                "causality", "Правило не смотрит в будущее", "fail",
                f"Решение на свече №{i} меняется, когда будущие свечи убирают из ряда: правило использует данные, "
                "которых в тот момент не было. Результат такого бэктеста недостижим в жизни.",
            )
    return _check("causality", "Правило не смотрит в будущее", "pass",
                  f"Решения совпали при усечении ряда в {len(cuts)} точках: будущие свечи на них не влияют.")


def check_data_order(df: pd.DataFrame) -> dict[str, str]:
    ts = df["ts"].to_numpy()
    if len(ts) > 1 and not np.all(np.diff(ts) > 0):
        return _check("data_order", "Свечи идут по порядку", "fail", "Время свечей не строго возрастает: дубликаты или перестановка.")
    o, h, low, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    arr = np.vstack([o, h, low, c])
    if not np.isfinite(arr).all() or (arr <= 0).any():
        return _check("data_order", "Свечи идут по порядку", "fail", "В ценах есть пропуски, нули или отрицательные значения.")
    if (h < np.maximum(o, c) - _TOL).any() or (low > np.minimum(o, c) + _TOL).any() or (h < low).any():
        return _check("data_order", "Свечи идут по порядку", "fail", "Максимум ниже открытия/закрытия или минимум выше: цены противоречат друг другу.")
    return _check("data_order", "Свечи идут по порядку", "pass", "Время растёт, цены целы и согласованы.")


def check_execution_timing(
    df: pd.DataFrame, target: pd.Series, bt: BacktestResult, slip_pct: float, fee_pct: float = 0.0
) -> dict[str, str]:
    """Вход по сигналу — по открытию свечи после сигнальной (с проскальзыванием); выход «по сигналу» — так же."""
    ts = df["ts"].to_numpy()
    index = {int(t): i for i, t in enumerate(ts)}
    opens = df["open"].to_numpy()
    tgt = target.to_numpy()
    slip = slip_pct / 100
    for t in bt.trades:
        i = index.get(t.entry_ts)
        if i is None or i == 0:
            return _check("execution_timing", "Исполнение после сигнала", "fail", "Вход совершён на свече, которой нет в ряде или на самой первой.")
        if tgt[i - 1] != 1:
            return _check(
                "execution_timing", "Исполнение после сигнала", "fail",
                f"Вход на свече №{i} без сигнала на предыдущей: сделка исполнена не по правилу «решение на закрытии — "
                "исполнение по следующему открытию».",
            )
        if not math.isclose(t.entry_price, opens[i] * (1 + slip), rel_tol=1e-9):
            return _check("execution_timing", "Исполнение после сигнала", "fail",
                          f"Цена входа на свече №{i} не равна открытию с проскальзыванием: исполнение по цене, которой не было.")
        if t.exit_ts is not None and t.exit_reason == "signal":
            j = index.get(t.exit_ts)
            if j is None or j == 0 or tgt[j - 1] != 0:
                return _check("execution_timing", "Исполнение после сигнала", "fail", "Выход по сигналу без сигнала на предыдущей свече.")
            if t.exit_price is None or not math.isclose(t.exit_price, opens[j] * (1 - slip), rel_tol=1e-9):
                return _check("execution_timing", "Исполнение после сигнала", "fail", "Цена выхода по сигналу не равна открытию свечи с проскальзыванием.")
    return _check("execution_timing", "Исполнение после сигнала", "pass",
                  f"Все {len(bt.trades)} входов и выходов по сигналу исполнены по открытию следующей свечи." if bt.trades
                  else "Сделок нет — проверять нечего.")


def check_no_overlap(bt: BacktestResult) -> dict[str, str]:
    prev_exit: int | None = None
    for k, t in enumerate(bt.trades):
        if t.qty <= 0:
            return _check("no_overlap", "Сделки не пересекаются", "fail", f"Сделка №{k + 1} с нулевым или отрицательным количеством.")
        if prev_exit is not None and t.entry_ts < prev_exit:
            return _check("no_overlap", "Сделки не пересекаются", "fail", f"Сделка №{k + 1} начата до выхода из предыдущей: позиций больше одной.")
        if t.exit_ts is not None and t.exit_ts < t.entry_ts:
            return _check("no_overlap", "Сделки не пересекаются", "fail", f"Сделка №{k + 1} закрыта раньше, чем открыта.")
        prev_exit = t.exit_ts
        if t.exit_ts is None and k != len(bt.trades) - 1:
            return _check("no_overlap", "Сделки не пересекаются", "fail", "Открытая сделка не последняя.")
    return _check("no_overlap", "Сделки не пересекаются", "pass", "Одна позиция за раз, без плеча и коротких продаж.")


def check_equity(
    bt: BacktestResult, df: pd.DataFrame, capital: float, rules: Rules | None, fee_pct: float = 0.0
) -> dict[str, str]:
    """Капитал на конец = начальный + результаты закрытых сделок + оценка открытой по последней цене закрытия
    (за вычетом уже уплаченной комиссии входа)."""
    uv = rules.unit_value if rules else 1.0
    final = bt.equity[-1][1]
    expected = capital + sum(t.pnl_amount for t in bt.trades if t.exit_ts is not None)
    for t in bt.trades:
        if t.exit_ts is None:
            expected += t.qty * float(df["close"].iloc[-1]) * uv - t.qty * t.entry_price * uv * (1 + fee_pct / 100)
    if not math.isfinite(final) or abs(final - expected) > max(1e-7 * capital, 1e-6):
        return _check("equity_consistency", "Капитал сходится со сделками", "fail",
                      f"Итоговый капитал {final:,.2f} не сходится с суммой сделок {expected:,.2f}.".replace(",", " "))
    return _check("equity_consistency", "Капитал сходится со сделками", "pass", "Итог равен начальному капиталу плюс результаты сделок.")


def check_ambiguity(bt: BacktestResult) -> dict[str, str]:
    n = int(bt.metrics.get("ambiguous_bars") or 0)
    if n:
        return _check("ambiguity", "Неоднозначные свечи", "info",
                      f"{n} свечей, где достигнуты и стоп, и цель: порядок внутри свечи неизвестен, принят стоп (худшее).")
    return _check("ambiguity", "Неоднозначные свечи", "pass", "Свечей, где нельзя понять порядок стопа и цели, не было.")


def integrity_report(
    strat: Strategy,
    params: dict[str, int],
    df: pd.DataFrame,
    target: pd.Series,
    bt: BacktestResult,
    capital: float,
    slip_pct: float,
    rules: Rules | None = None,
    fee_pct: float = 0.0,
) -> dict[str, Any]:
    """Сводка проверок. slip_pct — эффективное проскальзывание (с половиной спреда), как в бэктесте."""
    checks = [
        check_causality(strat, params, df),
        check_data_order(df),
        check_execution_timing(df, target, bt, slip_pct),
        check_no_overlap(bt),
        check_equity(bt, df, capital, rules, fee_pct),
        check_ambiguity(bt),
    ]
    failed = [c for c in checks if c["status"] == "fail"]
    return {
        "passed": not failed,
        "checks": checks,
        "summary": (
            "Проверки целостности пройдены: бэктест не подглядывал в будущее и исполнял сделки по правилам."
            if not failed
            else "Проверка целостности НЕ пройдена: " + " ".join(c["detail"] for c in failed)
        ),
    }
