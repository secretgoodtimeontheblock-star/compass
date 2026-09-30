"""Состояние счёта и совокупный риск по записям журнала.

Всё считается по РЕАЛЬНЫМ сделкам журнала одного рынка (одной валюты) — учебные и исторические не
входят. Рыночных цен здесь нет: нереализованная прибыль и убыток не учитываются, а «свободные
средства» = капитал + реализованный результат − стоимость открытых позиций по цене входа.

Риск открытой позиции — расчётный сценарий по записанному стопу (количество × (средняя цена − стоп)),
а не гарантированный максимум потерь: цена может пройти стоп гэпом. Позиция без записанного стопа
не ограничена ничем, и это показывается отдельно.

Цена в журнале — деньги за единицу актива. Для облигаций (в интерфейсе цена в % номинала)
стоимость и риск здесь считаются неверно — записывайте их по цене в рублях за бумагу."""

from __future__ import annotations

import math
from datetime import datetime
from itertools import pairwise
from typing import Any

import numpy as np

from compass.accounts import MARKET_TZ, Account
from compass.journal import Entry

_EPS = 1e-9
CORRELATION_WARN = 0.7
MIN_OVERLAP = 40  # меньше общих свечей — корреляции верить нельзя


def realized_events(entries: list[Entry]) -> list[tuple[int, str, float]]:
    """(время, тикер, реализованный результат) по каждой продаже, по средней цене с комиссиями — как в журнале."""
    by_symbol: dict[str, list[Entry]] = {}
    for e in entries:
        by_symbol.setdefault(e.symbol, []).append(e)
    out: list[tuple[int, str, float]] = []
    for symbol, items in by_symbol.items():
        qty = cost = 0.0
        for e in sorted(items, key=lambda x: (x.ts, x.side != "buy", x.id or 0)):
            if e.side == "buy":
                qty += e.qty
                cost += e.qty * e.price + e.fee
            else:
                avg = cost / qty if qty > _EPS else 0.0
                out.append((e.ts, symbol, e.qty * (e.price - avg) - e.fee))
                cost -= e.qty * avg
                qty -= e.qty
                if qty < _EPS:
                    qty = cost = 0.0
    return out


def _day_start_ms(now_ms: int, market: str) -> int:
    tz = MARKET_TZ[market]
    local = datetime.fromtimestamp(now_ms / 1000, tz)
    return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _open_positions(entries: list[Entry]) -> list[dict[str, Any]]:
    by_symbol: dict[str, list[Entry]] = {}
    for e in entries:
        by_symbol.setdefault(e.symbol, []).append(e)
    out = []
    for symbol, items in sorted(by_symbol.items()):
        qty = cost = 0.0
        stop: float | None = None
        for e in sorted(items, key=lambda x: (x.ts, x.side != "buy", x.id or 0)):
            if e.side == "buy":
                qty += e.qty
                cost += e.qty * e.price + e.fee
                if e.planned_stop is not None:
                    stop = e.planned_stop  # стоп последней покупки, у которой он записан
            else:
                avg = cost / qty if qty > _EPS else 0.0
                cost -= e.qty * avg
                qty -= e.qty
                if qty < _EPS:
                    qty = cost = 0.0
                    stop = None
        if qty > _EPS:
            out.append({"symbol": symbol, "qty": round(qty, 10), "avg_price": cost / qty, "cost": cost, "stop": stop})
    return out


