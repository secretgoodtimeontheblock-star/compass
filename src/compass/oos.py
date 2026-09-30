"""Проверка правила на отдельном периоде (вне выборки).

История делится по времени на две подряд идущие части. Параметры подбираются ТОЛЬКО на первой
(период подбора) — по показателю «доходность к просадке» среди вариантов, набравших достаточно
сделок. Выбранный вариант один раз оценивается на второй части (проверочный период), которую подбор
не видел: сюда не попадают ни цены, ни результаты проверочного периода. Индикаторы считаются по всей
истории (они используют только прошлое), но торгуется проверочный период отдельно, с собственным
начальным капиталом.

Что это даёт и чего нет: показывает, сохранился ли результат на данных, которых подбор не видел.
Это не прогноз: другой рынок в будущем может вести себя иначе. Один проверочный период — тоже одна
выборка: чем больше вариантов перебрано и чем меньше сделок, тем меньше ей можно верить.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pandas as pd

from compass.backtest import BacktestResult, Rules, backtest
from compass.strategies import Strategy
from compass.validation import MIN_TRADES_FOR_STATS, apply_sample_rules, risk_ratios, trade_stats

MAX_COMBOS = 300
MAX_VALUES_PER_PARAM = 12
MIN_TRAIN_CANDLES = 100
MIN_TEST_CANDLES = 50
COST_MULTIPLIERS = (1, 2, 3)


def split_index(n: int, train_pct: float) -> int:
    if not 50 <= train_pct <= 90:
        raise ValueError("Доля периода подбора — от 50 до 90%")
    k = int(n * train_pct / 100)
    if k < MIN_TRAIN_CANDLES or n - k < MIN_TEST_CANDLES:
        raise ValueError(
            f"Мало истории для разделения: на подбор нужно хотя бы {MIN_TRAIN_CANDLES} свечей, "
            f"на проверку — {MIN_TEST_CANDLES} (сейчас {k} и {n - k}). Увеличьте глубину истории."
        )
    return k


def expand_grid(strat: Strategy, base: dict[str, int], grid: dict[str, list[int]]) -> tuple[list[dict[str, int]], int]:
    """Все допустимые сочетания параметров и число отброшенных (например, быстрая средняя не короче медленной)."""
    unknown = set(grid) - {p.name for p in strat.params}
    if unknown:
        raise ValueError(f"Неизвестные параметры: {', '.join(sorted(unknown))}")
    axes: list[list[tuple[str, int]]] = []
    for name, values in grid.items():
        uniq = sorted(set(values))
        if not uniq or len(uniq) > MAX_VALUES_PER_PARAM:
            raise ValueError(f"Для параметра {name} — от 1 до {MAX_VALUES_PER_PARAM} значений")
        axes.append([(name, v) for v in uniq])
    total = math.prod(len(a) for a in axes) if axes else 1
    if total > MAX_COMBOS:
        raise ValueError(f"Слишком много сочетаний ({total}): не больше {MAX_COMBOS}")
    combos: list[dict[str, int]] = []
    dropped = 0
    for combo in itertools.product(*axes) if axes else [()]:
        try:
            combos.append(strat.resolve({**base, **dict(combo)}))
        except ValueError:
            dropped += 1
    if not combos:
        raise ValueError("Ни одно сочетание параметров не допустимо")
    return combos, dropped


def score(metrics: dict[str, Any]) -> float | None:
    """Доходность к просадке. None — сделок слишком мало, чтобы вариант участвовал в выборе."""
    if int(metrics.get("trades") or 0) < MIN_TRADES_FOR_STATS:
        return None
    dd = abs(float(metrics["max_drawdown_pct"]))
    return round(float(metrics["total_return_pct"]) / max(dd, 1.0), 3)


def _metrics(bt: BacktestResult, ts: np.ndarray, capital: float) -> dict[str, Any]:
    """ts — время свечей именно этого периода: коэффициенты считаются по его длине, а не по всей истории."""
    eq = np.array([v for _, v in bt.equity])
    raw = {
        **bt.metrics,
        **risk_ratios(eq, ts, capital, bt.metrics["max_drawdown_pct"]),
        **trade_stats(bt.trades),
    }
    return apply_sample_rules(raw)[0]


def _run(df, target, k, capital, fee, slip, rules, *, part: str) -> BacktestResult:
    if part == "train":
        return backtest(df.iloc[:k].reset_index(drop=True), target.iloc[:k].reset_index(drop=True), capital, fee, slip, rules)
    return backtest(df, target, capital, fee, slip, rules, start=k)


def evaluate(strat: Strategy, params: dict[str, int], df: pd.DataFrame, k: int, capital: float, fee: float,
             slip: float, rules: Rules | None) -> dict[str, Any]:
    target = strat.target(df, params)
    train = _run(df, target, k, capital, fee, slip, rules, part="train")
    test = _run(df, target, k, capital, fee, slip, rules, part="test")
    ts = df["ts"].to_numpy()
    return {
        "train": _metrics(train, ts[:k], capital),
        "test": _metrics(test, ts[k:], capital),
        "_train_raw": train.metrics,
    }


def run_oos(
    strat: Strategy,
    base_params: dict[str, int],
    grid: dict[str, list[int]],
    df: pd.DataFrame,
    train_pct: float,
    capital: float,
    fee_pct: float,
    slippage_pct: float,
    rules: Rules | None,
) -> dict[str, Any]:
    k = split_index(len(df), train_pct)
    combos, dropped = expand_grid(strat, base_params, grid) if grid else ([strat.resolve(base_params)], 0)

    # --- подбор: только период подбора ---
    rows = []
    for p in combos:
        target = strat.target(df, p)
        train = _run(df, target, k, capital, fee_pct, slippage_pct, rules, part="train")
        m = _metrics(train, df["ts"].to_numpy()[:k], capital)
        rows.append({"params": p, "score": score(train.metrics), "train": m, "_raw": train.metrics})
    eligible = [r for r in rows if r["score"] is not None]
    optimized = bool(grid)
    if optimized:
        chosen_row = max(eligible, key=lambda r: r["score"]) if eligible else None  # первый из равных
    else:
        chosen_row = rows[0]
    ts = df["ts"]
    out: dict[str, Any] = {
        "split": {
            "train_pct": train_pct, "train_candles": k, "test_candles": len(df) - k,
            "train_from": int(ts.iloc[0]), "train_to": int(ts.iloc[k - 1]),
            "test_from": int(ts.iloc[k]), "test_to": int(ts.iloc[-1]),
        },
        "optimization": {
            "enabled": optimized, "objective": "доходность / max(просадка, 1%) на периоде подбора; "
            f"только варианты не меньше чем с {MIN_TRADES_FOR_STATS} сделок",
            "variants": len(combos), "dropped_invalid": dropped, "eligible": len(eligible),
            "top": [
                {"params": r["params"], "score": r["score"], "return_pct": r["train"]["total_return_pct"],
                 "trades": r["train"]["trades"]}
                for r in sorted(eligible, key=lambda r: -r["score"])[:10]
            ],
        },
        "chosen": None, "baseline": None, "sensitivity": [], "cost_sensitivity": [], "notes": [],
    }
    if chosen_row is None:
        out["verdict"] = {
            "status": "inconclusive",
            "reasons": [f"Ни один вариант не набрал {MIN_TRADES_FOR_STATS} сделок на периоде подбора: выбирать не из чего."],
        }
        return out

    chosen = chosen_row["params"]
    ev = evaluate(strat, chosen, df, k, capital, fee_pct, slippage_pct, rules)
    out["chosen"] = {"params": chosen, "train": ev["train"], "test": ev["test"], "train_score": chosen_row["score"]}

    if optimized:  # для сравнения: что дал бы вариант «как есть», без подбора
        default = strat.resolve(base_params)
        dev = evaluate(strat, default, df, k, capital, fee_pct, slippage_pct, rules)
        out["baseline"] = {"params": default, "train": dev["train"], "test": dev["test"]}
        # чувствительность: соседние значения каждого параметра сетки при остальных выбранных
        for name, values in grid.items():
            axis = sorted(set(values))
            if chosen[name] not in axis:
                continue
            idx = axis.index(chosen[name])
            for j in (idx - 1, idx + 1):
                if not 0 <= j < len(axis):
                    continue
                try:
                    p = strat.resolve({**chosen, name: axis[j]})
                except ValueError:
                    continue
                nev = evaluate(strat, p, df, k, capital, fee_pct, slippage_pct, rules)
                out["sensitivity"].append({
                    "param": name, "value": axis[j], "train_score": score(nev["_train_raw"]),
                    "train_return_pct": nev["train"]["total_return_pct"],
                    "test_return_pct": nev["test"]["total_return_pct"], "test_trades": nev["test"]["trades"],
                })

    # чувствительность к расходам: тот же выбранный вариант на проверочном периоде при худшем исполнении
    target = strat.target(df, chosen)
    for mult in COST_MULTIPLIERS:
        bt = backtest(df, target, capital, fee_pct * mult, slippage_pct * mult, rules, start=k)
        out["cost_sensitivity"].append({
            "multiplier": mult, "fee_pct": round(fee_pct * mult, 4), "slippage_pct": round(slippage_pct * mult, 4),
            "return_pct": bt.metrics["total_return_pct"], "trades": bt.metrics["trades"],
        })

    out["verdict"] = _verdict(ev["train"], ev["test"], chosen_row["score"])
    out["notes"] = [
        "Параметры подобраны только на периоде подбора; проверочный период в выборе не участвовал.",
        "Проверочный период торгуется отдельно с собственным капиталом; вход возможен не раньше второй его свечи.",
        "Один проверочный период — одна выборка: результат на нём не гарантирует будущее.",
    ]
    return out


def _verdict(train: dict[str, Any], test: dict[str, Any], train_score: float | None) -> dict[str, Any]:
    trades = int(test.get("trades") or 0)
    reasons = [
        (
            f"Проверочный период: доходность {test['total_return_pct']}% против {test['buy_hold_return_pct']}% "
            f"у «купил и держи», просадка {test['max_drawdown_pct']}%, сделок {trades}."
        ),
        f"Период подбора: доходность {train['total_return_pct']}%, просадка {train['max_drawdown_pct']}%.",
    ]
    if trades < MIN_TRADES_FOR_STATS:
        reasons.append(f"На проверке меньше {MIN_TRADES_FOR_STATS} сделок: вывод об устойчивости делать нельзя.")
        return {"status": "inconclusive", "reasons": reasons}
    pf = test.get("profit_factor")
    held = test["total_return_pct"] > 0 and (pf is None or pf > 1) and (test.get("avg_trade_pct") or 0) > 0
    reasons.append(
        "Результат на проверочном периоде остался в плюсе." if held
        else "Результат на проверочном периоде не сохранился: в плюсе не остался или прибыль держится не на большинстве сделок."
    )
    return {"status": "held" if held else "degraded", "reasons": reasons}
