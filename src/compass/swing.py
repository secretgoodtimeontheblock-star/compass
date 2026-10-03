"""Свинговый план крипто-сигнала на изолированные 100 USDT.

Короткий стоп без плеча превращает 100 долларов в копейки: заметная сумма
появляется только на большом депозите. Здесь стоп ставится по волатильности
(2·ATR дневной свечи), а плечо подбирается так, чтобы эти 100 USDT при стопе
теряли около половины маржи (внутри 30–100%), а цель в три риска давала
примерно 150% (внутри 30–300%). Выше этих 100 USDT сделка не теряет: маржа изолированная.
Потолок плеча 200× — это высокий риск потери: короткое движение цены
против сделки может забрать всю маржу.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

STAKE_USDT = 100.0
STOP_ATR_MULT = 2.0
AIM_LOSS_PCT = 50.0
MIN_LOSS_PCT = 30.0
MAX_LOSS_PCT = 100.0
AIM_R = 3.0
MIN_PROFIT_PCT = 30.0
MAX_PROFIT_PCT = 300.0
MAX_LEVERAGE = 200.0
# Стоп шире 80% цены уже не свинг, а ставка на обвал. Дальше плечо только снижается.
MAX_STOP_PCT = 80.0


@dataclass(frozen=True, slots=True)
class SwingPlan:
    stop: float
    target: float
    leverage: float
    stake: float


def swing_plan(price: float, atr: float, stake: float = STAKE_USDT) -> SwingPlan | None:
    """План входа. None — цену или ATR нельзя использовать."""
    if not all(math.isfinite(x) for x in (price, atr, stake)) or price <= 0 or atr <= 0 or stake <= 0:
        return None
    stop_pct = min(STOP_ATR_MULT * atr / price * 100, MAX_STOP_PCT)
    leverage = AIM_LOSS_PCT / stop_pct
    leverage = min(leverage, MAX_LEVERAGE, MAX_LOSS_PCT / stop_pct)
    leverage = max(1.0, math.floor(leverage * 10 + 1e-9) / 10)
    if stop_pct * leverage > MAX_LOSS_PCT:
        leverage = max(1.0, math.floor(MAX_LOSS_PCT / stop_pct * 10 + 1e-9) / 10)
    # Тесный стоп даже на максимальном плече даёт меньше 30% маржи — отодвигаем стоп.
    if stop_pct * leverage < MIN_LOSS_PCT:
        stop_pct = MIN_LOSS_PCT / leverage
    loss_pct = stop_pct * leverage
    profit_pct = min(MAX_PROFIT_PCT, max(MIN_PROFIT_PCT, AIM_R * loss_pct))
    stop = round(price * (1 - stop_pct / 100), 8)
    target = round(price * (1 + profit_pct / leverage / 100), 8)
    if stop <= 0 or stop >= price or target <= price:
        return None
    return SwingPlan(stop=stop, target=target, leverage=leverage, stake=stake)


def outcome(
    price: float,
    stop: float | None,
    target: float | None,
    leverage: float | None,
    stake: float | None,
) -> dict | None:
    """Сколько 100 USDT заработают на цели и потеряют на стопе. None — плана нет."""
    if stop is None or target is None or leverage is None or stake is None:
        return None
    if not all(math.isfinite(x) for x in (price, stop, target, leverage, stake)):
        return None
    if price <= 0 or stake <= 0 or leverage <= 0 or stop <= 0 or stop >= price or target <= price:
        return None
    loss_usdt = stake * leverage * (price - stop) / price
    profit_usdt = stake * leverage * (target - price) / price
    return {
        "stake": stake,
        "leverage": leverage,
        "target": target,
        "notional": round(stake * leverage, 2),
        "qty": round(stake * leverage / price, 8),
        "loss_usdt": round(loss_usdt, 2),
        "profit_usdt": round(profit_usdt, 2),
        "loss_pct": round(loss_usdt / stake * 100, 1),
        "profit_pct": round(profit_usdt / stake * 100, 1),
        "risk_note": (
            f"Высокий риск потери. Плечо {leverage:g}×, потолок {MAX_LEVERAGE:.0f}×: "
            "небольшое движение цены против сделки может забрать всю маржу."
        ),
    }
