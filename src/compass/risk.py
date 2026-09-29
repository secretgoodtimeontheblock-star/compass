"""Риск-калькулятор: сколько купить, чтобы срабатывание стопа стоило не больше
заданного процента капитала. Считает код, не AI.

Потеря при стопе — расчётный сценарий, а не гарантированный максимум: цена может
пройти стоп гэпом, а заявка исполниться хуже. Поэтому кроме сценария «стоп сработал
с обычным проскальзыванием» считается ухудшенный (проскальзывание в несколько раз
больше), и оба подписаны в ответе."""

from __future__ import annotations

import math
from dataclasses import dataclass

# Допущения по умолчанию, общие для бэктеста, плана и объяснения сигнала.
DEFAULT_FEE_PCT = {"moex": 0.05, "crypto": 0.1}
DEFAULT_SLIPPAGE_PCT = 0.05
WORSE_SLIPPAGE_MULT = 3.0  # ухудшенное исполнение: проскальзывание втрое больше обычного


@dataclass(frozen=True, slots=True)
class PositionSize:
    qty: float  # штук (для акций — кратно лоту, для крипты — с шагом qty_step)
    lots: float
    cost: float  # во сколько обойдётся вход: цена с проскальзыванием + комиссия
    risk_amount: float  # расчётная потеря, если сработает стоп (с комиссиями и проскальзыванием)
    risk_amount_worse: float  # то же при ухудшенном исполнении
    budget: float  # допустимая потеря: капитал × риск на сделку
    capped: bool  # True — количество ограничено свободными средствами, а не риском
    warning: str | None = None  # почему количество нулевое или урезано


def _loss_per_unit(entry: float, stop: float, fee: float, slip: float, unit_value: float) -> float:
    """Потеря на единицу: заплатили за вход с проскальзыванием и комиссией, вышли по стопу
    с проскальзыванием и комиссией. unit_value — деньги за единицу цены на одну бумагу."""
    paid = entry * (1 + slip) * (1 + fee)
    got = stop * (1 - slip) * (1 - fee)
    return (paid - got) * unit_value


def position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    stop: float,
    lot: int = 1,
    qty_step: float | None = None,
    fee_pct: float = 0.0,
    slippage_pct: float = 0.0,
    available: float | None = None,
    unit_value: float = 1.0,
    accrued: float = 0.0,
    min_qty: float | None = None,
    min_cost: float | None = None,
) -> PositionSize:
    """lot — размер лота (акции МосБиржи); qty_step — шаг количества (крипта, напр. 1e-6).
    available — свободные средства (по умолчанию весь капитал); fee_pct, slippage_pct — в процентах.
    unit_value — во сколько денег превращается единица цены: для облигаций цена в % номинала,
    поэтому номинал/100; accrued — НКД на бумагу (платится при покупке, при расчёте потери не учитывается);
    min_qty, min_cost — минимальная заявка биржи."""
    if capital <= 0 or not 0 < risk_pct <= 100:
        raise ValueError("Капитал должен быть больше нуля, риск — от 0 до 100%")
    if not all(math.isfinite(x) for x in (capital, risk_pct, entry, stop)):
        raise ValueError("Числа должны быть конечными")
    if entry <= 0 or stop <= 0:
        raise ValueError("Цены входа и стопа должны быть положительными")
    if stop >= entry:
        raise ValueError("Стоп должен быть ниже цены входа (мы торгуем только в лонг)")
    if lot < 1:
        raise ValueError("Лот должен быть не меньше 1")
    if fee_pct < 0 or slippage_pct < 0 or fee_pct >= 100 or slippage_pct >= 100:
        raise ValueError("Комиссия и проскальзывание — от 0 до 100%")
    funds = capital if available is None else available
    if funds < 0:
        raise ValueError("Свободные средства не могут быть отрицательными")
    if unit_value <= 0 or accrued < 0:
        raise ValueError("Номинал и НКД некорректны")

    fee, slip = fee_pct / 100, slippage_pct / 100
    per_unit_loss = _loss_per_unit(entry, stop, fee, slip, unit_value)
    per_unit_cost = entry * (1 + slip) * (1 + fee) * unit_value + accrued
    budget = capital * risk_pct / 100

    by_risk = budget / per_unit_loss
    by_funds = funds / per_unit_cost
    capped = by_risk > by_funds
    raw = min(by_risk, by_funds)

    if qty_step:
        qty = math.floor(raw / qty_step + 1e-9) * qty_step
        lots = qty
    else:
        lots = math.floor(raw / lot + 1e-9)
        qty = lots * lot

    warning = None
    below_min = qty > 0 and (
        (min_qty is not None and qty < min_qty - 1e-12) or (min_cost is not None and qty * per_unit_cost < min_cost)
    )
    if below_min:
        warning = "Количество меньше минимальной заявки биржи: такую заявку не примут."
        qty = lots = 0.0
    unit = lot if not qty_step else qty_step
    if below_min:
        pass
    elif qty <= 0:
        if unit * per_unit_cost > funds:
            warning = "Свободных средств не хватает даже на минимальную покупку (один лот)."
        else:
            warning = (
                f"Один лот рискует {unit * per_unit_loss:,.2f} — больше допустимых {budget:,.2f}. "
                "Уменьшите расстояние до стопа или увеличьте риск на сделку: сейчас покупать нечего."
            ).replace(",", " ")
    elif capped:
        warning = "Количество ограничено свободными средствами: риск на сделку получится меньше заданного."

    worse_loss = _loss_per_unit(entry, stop, fee, slip * WORSE_SLIPPAGE_MULT, unit_value)
    return PositionSize(
        qty=round(qty, 10),
        lots=round(lots, 10),
        cost=round(qty * per_unit_cost, 2),
        risk_amount=round(qty * per_unit_loss, 2),
        risk_amount_worse=round(qty * worse_loss, 2),
        budget=round(budget, 2),
        capped=capped,
        warning=warning,
    )
