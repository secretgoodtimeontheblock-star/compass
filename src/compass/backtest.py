"""Бэктест long/flat без заглядывания в будущее.

Решение принимается на ЗАКРЫТИИ свечи i, сделка исполняется по ОТКРЫТИЮ свечи
i+1 — так же, как будет в жизни: сигнал видно только после закрытия. Каждая
сделка — все деньги (без плеча, без пирамидинга). Комиссия берётся с каждой
стороны, проскальзывание ухудшает цену входа и выхода.

Ограничения (честно): стоп-лоссов в модели нет; ликвидность и размер заявки не
учитываются; прошлое не гарантирует будущее.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Trade:
    entry_ts: int
    entry_price: float
    exit_ts: int | None  # None — позиция ещё открыта
    exit_price: float | None
    pnl_pct: float  # для открытой — по последней цене закрытия, комиссия выхода учтена


@dataclass(frozen=True, slots=True)
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[int, float]]  # (ts, капитал на закрытии свечи)
    metrics: dict[str, float | int | None]


def backtest(
    df: pd.DataFrame,
    target: pd.Series,
    capital: float = 100_000.0,
    fee_pct: float = 0.05,
    slippage_pct: float = 0.05,
) -> BacktestResult:
    if len(df) != len(target):
        raise ValueError("target и свечи разной длины")
    if capital <= 0 or fee_pct < 0 or slippage_pct < 0:
        raise ValueError("Капитал должен быть больше нуля, комиссии — неотрицательны")
    n = len(df)
    if n < 2:
        raise ValueError("Слишком мало свечей для бэктеста")

    fee, slip = fee_pct / 100, slippage_pct / 100
    ts, opens, closes = df["ts"].to_numpy(), df["open"].to_numpy(), df["close"].to_numpy()
    tgt = target.to_numpy()

    cash, qty = capital, 0.0
    spent = 0.0
    entry_price, entry_ts = 0.0, 0
    pending: str | None = None
    trades: list[Trade] = []
    equity: list[tuple[int, float]] = []
    bars_in_market = 0

    for i in range(n):
        if pending == "buy" and qty == 0:
            price = opens[i] * (1 + slip)
            spent = cash
            qty = cash / (price * (1 + fee))
            cash = 0.0
            entry_price, entry_ts = price, int(ts[i])
        elif pending == "sell" and qty > 0:
            price = opens[i] * (1 - slip)
            cash = qty * price * (1 - fee)
            trades.append(Trade(entry_ts, entry_price, int(ts[i]), price, cash / spent - 1))
            qty = 0.0
        pending = None

        if qty > 0:
            bars_in_market += 1
        equity.append((int(ts[i]), cash + qty * closes[i]))

        if i < n - 1:  # на последней свече заявку исполнять уже нечем
            if tgt[i] == 1 and qty == 0:
                pending = "buy"
            elif tgt[i] == 0 and qty > 0:
                pending = "sell"

    if qty > 0:
        mark = qty * closes[-1] * (1 - fee)
        trades.append(Trade(entry_ts, entry_price, None, None, mark / spent - 1))

    return BacktestResult(trades, equity, _metrics(trades, equity, closes, capital, bars_in_market, n))


def _metrics(
    trades: list[Trade],
    equity: list[tuple[int, float]],
    closes: np.ndarray,
    capital: float,
    bars_in_market: int,
    n: int,
) -> dict[str, float | int | None]:
    eq = np.array([v for _, v in equity])
    peak = np.maximum.accumulate(eq)
    closed = [t for t in trades if t.exit_ts is not None]
    wins = [t.pnl_pct for t in closed if t.pnl_pct > 0]
    losses = [t.pnl_pct for t in closed if t.pnl_pct <= 0]
    return {
        "total_return_pct": round((eq[-1] / capital - 1) * 100, 2),
        "buy_hold_return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "max_drawdown_pct": round(float(((eq / peak) - 1).min()) * 100, 2),
        "trades": len(closed),
        "open_trade": int(len(trades) > len(closed)),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_trade_pct": round(sum(t.pnl_pct for t in closed) / len(closed) * 100, 2) if closed else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "exposure_pct": round(bars_in_market / n * 100, 1),
        "candles": n,
    }
