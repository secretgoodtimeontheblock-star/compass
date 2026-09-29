"""Риск-калькулятор: сколько купить, чтобы срабатывание стопа стоило не больше
заданного процента капитала. Считает код, не AI."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PositionSize:
    qty: float  # штук (для акций — кратно лоту, для крипты — с шагом qty_step)
    lots: float
    cost: float  # во сколько обойдётся вход
    risk_amount: float  # потеря, если сработает стоп
    capped: bool  # True — количество ограничено капиталом, а не риском


def position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    lot: int = 1,
    qty_step: float | None = None,
) -> PositionSize:
    """lot — размер лота (акции МосБиржи); qty_step — шаг количества (крипта, напр. 1e-6)."""
    if capital <= 0 or not 0 < risk_pct <= 100:
        raise ValueError("Капитал должен быть больше нуля, риск — от 0 до 100%")
    if entry <= 0 or stop <= 0:
        raise ValueError("Цены входа и стопа должны быть положительными")
    if stop >= entry:
        raise ValueError("Стоп должен быть ниже цены входа (мы торгуем только в лонг)")
    if lot < 1:
        raise ValueError("Лот должен быть не меньше 1")

    risk_amount = capital * risk_pct / 100
    per_unit_risk = entry - stop
    raw_qty = risk_amount / per_unit_risk
    max_qty_by_capital = capital / entry
    capped = raw_qty > max_qty_by_capital
    qty = min(raw_qty, max_qty_by_capital)

    if qty_step:
        qty = math.floor(qty / qty_step + 1e-9) * qty_step
        lots = qty
    else:
        lots = math.floor(qty / lot)
        qty = lots * lot
    return PositionSize(
        qty=round(qty, 10),
        lots=round(lots, 10),
        cost=round(qty * entry, 2),
        risk_amount=round(qty * per_unit_risk, 2),
        capped=capped,
    )