def snapshot(account: Account, entries: list[Entry], now_ms: int) -> dict[str, Any]:
    """Состояние счёта. entries — действующие реальные записи этого рынка."""
    capital = account.capital
    events = realized_events(entries)
    realized_total = sum(p for _, _, p in events)
    day_from = _day_start_ms(now_ms, account.market)
    daily_pnl = sum(p for ts, _, p in events if ts >= day_from)
    positions = _open_positions(entries)
    exposure = sum(p["cost"] for p in positions)
    open_risk = 0.0
    unprotected = []
    for p in positions:
        stop = p["stop"]
        if stop is None:
            p["risk_at_stop"] = None
            unprotected.append(p["symbol"])
        else:
            p["risk_at_stop"] = round(max(0.0, p["qty"] * (p["avg_price"] - stop)), 2)
            open_risk += p["risk_at_stop"]
        p["cost"] = round(p["cost"], 2)
        p["avg_price"] = round(p["avg_price"], 8)
    equity = None if capital is None else capital + realized_total
    free = None if equity is None else equity - exposure
    warnings: list[str] = []
    pct = (lambda v: None if not capital else round(v / capital * 100, 2))
    daily_limit = None if not capital else capital * account.daily_loss_limit_pct / 100
    daily_breached = daily_limit is not None and daily_pnl <= -daily_limit
    if daily_breached:
        warnings.append(
            f"Дневной лимит убытка достигнут: сегодня {daily_pnl:,.2f} при лимите −{daily_limit:,.2f} "
            f"({account.daily_loss_limit_pct:g}% капитала). Ваш план говорит остановиться на сегодня.".replace(",", " ")
        )
    day_entries = sum(1 for e in entries if e.side == "buy" and e.ts >= day_from)
    trades_breached = account.max_trades_per_day is not None and day_entries >= account.max_trades_per_day
    if trades_breached:
        warnings.append(
            f"Сегодня уже {day_entries} входов при вашем лимите {account.max_trades_per_day}: частые сделки съедают результат "
            "комиссиями и проскальзыванием, а ошибки в них копятся."
        )
    heat_pct = pct(open_risk)
    if heat_pct is not None and heat_pct > account.max_open_risk_pct:
        warnings.append(
            f"Суммарный риск открытых позиций {heat_pct:g}% капитала выше вашего предела {account.max_open_risk_pct:g}%."
        )
    if unprotected:
        warnings.append(
            f"Нет записанного стопа: {', '.join(unprotected)}. Риск по этим позициям не ограничен и в расчёт риска не входит."
        )
    if capital is None:
        warnings.append("Капитал счёта не задан: доли и свободные средства посчитать нельзя.")
    return {
        "market": account.market, "name": account.name, "currency": account.currency,
        "capital": capital, "equity": None if equity is None else round(equity, 2),
        "free": None if free is None else round(free, 2),
        "realized_total": round(realized_total, 2), "daily_pnl": round(daily_pnl, 2),
        "daily_limit": None if daily_limit is None else round(daily_limit, 2), "daily_limit_breached": daily_breached,
        "entries_today": day_entries, "max_trades_per_day": account.max_trades_per_day, "trades_limit_reached": trades_breached,
        "exposure": round(exposure, 2), "exposure_pct": pct(exposure),
        "open_risk": round(open_risk, 2), "heat_pct": heat_pct, "max_open_risk_pct": account.max_open_risk_pct,
        "positions": positions, "unprotected": unprotected, "warnings": warnings,
        "notes": [
            "Без рыночных цен: нереализованный результат не учитывается.",
            "Риск по стопу — расчётный сценарий, а не гарантированный максимум потерь.",
        ],
    }


def assess_new_position(snap: dict[str, Any], account: Account, risk_amount: float, cost: float) -> dict[str, Any]:
    """Как новая позиция изменит совокупный риск. Только предупреждения: ничего не блокируется."""
    capital = account.capital
    heat_after = None if not capital else round((snap["open_risk"] + risk_amount) / capital * 100, 2)
    exposure_after = None if not capital else round((snap["exposure"] + cost) / capital * 100, 2)
    warnings: list[str] = []
    if snap["daily_limit_breached"]:
        warnings.append("Дневной лимит убытка уже достигнут: новый вход нарушает ваш собственный план на день.")
    if heat_after is not None and heat_after > account.max_open_risk_pct:
        warnings.append(
            f"С этой позицией суммарный риск открытых позиций станет {heat_after:g}% капитала при вашем пределе "
            f"{account.max_open_risk_pct:g}%."
        )
    if snap.get("trades_limit_reached"):
        warnings.append("Лимит входов на сегодня уже достигнут: новый вход выходит за ваш собственный план на день.")
    if snap["free"] is not None and cost > snap["free"] + 1e-9:
        warnings.append("Стоимость входа больше свободных средств счёта.")
    if snap["unprotected"]:
        warnings.append(
            f"Позиции без записанного стопа ({', '.join(snap['unprotected'])}) в суммарный риск не входят: "
            "реальный риск выше расчётного."
        )
    return {
        "heat_before_pct": snap["heat_pct"], "heat_after_pct": heat_after, "exposure_after_pct": exposure_after,
        "daily_pnl": snap["daily_pnl"], "daily_limit_breached": snap["daily_limit_breached"],
        "free": snap["free"], "currency": snap["currency"], "warnings": warnings,
    }


def _returns(closes: dict[int, float]) -> dict[int, float]:
    ts = sorted(closes)
    return {b: math.log(closes[b] / closes[a]) for a, b in pairwise(ts) if closes[a] > 0 and closes[b] > 0}


def correlated_positions(
    candidate: dict[int, float], others: dict[str, dict[int, float]], threshold: float = CORRELATION_WARN
) -> list[dict[str, Any]]:
    """Открытые позиции, чьи дневные доходности сильно совпадают с кандидатом. Мало общих свечей — не судим.
    Корреляция описывает прошлое и нестабильна: при обвале рынка бумаги часто падают вместе сильнее обычного."""
    base = _returns(candidate)
    out = []
    for symbol, closes in others.items():
        other = _returns(closes)
        common = sorted(set(base) & set(other))
        if len(common) < MIN_OVERLAP:
            continue
        a = np.array([base[t] for t in common])
        b = np.array([other[t] for t in common])
        if a.std() < 1e-12 or b.std() < 1e-12:
            continue
        r = float(np.corrcoef(a, b)[0, 1])
        if abs(r) >= threshold:
            out.append({"symbol": symbol, "correlation": round(r, 2), "overlap": len(common)})
    return sorted(out, key=lambda x: -abs(x["correlation"]))
