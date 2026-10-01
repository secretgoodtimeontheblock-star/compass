"""Validation Lab: защита пользователя от самообмана при проверке правила.

Одного разбиения «подбор / проверка» (oos.py) недостаточно: один проверочный период — одна выборка.
Здесь — следующий слой, где каждая проверка отвечает на человеческий вопрос:

- walk-forward (скользящий или растущий): параметры подбираются на каждом обучающем окне и проверяются на
  следующем, не виденном подбору. Несколько независимых окон показывают, повторяется ли результат;
- карта устойчивости параметров: широкая область хороших значений — признак, одиночный пик — риск подгонки;
- ресэмплинг сделок (bootstrap): доверительный интервал средней сделки и шанс, что преимущества нет;
- концентрация прибыли: держится ли результат на нескольких удачных сделках;
- глубина, длительность и восстановление просадок, хвостовой риск (CVaR);
- чувствительность к режимам рынка (тренд вверх / вниз, низкая / высокая волатильность);
- стресс расходов: комиссия, проскальзывание и спред вместе ×2 и ×3;
- множественные проверки: PBO (вероятность переобучения подбора, CSCV) и Deflated Sharpe — поправка на то,
  сколько вариантов перебрано (в том числе в прошлых запусках).

Итог — вердикт простым языком; Sharpe, Sortino, доверительные интервалы и прочая статистика лежат в
раскрываемом «слое исследователя». Всё это описательная статистика прошлого: она может показать, что
результат ненадёжен, но не доказывает, что он будет работать.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd

from compass import glossary
from compass.backtest import BacktestResult, Rules, Trade, backtest
from compass.indicators import atr, sma
from compass.oos import MIN_TRAIN_CANDLES, expand_grid, score
from compass.strategies import Strategy
from compass.validation import MIN_TRADES_FOR_STATS, _clean, risk_ratios, trade_stats

MIN_FOLDS, MAX_FOLDS = 3, 8
MIN_WINDOW = 40  # свечей в проверочном окне: меньше — окно почти ничего не покажет
MAX_MAP_AXES = 2
PBO_CHUNKS = 8  # частей истории для CSCV (C(8,4)=70 разбиений)
PBO_MIN_VARIANTS = 3
PBO_MIN_BARS = PBO_CHUNKS * 10
_EULER = 0.5772156649015329
_N = NormalDist()

# Пороги вердикта (описательные, не «научные»: они объяснены в тексте находок)
SURVIVE_SHARE = 0.6  # доля прибыльных окон, при которой результат считается повторившимся
UNSTABLE_SHARE = 0.5  # меньше — нестабильно
CONCENTRATION_SHARE = 60.0  # % прибыли от трёх лучших сделок
PBO_BAD = 0.5
DSR_OK = 0.95
REGIME_DOMINANCE = 80.0  # % прибыли из одного режима
MIN_REGIME_BARS = 30


# --- walk-forward ---


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    train_start: int
    train_end: int  # не включая; для режима без подбора train_start == train_end == test_start
    test_start: int
    test_end: int  # не включая


def make_folds(n: int, k: int, optimize: bool, mode: str) -> list[Fold]:
    """Окна проверки. С подбором: k подряд идущих окон в последней половине истории, обучение — на всём,
    что раньше (растущее) или на окне фиксированной длины (скользящее). Без подбора правило фиксировано,
    поэтому окна делят всю историю."""
    if not MIN_FOLDS <= k <= MAX_FOLDS:
        raise ValueError(f"Число окон — от {MIN_FOLDS} до {MAX_FOLDS}")
    if mode not in ("rolling", "anchored"):
        raise ValueError("Режим: rolling (скользящее) или anchored (растущее обучение)")
    if not optimize:
        w = n // k
        if w < MIN_WINDOW:
            raise ValueError(f"Мало истории: на {k} окон нужно хотя бы {MIN_WINDOW * k} свечей (есть {n}).")
        return [Fold(j, j * w, j * w, j * w, (j + 1) * w if j < k - 1 else n) for j in range(k)]
    w = n // (2 * k)
    train0 = n - k * w
    if w < MIN_WINDOW or train0 < MIN_TRAIN_CANDLES:
        raise ValueError(
            f"Мало истории для {k} окон с подбором: нужно окно проверки не меньше {MIN_WINDOW} свечей и обучение не меньше "
            f"{MIN_TRAIN_CANDLES} (сейчас {w} и {train0}). Уменьшите число окон или увеличьте глубину истории."
        )
    return [
        Fold(j, 0 if mode == "anchored" else j * w, train0 + j * w, train0 + j * w, train0 + (j + 1) * w)
        for j in range(k)
    ]


def _growth(ret_pct: float) -> float:
    """Логарифмический рост: доходности разной длины и величины сравнимы, а составная доходность не «взрывает» пороги."""
    return math.log(max(1 + ret_pct / 100, 1e-9))


def _bh(df: pd.DataFrame, a: int, b: int) -> float | None:
    """Купил и держи на окне [a, b): от закрытия перед окном (или первого закрытия) до последнего."""
    c = df["close"].to_numpy()
    base = c[a - 1] if a > 0 else c[0]
    return _clean((c[b - 1] / base - 1) * 100)


def run_fold(
    strat: Strategy, base: dict[str, int], combos: list[dict[str, int]] | None, df: pd.DataFrame, fold: Fold,
    capital: float, fee: float, slip: float, rules: Rules | None, extra: dict[str, Any],
) -> dict[str, Any]:
    """Один шаг walk-forward. Подбор видит только df[:train_end]; проверка торгует окно [test_start, test_end)
    на ряде, обрезанном по test_end, — будущего после окна в расчёте нет."""
    chosen: dict[str, int] | None = base
    train_score: float | None = None
    train_ret: float | None = None
    if combos is not None:
        sl = df.iloc[: fold.train_end].reset_index(drop=True)
        best: tuple[float, dict[str, int], float] | None = None
        for p in combos:
            bt = backtest(sl, strat.target(sl, p), capital, fee, slip, rules, start=fold.train_start, **extra)
            sc = score(bt.metrics)
            if sc is not None and (best is None or sc > best[0]):
                best = (sc, p, bt.metrics["total_return_pct"])
        if best is None:
            chosen = None
        else:
            train_score, chosen, train_ret = best[0], best[1], best[2]
    ts = df["ts"].to_numpy()
    row: dict[str, Any] = {
        "index": fold.index,
        "train_from": int(ts[fold.train_start]) if combos is not None else None,
        "train_to": int(ts[fold.train_end - 1]) if combos is not None else None,
        "test_from": int(ts[fold.test_start]), "test_to": int(ts[fold.test_end - 1]),
        "params": chosen, "train_score": train_score, "train_return_pct": train_ret,
        "train_candles": fold.train_end - fold.train_start if combos is not None else 0,
        "test_candles": fold.test_end - fold.test_start,
        "buy_hold_pct": _bh(df, fold.test_start, fold.test_end),
    }
    if chosen is None:
        row.update(status="no_choice", test_return_pct=0.0, test_trades=0, test_max_drawdown_pct=0.0)
        return {"row": row, "bt": None, "chosen": None}
    bt = trade_window(strat, chosen, df, fold, capital, fee, slip, rules, extra)
    row.update(
        status="ok", test_return_pct=bt.metrics["total_return_pct"], test_trades=bt.metrics["trades"],
        test_max_drawdown_pct=bt.metrics["max_drawdown_pct"],
    )
    return {"row": row, "bt": bt, "chosen": chosen}


def trade_window(
    strat: Strategy, params: dict[str, int], df: pd.DataFrame, fold: Fold, capital: float, fee: float, slip: float,
    rules: Rules | None, extra: dict[str, Any],
) -> BacktestResult:
    sl = df.iloc[: fold.test_end].reset_index(drop=True)
    return backtest(sl, strat.target(sl, params), capital, fee, slip, rules, start=fold.test_start, **extra)



def stitch(results: list[dict[str, Any]], capital: float) -> tuple[np.ndarray, np.ndarray, list[Trade]]:
    """Сшивает проверочные окна подряд: капитал каждого окна нормируется на достигнутый уровень предыдущих."""
    ts_all: list[np.ndarray] = []
    eq_all: list[np.ndarray] = []
    trades: list[Trade] = []
    level = 1.0
    for r in results:
        bt: BacktestResult | None = r["bt"]
        if bt is None or not bt.equity:
            continue
        ts_all.append(np.array([t for t, _ in bt.equity], dtype=np.int64))
        arr = np.array([v for _, v in bt.equity]) / capital * level
        eq_all.append(arr)
        level = float(arr[-1])
        trades.extend(bt.trades)
    if not eq_all:
        return np.array([], dtype=np.int64), np.array([]), []
    return np.concatenate(ts_all), np.concatenate(eq_all), trades


def walk_forward_lab(
    strat: Strategy, base: dict[str, int], grid: dict[str, list[int]], df: pd.DataFrame, k: int, mode: str,
    capital: float, fee: float, slip: float, rules: Rules | None, extra: dict[str, Any],
) -> dict[str, Any]:
    optimize = bool(grid)
    n = len(df)
    folds = make_folds(n, k, optimize, mode)
    combos = expand_grid(strat, base, grid)[0] if optimize else None
    results = [run_fold(strat, base, combos, df, f, capital, fee, slip, rules, extra) for f in folds]
    rows = [r["row"] for r in results]
    ok = [r for r in rows if r["status"] == "ok"]
    ts, eq, trades = stitch(results, capital)
    rets = [r["test_return_pct"] for r in ok]
    stitched_ret = _clean((eq[-1] - 1) * 100) if len(eq) else None
    chosen_keys = [tuple(sorted(r["params"].items())) for r in ok]
    top_share = max((chosen_keys.count(c) for c in set(chosen_keys)), default=0) / len(chosen_keys) if chosen_keys else None
    # «эффективность»: какая доля скорости роста на подборе (log-рост за свечу) сохранилась на проверке
    train_g = [_growth(r["train_return_pct"]) / r["train_candles"] for r in ok if r["train_return_pct"] is not None and r["train_candles"]]
    test_g = [_growth(r["test_return_pct"]) / r["test_candles"] for r in ok if r["test_candles"]]
    summary = {
        "mode": mode if optimize else "fixed",
        "optimized": optimize, "folds": len(rows), "usable_folds": len(ok),
        "profitable_folds": sum(1 for x in rets if x > 0),
        "stitched_return_pct": stitched_ret,
        "mean_return_pct": _clean(float(np.mean(rets))) if rets else None,
        "median_return_pct": _clean(float(np.median(rets))) if rets else None,
        "worst_fold_pct": _clean(min(rets)) if rets else None,
        "return_std_pct": _clean(float(np.std(rets))) if rets else None,
        "trades": sum(r["test_trades"] for r in ok),
        "param_consistency": _clean(top_share, 2),
        "efficiency": _clean(float(np.mean(test_g)) / float(np.mean(train_g)))
        if optimize and test_g and train_g and np.mean(train_g) > 0 else None,
    }
    return {"summary": summary, "folds": rows, "_ts": ts, "_equity": eq, "_trades": trades, "_results": results,
            "_fold_defs": folds}


def replay_with_costs(
    strat: Strategy, wf: dict[str, Any], df: pd.DataFrame, capital: float, fee: float, slip: float,
    rules: Rules | None, extra: dict[str, Any], mults: tuple[float, ...] = (1, 2, 3),
) -> list[dict[str, Any]]:
    """Те же выбранные параметры на тех же проверочных окнах при расходах ×1/×2/×3 (комиссия, проскальзывание, спред).
    Подбор заново не делается: показывается, что станет с уже найденным вариантом, если исполнение хуже."""
    out = []
    for m in mults:
        xkw = {**extra, "spread_pct": extra.get("spread_pct", 0.0) * m}
        level, trades = 1.0, 0
        for res, fold in zip(wf["_results"], wf["_fold_defs"], strict=True):
            if res["chosen"] is None:
                continue
            bt = trade_window(strat, res["chosen"], df, fold, capital, fee * m, slip * m, rules, xkw)
            level *= bt.equity[-1][1] / capital
            trades += int(bt.metrics["trades"] or 0)
        out.append({"multiplier": m, "fee_pct": _clean(fee * m, 4), "slippage_pct": _clean(slip * m, 4),
                    "spread_pct": _clean(xkw["spread_pct"], 4), "return_pct": _clean((level - 1) * 100), "trades": trades})
    return out


# --- карта устойчивости параметров и данные для PBO/DSR ---


def bar_returns(equity: np.ndarray, capital: float) -> np.ndarray:
    prev = np.concatenate(([capital], equity[:-1]))
    return equity / prev - 1


def parameter_map(
    strat: Strategy, base: dict[str, int], grid: dict[str, list[int]], df: pd.DataFrame, capital: float, fee: float,
    slip: float, rules: Rules | None, extra: dict[str, Any],
) -> dict[str, Any]:
    """Все варианты сетки на всей истории: матрица результатов, а не только победитель. Это описание, а не выбор:
    по нему нельзя подбирать параметры (подбор был бы на тех же данных, что и проверка)."""
    if not grid:
        return {"available": False, "reason": "Сетка параметров не задана: карту строить не из чего.", "_returns": None}
    if len(grid) > MAX_MAP_AXES:
        raise ValueError(f"Лаборатория принимает не больше {MAX_MAP_AXES} параметров в сетке: так карта остаётся читаемой.")
    expand_grid(strat, base, grid)  # проверка границ и общего числа сочетаний
    names = list(grid)
    axes = [sorted(set(grid[x])) for x in names]
    cells: dict[tuple, dict[str, Any]] = {}
    rets: list[np.ndarray] = []
    keys: list[tuple] = []
    for combo in itertools.product(*axes):
        try:
            p = strat.resolve({**base, **dict(zip(names, combo, strict=True))})
        except ValueError:
            cells[combo] = {"valid": False}
            continue
        bt = backtest(df, strat.target(df, p), capital, fee, slip, rules, **extra)
        m = bt.metrics
        cells[combo] = {
            "valid": True, "return_pct": m["total_return_pct"], "max_drawdown_pct": m["max_drawdown_pct"],
            "trades": m["trades"], "score": score(m), "thin": int(m["trades"] or 0) < MIN_TRADES_FOR_STATS,
        }
        rets.append(bar_returns(np.array([v for _, v in bt.equity]), capital))
        keys.append(combo)
    valid = {c: v for c, v in cells.items() if v["valid"]}
    eligible = {c: v for c, v in valid.items() if v["score"] is not None}
    out: dict[str, Any] = {
        "available": True, "params": names, "axes": axes,
        "matrix": [[cells[c] for c in itertools.product(*axes) if c[0] == a0] for a0 in axes[0]],
        "variants": len(valid), "eligible": len(eligible),
        "positive_share_pct": _clean(sum(1 for v in valid.values() if v["return_pct"] > 0) / len(valid) * 100, 1) if valid else None,
        "_returns": np.vstack(rets) if rets else None, "_keys": keys,
    }
    out["stability"] = _stability(axes, cells, eligible)
    return out


def _neighbors(axes: list[list[int]], combo: tuple) -> list[tuple]:
    out = []
    for i, a in enumerate(axes):
        j = a.index(combo[i])
        for d in (-1, 1):
            if 0 <= j + d < len(a):
                out.append(combo[:i] + (a[j + d],) + combo[i + 1:])
    return out


def _stability(axes: list[list[int]], cells: dict, eligible: dict) -> dict[str, Any]:
    """Лучшая ячейка карты и её соседи: если соседи заметно хуже или в минусе — пик одиночный."""
    if len(eligible) < 3:
        return {"verdict": "inconclusive", "text": "Слишком мало вариантов с достаточным числом сделок: устойчивость не оценить."}
    best = max(eligible, key=lambda c: eligible[c]["score"])
    nb = [cells[c] for c in _neighbors(axes, best) if cells.get(c, {}).get("valid")]
    if not nb:
        return {"verdict": "inconclusive", "best": list(best), "text": "У лучшего варианта нет соседей на карте."}
    nb_ret = [c["return_pct"] for c in nb]
    best_ret = eligible[best]["return_pct"]
    # сравниваем скорость роста (логарифм), а не сырую составную доходность: иначе пороги зависят от длины истории
    worse = [r for r in nb_ret if r <= 0 or (best_ret > 0 and _growth(r) < _growth(best_ret) * 0.5)]
    frac_bad = len(worse) / len(nb)
    positive = sum(1 for v in eligible.values() if v["return_pct"] > 0) / len(eligible)
    if best_ret <= 0:
        verdict, text = "no_edge", "Даже лучший вариант на карте не в плюсе: подбор не нашёл преимущества."
    elif frac_bad >= 0.5:
        verdict, text = "fragile", "Соседние значения параметров заметно хуже или убыточны: результат держится на одной точке, это похоже на подгонку."
    elif positive < 0.5:
        verdict, text = "narrow", "Хорошие значения занимают меньшую часть карты: область устойчивости узкая."
    else:
        verdict, text = "stable", "Рядом с лучшим вариантом результат сопоставим, и большая часть карты в плюсе: область устойчивая."
    return {"verdict": verdict, "best": list(best), "best_return_pct": best_ret, "neighbor_returns_pct": nb_ret,
            "positive_share_pct": _clean(positive * 100, 1), "text": text}


# --- множественные проверки: PBO и Deflated Sharpe ---


def _sharpe_cols(m: np.ndarray) -> np.ndarray:
    sd = m.std(axis=0, ddof=1)
    return np.where(sd > 1e-12, m.mean(axis=0) / np.where(sd > 1e-12, sd, 1.0), 0.0)


def pbo(returns: np.ndarray | None) -> dict[str, Any]:
    """Вероятность переобучения подбора (CSCV, Bailey–Borwein–López de Prado–Zhu): историю делят на 8 частей,
    на каждых 4 выбирают лучший по Sharpe вариант и смотрят его место на остальных 4. Если лучший «внутри» часто
    оказывается ниже медианы «снаружи» — подбор выбирает шум. returns: строки — варианты, столбцы — свечи."""
    if returns is None or returns.shape[0] < PBO_MIN_VARIANTS or returns.shape[1] < PBO_MIN_BARS:
        return {"available": False, "value": None,
                "reason": f"Нужно не меньше {PBO_MIN_VARIANTS} вариантов и {PBO_MIN_BARS} свечей."}
    n_var, t = returns.shape
    size = t // PBO_CHUNKS
    chunks = [returns[:, i * size:(i + 1) * size].T for i in range(PBO_CHUNKS)]  # каждая: свечи × варианты
    logits: list[float] = []
    for is_idx in itertools.combinations(range(PBO_CHUNKS), PBO_CHUNKS // 2):
        oos_idx = [i for i in range(PBO_CHUNKS) if i not in is_idx]
        is_m = np.vstack([chunks[i] for i in is_idx])
        oos_m = np.vstack([chunks[i] for i in oos_idx])
        best = int(np.argmax(_sharpe_cols(is_m)))
        sh = _sharpe_cols(oos_m)
        rank = (sh < sh[best]).sum() + 0.5 * ((sh == sh[best]).sum() - 1)  # среднее место при равенстве
        w = (rank + 1) / (n_var + 1)
        logits.append(math.log(w / (1 - w)))
    val = sum(1 for x in logits if x <= 0) / len(logits)
    return {"available": True, "value": _clean(val, 3), "splits": len(logits), "variants": n_var}


def deflated_sharpe(
    r: np.ndarray, n_trials: int, trial_sharpes: np.ndarray | None
) -> dict[str, Any]:
    """Вероятность того, что истинный Sharpe выше нуля, с поправкой на асимметрию, «толстые хвосты» и число
    перебранных вариантов (Bailey & López de Prado, 2014). Sharpe здесь по свечам, без годовой нормировки.
    n_trials=1 — обычный PSR (поправки на перебор нет)."""
    t = len(r)
    sd = float(r.std(ddof=1)) if t > 2 else 0.0
    if t < 30 or sd < 1e-12:
        return {"available": False, "value": None, "reason": "Слишком мало свечей или нулевая изменчивость результата."}
    sr = float(r.mean() / sd)
    z = (r - r.mean()) / sd
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))
    denom2 = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom2 <= 0:
        return {"available": False, "value": None, "reason": "Распределение результатов вырождено."}
    var_sr = float(np.var(trial_sharpes, ddof=1)) if trial_sharpes is not None and len(trial_sharpes) >= 3 else 1 / t
    n = max(1, int(n_trials))
    sr0 = 0.0 if n == 1 else math.sqrt(var_sr) * ((1 - _EULER) * _N.inv_cdf(1 - 1 / n) + _EULER * _N.inv_cdf(1 - 1 / (n * math.e)))
    stat = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom2)
    return {"available": True, "value": _clean(_N.cdf(stat), 3), "sharpe_per_bar": _clean(sr, 4),
            "benchmark_sharpe_per_bar": _clean(sr0, 4), "trials": n, "bars": t}


# --- распределение результата: bootstrap, концентрация, просадки, хвосты, режимы ---


def bootstrap_ci(trades: list[Trade], n_sim: int = 2000, seed: int = 42) -> dict[str, Any] | None:
    pnl = np.array([t.pnl_pct for t in trades if t.exit_ts is not None])
    if len(pnl) < 5:
        return None
    rng = np.random.default_rng(seed)
    boot = rng.choice(pnl, size=(n_sim, len(pnl)), replace=True)
    means = boot.mean(axis=1) * 100
    gains = np.where(boot > 0, boot, 0.0).sum(axis=1)
    losses = np.where(boot <= 0, -boot, 0.0).sum(axis=1)
    pf = np.where(losses > 1e-12, gains / np.where(losses > 1e-12, losses, 1.0), np.nan)
    pf = pf[np.isfinite(pf)]
    return {
        "trades": len(pnl), "simulations": n_sim,
        "mean_trade_pct": _clean(float(pnl.mean() * 100)),
        "mean_p5_pct": _clean(float(np.percentile(means, 5))), "mean_p95_pct": _clean(float(np.percentile(means, 95))),
        "prob_no_edge_pct": _clean(float((means <= 0).mean() * 100), 1),  # доля пересборок, где средняя сделка ≤ 0
        "profit_factor_p5": _clean(float(np.percentile(pf, 5))) if len(pf) else None,
        "profit_factor_p95": _clean(float(np.percentile(pf, 95))) if len(pf) else None,
    }


def concentration(trades: list[Trade]) -> dict[str, Any] | None:
    pnl = np.array([t.pnl_pct for t in trades if t.exit_ts is not None])
    if len(pnl) < 5:
        return None
    order = np.sort(pnl)[::-1]
    gains = float(order[order > 0].sum())
    total = float(np.prod(1 + pnl) - 1) * 100
    without1 = float(np.prod(1 + order[1:]) - 1) * 100
    without3 = float(np.prod(1 + order[3:]) - 1) * 100 if len(order) > 3 else None
    return {
        "trades": len(pnl), "total_return_pct": _clean(total),
        "top3_profit_share_pct": _clean(float(order[:3][order[:3] > 0].sum() / gains * 100), 1) if gains > 0 else None,
        "return_without_best_pct": _clean(without1),
        "return_without_best3_pct": _clean(without3) if without3 is not None else None,
        "best_trade_pct": _clean(float(order[0] * 100)),
    }


def drawdown_profile(ts: np.ndarray, equity: np.ndarray) -> dict[str, Any] | None:
    """Глубина, длительность и восстановление просадки. equity нормирован так, что старт = 1."""
    if len(equity) < 20:
        return None
    eq = np.concatenate(([1.0], equity))
    tt = np.concatenate(([ts[0] - (ts[1] - ts[0])], ts))
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    trough = int(np.argmin(dd))
    peak_idx = int(np.argmax(eq[: trough + 1])) if trough > 0 else 0
    rec = next((i for i in range(trough, len(eq)) if eq[i] >= peak[trough] - 1e-12), None)
    under, longest, run_start, longest_span = False, 0, 0, (0, 0)
    for i, v in enumerate(dd):
        if v < -1e-12:
            if not under:
                under, run_start = True, i
            if i - run_start + 1 > longest:
                longest, longest_span = i - run_start + 1, (run_start, i)
        else:
            under = False
    day = 86_400_000
    return {
        "max_drawdown_pct": _clean(float(dd[trough]) * 100),
        "peak_to_trough_bars": trough - peak_idx, "peak_to_trough_days": _clean((tt[trough] - tt[peak_idx]) / day, 1),
        "recovered": rec is not None,
        "recovery_bars": None if rec is None else rec - trough,
        "recovery_days": None if rec is None else _clean((tt[rec] - tt[trough]) / day, 1),
        "longest_underwater_bars": longest,
        "longest_underwater_days": _clean((tt[longest_span[1]] - tt[longest_span[0]]) / day, 1) if longest else 0.0,
        "current_drawdown_pct": _clean(float(dd[-1]) * 100),
    }


def tail_risk(equity: np.ndarray, trades: list[Trade]) -> dict[str, Any] | None:
    """Худшие 5% свечей и сделок. CVaR — средняя потеря в этих худших случаях, а не только их порог."""
    if len(equity) < 40:
        return None
    r = bar_returns(equity, 1.0)
    var = float(np.percentile(r, 5))
    tail = r[r <= var]
    out: dict[str, Any] = {
        "bars": len(r), "var95_bar_pct": _clean(var * 100, 3),
        "cvar95_bar_pct": _clean(float(tail.mean()) * 100, 3) if len(tail) else None,
        "worst_bar_pct": _clean(float(r.min()) * 100, 3),
    }
    pnl = np.array([t.pnl_pct for t in trades if t.exit_ts is not None])
    if len(pnl) >= 20:
        v = float(np.percentile(pnl, 5))
        out.update(var95_trade_pct=_clean(v * 100), cvar95_trade_pct=_clean(float(pnl[pnl <= v].mean()) * 100))
    return out


def regime_split(df: pd.DataFrame, ts: np.ndarray, equity: np.ndarray) -> dict[str, Any] | None:
    """Результат по режимам рынка: тренд (цена выше/ниже SMA100) и волатильность (ATR% — терции по всей истории).
    Режимы определены по ценам на каждой свече только из прошлого; границы терций — по всей истории (описание)."""
    if len(equity) < MIN_REGIME_BARS * 2:
        return None
    index = {int(t): i for i, t in enumerate(df["ts"].to_numpy())}
    rows = np.array([index.get(int(t), -1) for t in ts])
    if (rows < 0).any():
        return None
    close = df["close"].to_numpy()
    ma = sma(df["close"], 100).to_numpy()
    has_trend = ~np.isnan(ma)
    trend_up = has_trend & (close > ma)
    vol = (atr(df, 14) / df["close"]).to_numpy()
    valid_vol = ~np.isnan(vol)
    lo, hi = (np.percentile(vol[valid_vol], [33.3, 66.7]) if valid_vol.sum() >= 30 else (None, None))
    strat_r = bar_returns(equity, 1.0)
    prev_close = np.concatenate(([close[0]], close[:-1]))
    bh_r = close / prev_close - 1
    labels: dict[str, np.ndarray] = {}
    sel = rows
    labels["trend_up"] = has_trend[sel] & trend_up[sel]
    labels["trend_down"] = has_trend[sel] & ~trend_up[sel]
    if lo is not None:
        labels["vol_low"] = valid_vol[sel] & (vol[sel] <= lo)
        labels["vol_mid"] = valid_vol[sel] & (vol[sel] > lo) & (vol[sel] <= hi)
        labels["vol_high"] = valid_vol[sel] & (vol[sel] > hi)
    names = {"trend_up": "тренд вверх (цена выше SMA100)", "trend_down": "тренд вниз (цена ниже SMA100)",
             "vol_low": "низкая волатильность", "vol_mid": "средняя волатильность", "vol_high": "высокая волатильность"}
    regimes = []
    for key, mask in labels.items():
        bars = int(mask.sum())
        if bars < MIN_REGIME_BARS:
            regimes.append({"id": key, "label": names[key], "bars": bars, "return_pct": None, "buy_hold_pct": None, "thin": True})
            continue
        regimes.append({
            "id": key, "label": names[key], "bars": bars, "thin": False,
            "return_pct": _clean((float(np.prod(1 + strat_r[mask])) - 1) * 100),
            "buy_hold_pct": _clean((float(np.prod(1 + bh_r[sel][mask])) - 1) * 100),
        })
    good = [x for x in regimes if not x["thin"]]
    if not good:
        return None
    return {"regimes": regimes, "dominant": _dominant_regime(good)}


def _dominant_regime(regimes: list[dict[str, Any]]) -> str | None:
    """Режим, из которого приходит почти вся прибыль: сравниваем внутри каждой группы (тренд, волатильность)."""
    for prefix in ("trend", "vol"):
        grp = [r for r in regimes if r["id"].startswith(prefix)]
        gains = [max(r["return_pct"], 0.0) for r in grp]
        total = sum(gains)
        if total > 0 and len(grp) > 1:
            top = int(np.argmax(gains))
            if gains[top] / total * 100 >= REGIME_DOMINANCE:
                return grp[top]["id"]
    return None


# --- вердикт простым языком ---


def _finding(code: str, severity: str, title: str, text: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "title": title, "text": text}


def build_verdict(
    wf: dict[str, Any], pmap: dict[str, Any], robust: dict[str, Any], multi: dict[str, Any], stress: list[dict[str, Any]],
    candles: int,
) -> dict[str, Any]:
    s = wf["summary"]
    f: list[dict[str, str]] = []
    trades = int(s["trades"] or 0)
    enough = trades >= MIN_TRADES_FOR_STATS and s["usable_folds"] >= max(2, s["folds"] // 2)
    if not enough:
        no_choice = sum(1 for r in wf["folds"] if r["status"] == "no_choice")
        why = (
            f" В {no_choice} окнах ни один вариант не набрал {MIN_TRADES_FOR_STATS} сделок на обучающем периоде, "
            "поэтому выбирать было не из чего."
            if no_choice else ""
        )
        f.append(_finding(
            "not_enough_data", "bad", "Недостаточно данных",
            f"На проверочных окнах всего {trades} сделок (нужно хотя бы {MIN_TRADES_FOR_STATS}) и пригодных окон "
            f"{s['usable_folds']} из {s['folds']}: сделать вывод об устойчивости нельзя.{why} "
            "Увеличьте глубину истории или возьмите более частый таймфрейм.",
        ))
    else:
        share = s["profitable_folds"] / s["usable_folds"]
        if share >= SURVIVE_SHARE and (s["stitched_return_pct"] or 0) > 0:
            f.append(_finding(
                "survived_unseen", "good", "Результат пережил непросмотренные данные",
                f"В {s['profitable_folds']} из {s['usable_folds']} независимых проверочных окон правило осталось в плюсе, "
                f"суммарно {s['stitched_return_pct']:+.1f}%. Параметры в них подбирались только на более ранних данных.",
            ))
        elif share < UNSTABLE_SHARE or (s["stitched_return_pct"] or 0) <= 0:
            f.append(_finding(
                "unstable", "bad", "Результат нестабилен",
                f"Только {s['profitable_folds']} из {s['usable_folds']} проверочных окон в плюсе, суммарно "
                f"{(s['stitched_return_pct'] or 0):+.1f}%: то, что выглядело хорошо на подборе, не повторяется на новых данных.",
            ))
        else:
            f.append(_finding("mixed_windows", "warn", "Результат повторяется не всегда",
                              f"В плюсе {s['profitable_folds']} из {s['usable_folds']} окон: преимущество есть не в любой период."))
    st = pmap.get("stability") if pmap.get("available") else None
    if st and st["verdict"] in ("fragile", "no_edge"):
        f.append(_finding("params_fragile", "bad", "Малые изменения параметров разрушают результат", st["text"]))
    elif st and st["verdict"] == "narrow":
        f.append(_finding("params_narrow", "warn", "Область устойчивых параметров узкая", st["text"]))
    elif st and st["verdict"] == "stable":
        f.append(_finding("params_stable", "good", "Параметры устойчивы", st["text"]))
    base, worst = (stress[0], stress[-1]) if stress else (None, None)
    if base and worst and (base["return_pct"] or 0) > 0 >= (worst["return_pct"] or 0):
        f.append(_finding(
            "costs_kill", "bad", "Повышенные расходы убирают преимущество",
            f"При обычных расходах результат {base['return_pct']:+.1f}%, при втрое больших — {worst['return_pct']:+.1f}%. "
            "Преимущество держится на хорошем исполнении, а спред и очередь в свечах не видны.",
        ))
    c = robust.get("concentration")
    if c and c["top3_profit_share_pct"] is not None and (
        c["top3_profit_share_pct"] >= CONCENTRATION_SHARE or (c["return_without_best_pct"] or 0) <= 0 < (c["total_return_pct"] or 0)
    ):
        f.append(_finding(
            "concentrated", "bad" if (c["return_without_best_pct"] or 0) <= 0 else "warn",
            "Результат может определяться несколькими сделками",
            f"Три лучшие сделки дают {c['top3_profit_share_pct']:g}% всей прибыли; без самой удачной результат "
            f"{c['return_without_best_pct']:+.1f}%. Если эти сделки были случайностью, преимущества нет.",
        ))
    b = robust.get("bootstrap")
    if b and b["prob_no_edge_pct"] is not None and b["prob_no_edge_pct"] >= 25 and (b["mean_trade_pct"] or 0) > 0:
        f.append(_finding(
            "edge_uncertain", "warn", "Преимущество может быть случайным",
            f"При пересборке тех же сделок средняя сделка оказывается не в плюсе в {b['prob_no_edge_pct']:g}% случаев "
            f"(интервал {b['mean_p5_pct']:+.2f}…{b['mean_p95_pct']:+.2f}% на сделку).",
        ))
    p = multi.get("pbo", {})
    if p.get("available") and p["value"] is not None and p["value"] >= PBO_BAD:
        f.append(_finding(
            "overfit_selection", "bad", "Подбор, вероятно, переобучен",
            f"Вероятность переобучения {p['value']:.0%}: лучший вариант на одной части истории часто оказывается среднего "
            "качества или хуже на другой. Подбор выбирает шум.",
        ))
    d = multi.get("dsr", {})
    if d.get("available") and d["value"] is not None and d["value"] < DSR_OK and d["trials"] > 1:
        f.append(_finding(
            "multiple_testing", "warn", "С поправкой на число проб преимущество не доказано",
            f"Перебрано вариантов: {d['trials']} (с прошлыми запусками). Вероятность, что настоящий Sharpe выше нуля с "
            f"поправкой на перебор, — {d['value']:.0%}; для уверенности нужно ≥ {DSR_OK:.0%}.",
        ))
    rg = robust.get("regimes")
    if rg and rg.get("dominant"):
        label = next(x["label"] for x in rg["regimes"] if x["id"] == rg["dominant"])
        f.append(_finding("regime_dependent", "warn", "Результат зависит от режима рынка",
                          f"Почти вся прибыль получена в режиме «{label}». Если рынок сменит режим, результат может пропасть."))
    dd = robust.get("drawdown")
    if dd and not dd["recovered"]:
        f.append(_finding("drawdown_open", "warn", "Просадка ещё не восстановлена",
                          f"Максимальная просадка {dd['max_drawdown_pct']:g}% пока не отыграна; самая долгая — {dd['longest_underwater_days']:g} дн. под водой."))
    bad = [x for x in f if x["severity"] == "bad"]
    if not enough:
        status, head = "insufficient", "Недостаточно данных для вывода"
    elif bad:
        status, head = "fragile", "Результат ненадёжен: " + "; ".join(x["title"].lower() for x in bad)
    elif any(x["code"] == "survived_unseen" for x in f) and sum(1 for x in f if x["severity"] == "warn") <= 1:
        status, head = "robust", "Результат пережил проверки на непросмотренных данных"
    else:
        status, head = "mixed", "Результат неоднозначный: есть и устойчивые, и слабые стороны"
    return {"status": status, "headline": head, "findings": glossary.attach(f), "candles": candles,
            "disclaimer": "Это описание прошлого. Устойчивость на истории не гарантирует прибыль в будущем."}


# --- сборка ---


def run_lab(
    strat: Strategy, base: dict[str, int], grid: dict[str, list[int]], df: pd.DataFrame, *, folds: int, mode: str,
    capital: float, fee_pct: float, slippage_pct: float, spread_pct: float, rules: Rules | None,
    max_participation_pct: float | None, prior_variants: int,
) -> dict[str, Any]:
    extra = {"spread_pct": spread_pct, "max_participation_pct": max_participation_pct}
    wf = walk_forward_lab(strat, base, grid, df, folds, mode, capital, fee_pct, slippage_pct, rules, extra)
    pmap = parameter_map(strat, base, grid, df, capital, fee_pct, slippage_pct, rules, extra)
    stress = replay_with_costs(strat, wf, df, capital, fee_pct, slippage_pct, rules, extra) if wf["summary"]["usable_folds"] else []
    ts, eq, trades = wf["_ts"], wf["_equity"], wf["_trades"]
    robust = {
        "bootstrap": bootstrap_ci(trades), "concentration": concentration(trades),
        "drawdown": drawdown_profile(ts, eq) if len(eq) else None,
        "tail": tail_risk(eq, trades) if len(eq) else None,
        "regimes": regime_split(df, ts, eq) if len(eq) else None,
    }
    variants = len(pmap["_keys"]) if pmap.get("available") and pmap.get("_keys") else 1
    rets = pmap.get("_returns")
    trial_sh = _sharpe_cols(rets.T) if rets is not None and rets.shape[0] >= 3 else None
    oos_r = bar_returns(eq, 1.0) if len(eq) else np.array([])
    multi = {
        "variants_this_run": variants, "prior_variants": prior_variants,
        "pbo": pbo(rets),
        "dsr": deflated_sharpe(oos_r, prior_variants + variants, trial_sh) if len(oos_r) else {"available": False, "value": None},
    }
    research: dict[str, Any] | None = None
    if len(eq) >= 20:
        eq_money = eq * capital
        dd = float(((eq / np.maximum.accumulate(eq)) - 1).min()) * 100
        research = {**risk_ratios(eq_money, ts, capital, dd), **trade_stats(trades), "max_drawdown_pct": _clean(dd),
                    "total_return_pct": wf["summary"]["stitched_return_pct"]}
    verdict = build_verdict(wf, pmap, robust, multi, stress, len(df))
    pmap_out = {k: v for k, v in pmap.items() if not k.startswith("_")}
    return {
        "verdict": verdict,
        "walk_forward": {"summary": wf["summary"], "folds": wf["folds"]},
        "parameter_map": pmap_out,
        "robustness": {**robust, "cost_stress": stress},
        "multiple_testing": multi,
        "research": research,
        "notes": [
            "Параметры в каждом окне подбирались только на данных до окна; окно торгуется отдельно с собственным капиталом.",
            "Окна сшиты подряд: итоговая доходность — произведение доходностей окон.",
            "Карта параметров считается на всей истории и описывает устойчивость, а не служит для выбора параметров.",
            "Прошлое не гарантирует будущее: устойчивость на истории — необходимое, но не достаточное условие.",
        ],
    }
