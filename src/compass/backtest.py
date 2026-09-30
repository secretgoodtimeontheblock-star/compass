"""Бэктест long/flat без заглядывания в будущее.

Решение принимается на ЗАКРЫТИИ свечи i, сделка исполняется по ОТКРЫТИЮ свечи
i+1 — так же, как будет в жизни: сигнал видно только после закрытия. Комиссия берётся с каждой
стороны, проскальзывание ухудшает цену входа и выхода.

Два режима размера и выхода:
- по умолчанию — все деньги в сделку, выход только по сигналу стратегии (как раньше);
- с `Rules` — размер по риску (тот же расчёт, что в риск-калькуляторе: комиссии, проскальзывание,
  лот/шаг, свободные средства), стоп на N·ATR от закрытия сигнальной свечи и, если задано, цель в R.

Правила исполнения внутри свечи. OHLC не говорит, что было раньше — минимум или максимум свечи,
поэтому порядок внутри свечи не придумывается:
- открытие ниже стопа (гэп) — выход по открытию, а не по стопу: гэп бьёт сильнее;
- открытие выше цели — выход по открытию;
- в одной свече достигнуты и стоп, и цель — принимается СТОП (консервативно), свеча считается неоднозначной;
- после выхода по стопу/цели повторный вход возможен только после нового сигнала (цель стратегии
  должна сначала стать «вне рынка»), иначе тут же вернулись бы в сделку, которую только что закрыли.
- если открытие для входа уже ниже стопа (или размер по риску округлился до нуля), вход пропускается и
  считается в `skipped_entries`; пока стратегия держит «в рынке», попытка повторяется на следующей свече.

Ограничения (честно): ликвидность и размер заявки относительно объёма не учитываются; исполнение
по цели считается по цене цели с проскальзыванием (консервативно); накопленный купонный доход,
дивиденды и налоги не учитываются; прошлое не гарантирует будущее.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from compass.indicators import atr
from compass.risk import position_size


@dataclass(frozen=True, slots=True)
class Trade:
    entry_ts: int
    entry_price: float
    exit_ts: int | None  # None — позиция ещё открыта
    exit_price: float | None
    pnl_pct: float  # доходность на стоимость входа; для открытой — по последней цене закрытия, комиссия выхода учтена
    exit_reason: str = "signal"  # signal | stop | gap_stop | target | gap_target | open
    qty: float = 0.0
    stop: float | None = None
    target: float | None = None
    risk_amount: float | None = None  # расчётная потеря при стопе в деньгах: знаменатель R
    pnl_amount: float = 0.0
    mae_pct: float = 0.0  # худшее движение цены против позиции, % от цены входа (≤ 0)
    mfe_pct: float = 0.0  # лучшее движение цены в пользу позиции, % от цены входа (≥ 0)
    bars: int = 0


@dataclass(frozen=True, slots=True)
class Rules:
    """Стопы, цели и размер по риску. Без стопа размер по риску невозможен."""

    stop_atr_mult: float | None = None  # стоп = закрытие сигнальной свечи − N·ATR
    target_r: float | None = None  # цель = вход + R·(вход − стоп)
    risk_pct: float | None = None  # риск на сделку, % текущего капитала; None — все деньги
    atr_period: int = 14
    lot: int = 1
    qty_step: float | None = None
    unit_value: float = 1.0  # деньги за единицу цены на бумагу (облигации: номинал/100)

    def check(self) -> None:
        if self.target_r is not None and self.stop_atr_mult is None:
            raise ValueError("Цель в R невозможна без стопа")
        if self.risk_pct is not None and self.stop_atr_mult is None:
            raise ValueError("Размер по риску невозможен без стопа")
        if self.stop_atr_mult is not None and not 0 < self.stop_atr_mult <= 20:
            raise ValueError("Стоп в ATR — от 0 до 20")
        if self.target_r is not None and not 0 < self.target_r <= 50:
            raise ValueError("Цель в R — от 0 до 50")
        if self.risk_pct is not None and not 0 < self.risk_pct <= 100:
            raise ValueError("Риск на сделку — от 0 до 100%")
        if self.atr_period < 2 or self.lot < 1 or self.unit_value <= 0:
            raise ValueError("Некорректные параметры правил")


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
    rules: Rules | None = None,
    start: int = 0,
) -> BacktestResult:
    """start — индекс первой торгуемой свечи: более ранние нужны только индикаторам (ATR) и не торгуются.
    Так проверочный период считается отдельно, с собственным начальным капиталом, а не «хвостом» общего прогона."""
    if len(df) != len(target):
        raise ValueError("target и свечи разной длины")
    if capital <= 0 or fee_pct < 0 or slippage_pct < 0:
        raise ValueError("Капитал должен быть больше нуля, комиссии — неотрицательны")
    n = len(df)
    if n < 2:
        raise ValueError("Слишком мало свечей для бэктеста")
    if not 0 <= start <= n - 2:
        raise ValueError("Начало периода вне ряда свечей")
    rules = rules or Rules()
    rules.check()

    fee, slip, uv = fee_pct / 100, slippage_pct / 100, rules.unit_value
    ts, opens, closes = df["ts"].to_numpy(), df["open"].to_numpy(), df["close"].to_numpy()
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    tgt = target.to_numpy()
    atr_arr = atr(df, rules.atr_period).to_numpy() if rules.stop_atr_mult else None

    cash, qty = capital, 0.0
    spent = 0.0
    entry_price, entry_ts, entry_i = 0.0, 0, 0
    stop_level: float | None = None
    target_level: float | None = None
    risk_amount: float | None = None
    run_low = run_high = 0.0
    pending: str | None = None
    blocked = False  # после выхода по стопу/цели — ждём нового сигнала
    trades: list[Trade] = []
    equity: list[tuple[int, float]] = []
    bars_in_market = 0
    counters = {"stops": 0, "targets": 0, "gap_exits": 0, "ambiguous_bars": 0, "skipped_entries": 0}

    def close_position(i: int, price: float, reason: str) -> None:
        nonlocal cash, qty
        proceeds = qty * price * uv * (1 - fee)
        cash += proceeds
        trades.append(
            Trade(
                entry_ts, entry_price, int(ts[i]), price, proceeds / spent - 1, reason, qty, stop_level,
                target_level, risk_amount, proceeds - spent, (run_low / entry_price - 1) * 100,
                (run_high / entry_price - 1) * 100, i - entry_i,
            )
        )
        qty = 0.0

    for i in range(start, n):
        # 1. отложенное решение прошлой свечи исполняется по открытию этой
        if pending == "buy" and qty == 0:
            price = opens[i] * (1 + slip)
            stop_level = target_level = risk_amount = None
            ok = True
            if atr_arr is not None:
                a = atr_arr[i - 1]
                stop_level = float(closes[i - 1] - rules.stop_atr_mult * a) if not math.isnan(a) else None
                if stop_level is None or stop_level <= 0 or opens[i] <= stop_level:
                    ok = False  # прогрев ATR или открытие уже ниже стопа: входить нельзя
            new_qty = 0.0
            if ok:
                if rules.risk_pct is not None:
                    sized = position_size(
                        cash, rules.risk_pct, float(opens[i]), stop_level, lot=rules.lot,
                        qty_step=rules.qty_step, fee_pct=fee_pct, slippage_pct=slippage_pct, available=cash,
                        unit_value=uv,
                    )
                    new_qty = sized.qty
                else:
                    new_qty = cash / (price * uv * (1 + fee))
            if new_qty > 0:
                qty = new_qty
                spent = qty * price * uv * (1 + fee)
                cash -= spent
                entry_price, entry_ts, entry_i = price, int(ts[i]), i
                run_low, run_high = price, price
                if stop_level is not None:
                    risk_amount = qty * uv * (price * (1 + fee) - stop_level * (1 - slip) * (1 - fee))
                    if rules.target_r is not None:
                        target_level = price + rules.target_r * (price - stop_level)
            else:
                counters["skipped_entries"] += 1
                stop_level = target_level = None
        elif pending == "sell" and qty > 0:
            close_position(i, opens[i] * (1 - slip), "signal")
        pending = None

        # 2. стоп и цель внутри свечи (в том числе в свече входа)
        if qty > 0:
            run_low, run_high = min(run_low, lows[i]), max(run_high, highs[i])
            if stop_level is not None:
                if opens[i] <= stop_level:
                    counters["stops"] += 1
                    counters["gap_exits"] += 1
                    close_position(i, opens[i] * (1 - slip), "gap_stop")
                elif target_level is not None and opens[i] >= target_level:
                    counters["targets"] += 1
                    counters["gap_exits"] += 1
                    close_position(i, opens[i] * (1 - slip), "gap_target")
                else:
                    hit_stop = lows[i] <= stop_level
                    hit_target = target_level is not None and highs[i] >= target_level
                    if hit_stop and hit_target:
                        counters["ambiguous_bars"] += 1  # порядок внутри свечи неизвестен — берём худшее
                    if hit_stop:
                        counters["stops"] += 1
                        close_position(i, stop_level * (1 - slip), "stop")
                    elif hit_target:
                        counters["targets"] += 1
                        close_position(i, target_level * (1 - slip), "target")
                if qty == 0:
                    blocked = True

        if qty > 0:
            bars_in_market += 1
        equity.append((int(ts[i]), cash + qty * closes[i] * uv))

        if i < n - 1:  # на последней свече заявку исполнять уже нечем
            if tgt[i] == 0:
                blocked = False
            if tgt[i] == 1 and qty == 0 and not blocked:
                pending = "buy"
            elif tgt[i] == 0 and qty > 0:
                pending = "sell"

    if qty > 0:
        mark = qty * closes[-1] * uv * (1 - fee)
        trades.append(
            Trade(
                entry_ts, entry_price, None, None, mark / spent - 1, "open", qty, stop_level, target_level,
                risk_amount, mark - spent, (run_low / entry_price - 1) * 100, (run_high / entry_price - 1) * 100,
                n - 1 - entry_i,
            )
        )

    metrics = _metrics(trades, equity, closes[start:], capital, bars_in_market, n - start)
    metrics.update(counters)
    return BacktestResult(trades, equity, metrics)


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
    rs = [t.pnl_amount / t.risk_amount for t in closed if t.risk_amount]
    return {
        "total_return_pct": round((eq[-1] / capital - 1) * 100, 2),
        "buy_hold_return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
        "max_drawdown_pct": round(float(((eq / peak) - 1).min()) * 100, 2),
        "trades": len(closed),
        "open_trade": int(len(trades) > len(closed)),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_trade_pct": round(sum(t.pnl_pct for t in closed) / len(closed) * 100, 2) if closed else None,
        "avg_win_pct": round(sum(wins) / len(wins) * 100, 2) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses) * 100, 2) if losses else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "avg_mae_pct": round(sum(t.mae_pct for t in closed) / len(closed), 2) if closed else None,
        "avg_mfe_pct": round(sum(t.mfe_pct for t in closed) / len(closed), 2) if closed else None,
        "avg_bars_held": round(sum(t.bars for t in closed) / len(closed), 1) if closed else None,
        "exposure_pct": round(bars_in_market / n * 100, 1),
        "candles": n,
    }
