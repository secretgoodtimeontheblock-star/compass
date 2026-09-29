"""Проверка бэктеста на устойчивость: коэффициенты риска, walk-forward, ресэмплинг
сделок и «карточка запуска» для воспроизводимости.

Идеи взяты из Vibe-Trading (backtest/validation.py, metrics.py, run_card.py, MIT).
Всё здесь — описательная статистика по уже посчитанному бэктесту: она не улучшает
стратегию и не предсказывает будущее, а показывает, насколько результат случаен.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from compass.backtest import BacktestResult, Trade

ENGINE_VERSION = "2"  # менять при любом изменении правил исполнения в backtest.py
_MS_PER_YEAR = 365.25 * 86_400_000
_MIN_YEARS_FOR_RATIOS = 60 / 365.25  # на коротком периоде годовые коэффициенты вводят в заблуждение
_MIN_TRADES_MC = 5
MIN_TRADES_FOR_STATS = 10  # меньше — доля прибыльных и коэффициенты почти ничего не говорят
_SAMPLE_DEPENDENT = ("win_rate_pct", "avg_trade_pct", "profit_factor", "payoff_ratio", "sharpe", "sortino", "calmar", "cagr_pct")
_MIN_BARS_PER_WINDOW = 20


def _years(ts: np.ndarray) -> float:
    return float(ts[-1] - ts[0]) / _MS_PER_YEAR if len(ts) > 1 else 0.0


def _clean(x: float | None, nd: int = 2) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def bars_per_year(ts: np.ndarray) -> float | None:
    """Свечей в году по факту: у акций ~250 дневных, у крипты 365 — считаем по данным."""
    years = _years(ts)
    return (len(ts) - 1) / years if years > 0 else None


def risk_ratios(
    equity: np.ndarray, ts: np.ndarray, capital: float, max_dd_pct: float
) -> dict[str, float | None]:
    """Sharpe / Sortino / Calmar. Безрисковая ставка = 0. None, если истории меньше ~2 месяцев."""
    empty: dict[str, float | None] = {"sharpe": None, "sortino": None, "calmar": None, "cagr_pct": None}
    years = _years(ts)
    bpy = bars_per_year(ts)
    if bpy is None or years < _MIN_YEARS_FOR_RATIOS or len(equity) < 20:
        return empty
    prev = np.concatenate(([capital], equity[:-1]))
    ret = equity / prev - 1
    std = ret.std(ddof=1)
    down = math.sqrt(float(np.mean(np.minimum(ret, 0.0) ** 2)))
    ann = math.sqrt(bpy)
    cagr = (equity[-1] / capital) ** (1 / years) - 1 if equity[-1] > 0 else -1.0
    return {
        "sharpe": _clean(ret.mean() / std * ann) if std > 1e-12 else None,
        "sortino": _clean(ret.mean() / down * ann) if down > 1e-12 else None,
        "calmar": _clean(cagr * 100 / abs(max_dd_pct)) if max_dd_pct < -1e-9 else None,
        "cagr_pct": _clean(cagr * 100),
    }


def trade_stats(trades: list[Trade]) -> dict[str, float | int | None]:
    closed = [t.pnl_pct for t in trades if t.exit_ts is not None]
    streak = best = 0
    for p in closed:
        streak = streak + 1 if p <= 0 else 0
        best = max(best, streak)
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p <= 0]
    payoff = (sum(wins) / len(wins)) / abs(sum(losses) / len(losses)) if wins and losses and sum(losses) else None
    return {
        "max_consecutive_losses": best,
        "payoff_ratio": _clean(payoff),
        "best_trade_pct": _clean(max(closed) * 100) if closed else None,
        "worst_trade_pct": _clean(min(closed) * 100) if closed else None,
    }


def walk_forward(
    df: pd.DataFrame, bt: BacktestResult, capital: float, n_windows: int = 4
) -> dict[str, Any] | None:
    """Делит историю на n подряд идущих окон и считает результат в каждом отдельно.
    Если прибыль есть только в одном окне — это не устойчивость, а одна удачная полоса."""
    n = len(bt.equity)
    if n < n_windows * _MIN_BARS_PER_WINDOW:
        return None
    ts = np.array([t for t, _ in bt.equity])
    eq = np.array([v for _, v in bt.equity])
    closes = df["close"].to_numpy()
    size = n // n_windows
    windows = []
    for w in range(n_windows):
        a, b = w * size, (w + 1) * size if w < n_windows - 1 else n
        seg = eq[a:b]
        start_val = eq[a - 1] if a > 0 else capital
        peak = np.maximum.accumulate(np.concatenate(([start_val], seg)))
        dd = (np.concatenate(([start_val], seg)) / peak - 1).min()
        trades_in = [t for t in bt.trades if ts[a] <= t.entry_ts <= ts[b - 1]]
        windows.append(
            {
                "start": int(ts[a]),
                "end": int(ts[b - 1]),
                "return_pct": _clean((seg[-1] / start_val - 1) * 100),
                "buy_hold_pct": _clean((closes[b - 1] / (closes[a - 1] if a > 0 else closes[0]) - 1) * 100),
                "max_drawdown_pct": _clean(dd * 100),
                "trades": len(trades_in),
            }
        )
    rets = [w["return_pct"] for w in windows]
    profitable = sum(1 for r in rets if r is not None and r > 0)
    return {
        "windows": windows,
        "profitable_windows": profitable,
        "n_windows": n_windows,
        "return_std_pct": _clean(float(np.std(rets))),
    }


def trade_resampling(
    trades: list[Trade], n_sim: int = 2000, seed: int = 42
) -> dict[str, Any] | None:
    """Показывает разброс результата при другом порядке и составе тех же сделок.

    Перемешивание порядка (без возвращения) меняет только просадку — итог при полном
    реинвестировании тот же. Ресэмплинг с возвращением показывает, как сильно итог
    зависит от нескольких удачных сделок."""
    pnl = np.array([t.pnl_pct for t in trades if t.exit_ts is not None])
    if len(pnl) < _MIN_TRADES_MC:
        return None
    rng = np.random.default_rng(seed)

    def path_stats(mat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        curve = np.cumprod(1 + mat, axis=1)
        peak = np.maximum.accumulate(np.concatenate([np.ones((len(curve), 1)), curve], axis=1), axis=1)[:, 1:]
        return curve[:, -1] - 1, (curve / peak - 1).min(axis=1)

    shuffled = np.array([rng.permutation(pnl) for _ in range(n_sim)])
    _, dd_shuffled = path_stats(shuffled)
    boot = rng.choice(pnl, size=(n_sim, len(pnl)), replace=True)
    ret_boot, dd_boot = path_stats(boot)
    _, actual_dd = path_stats(pnl[None, :])
    pct = lambda a, q: _clean(float(np.percentile(a, q)) * 100)
    return {
        "trades": len(pnl),
        "simulations": n_sim,
        "profitable_share_pct": _clean(float((ret_boot > 0).mean()) * 100, 1),
        "return_p5_pct": pct(ret_boot, 5),
        "return_p50_pct": pct(ret_boot, 50),
        "return_p95_pct": pct(ret_boot, 95),
        "drawdown_actual_pct": _clean(float(actual_dd[0]) * 100),
        # «плохой порядок» сделок: просадка, которую превысят лишь 5% перестановок
        "drawdown_shuffled_p95_pct": pct(dd_shuffled, 5),
        "drawdown_bootstrap_p95_pct": pct(dd_boot, 5),
    }


def data_fingerprint(df: pd.DataFrame) -> str:
    h = hashlib.sha256()
    for col in ("ts", "open", "high", "low", "close", "volume"):
        h.update(np.ascontiguousarray(df[col].to_numpy(dtype=np.float64)).tobytes())
    return h.hexdigest()[:16]


def run_card(
    df: pd.DataFrame,
    *,
    market: str,
    symbol: str,
    tf: str,
    strategy: str,
    params: dict[str, int],
    capital: float,
    fee_pct: float,
    slippage_pct: float,
    stale: bool,
) -> dict[str, Any]:
    """Всё, что нужно, чтобы повторить расчёт и понять, совпадает ли он с прежним."""
    return {
        "engine_version": ENGINE_VERSION,
        "market": market,
        "symbol": symbol,
        "tf": tf,
        "strategy": strategy,
        "params": params,
        "capital": capital,
        "fee_pct": fee_pct,
        "slippage_pct": slippage_pct,
        "start_ts": int(df["ts"].iloc[0]),
        "end_ts": int(df["ts"].iloc[-1]),
        "candles": len(df),
        "data_hash": data_fingerprint(df),
        "data_stale": stale,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "assumptions": [
            "long/flat, без плеча, весь капитал в сделку",
            "решение на закрытии свечи, исполнение по открытию следующей",
            "стоп-лоссов нет, ликвидность не учитывается",
            "безрисковая ставка 0 в Sharpe/Sortino",
        ],
    }


def apply_sample_rules(metrics: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Скрывает показатели, по которым при малом числе сделок нельзя делать выводы.

    Доходность, просадка и «купил и держи» остаются: это факты о прошедшем периоде.
    Скрытое заменяется на None, а причина возвращается текстом для пользователя."""
    trades = int(metrics.get("trades") or 0)
    if trades >= MIN_TRADES_FOR_STATS:
        return metrics, []
    out = {k: (None if k in _SAMPLE_DEPENDENT else v) for k, v in metrics.items()}
    note = (
        f"Закрытых сделок: {trades}. Меньше {MIN_TRADES_FOR_STATS} — доля прибыльных, профит-фактор, "
        "Sharpe, Sortino и Calmar не показываются: при такой выборке они случайны."
    )
    return out, [note]
