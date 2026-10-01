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
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

import numpy as np

from compass.accounts import MARKET_TZ, Account
from compass.journal import Entry

_EPS = 1e-9
CORRELATION_WARN = 0.7
MIN_OVERLAP = 40  # меньше общих свечей — корреляции верить нельзя
CONCENTRATION_WARN_PCT = 40.0  # одна позиция дороже такой доли стоимости портфеля — предупреждение
QTY_TOL = 1e-6  # расхождение количества при сверке, ниже которого позиции считаются совпадающими
PRICE_TOL_PCT = 0.5  # расхождение средней цены при сверке, %


@dataclass(frozen=True, slots=True)
class Mark:
    """Рыночная цена инструмента для оценки позиции.

    price_ts — время свечи, из которой взята цена (мс UTC); fetched_at — когда источник последний раз
    успешно ответил (секунды UTC); stale — источник недоступен и показан кэш; max_age_s — как долго после
    ответа источника цену ещё можно считать свежей (зависит от таймфрейма)."""

    price: float
    price_ts: int
    fetched_at: int | None = None
    stale: bool = False
    source: str = ""
    max_age_s: int = 6 * 3600


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


def mark_status(mark: Mark | None, now_ms: int) -> tuple[str, int | None]:
    """«fresh» — цена свежая, «stale» — источник молчит или давно не отвечал, «missing» — цены нет."""
    if mark is None:
        return "missing", None
    age = None if mark.fetched_at is None else max(0, now_ms // 1000 - mark.fetched_at)
    if mark.stale or age is None or age > mark.max_age_s:
        return "stale", age
    return "fresh", age


def _value_positions(positions: list[dict[str, Any]], marks: dict[str, Mark], now_ms: int) -> None:
    """Дописывает к позициям оценку по метке: стоимость, нереализованный результат, риск от текущей цены."""
    for p in positions:
        mark = marks.get(p["symbol"])
        status, age = mark_status(mark, now_ms)
        p["mark_status"], p["mark_age_s"] = status, age
        stop = p["stop"]
        if mark is None:
            p.update(mark_price=None, mark_ts=None, mark_source=None, market_value=None, unrealized_pnl=None,
                     unrealized_pct=None, stop_breached=False)
            p["value"] = p["cost"]  # нет цены — оцениваем по входу и говорим об этом
            p["risk"], p["risk_basis"] = p["risk_at_stop"], "cost"
            continue
        value = p["qty"] * mark.price
        p.update(
            mark_price=round(mark.price, 8), mark_ts=mark.price_ts, mark_source=mark.source,
            market_value=round(value, 2), unrealized_pnl=round(value - p["cost"], 2),
            unrealized_pct=round((value / p["cost"] - 1) * 100, 2) if p["cost"] else None,
        )
        p["value"] = value
        p["stop_breached"] = stop is not None and mark.price <= stop
        if stop is None:
            p["risk"], p["risk_basis"] = None, "mark"
        else:
            p["risk"], p["risk_basis"] = round(max(0.0, p["qty"] * (mark.price - stop)), 2), "mark"


def snapshot(
    account: Account,
    entries: list[Entry],
    now_ms: int,
    marks: dict[str, Mark] | None = None,
    broker: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Состояние счёта. entries — действующие реальные записи этого рынка; marks — тикер → рыночная цена
    (нет — позиция оценивается по цене входа); broker — позиции из выписки брокера для сверки."""
    marks = marks or {}
    capital = account.capital
    events = realized_events(entries)
    realized_total = sum(p for _, _, p in events)
    day_from = _day_start_ms(now_ms, account.market)
    daily_pnl = sum(p for ts, _, p in events if ts >= day_from)
    positions = _open_positions(entries)
    cost_total = sum(p["cost"] for p in positions)
    unprotected = []
    for p in positions:
        stop = p["stop"]
        if stop is None:
            p["risk_at_stop"] = None
            unprotected.append(p["symbol"])
        else:
            p["risk_at_stop"] = round(max(0.0, p["qty"] * (p["avg_price"] - stop)), 2)
    _value_positions(positions, marks, now_ms)
    open_risk = sum(p["risk"] for p in positions if p["risk"] is not None)
    exposure = sum(p["value"] for p in positions)
    unrealized_total = sum(p["unrealized_pnl"] for p in positions if p["unrealized_pnl"] is not None)
    for p in positions:
        p["weight_pct"] = round(p["value"] / exposure * 100, 1) if exposure > _EPS else None
        p["cost"] = round(p["cost"], 2)
        p["avg_price"] = round(p["avg_price"], 8)
        del p["value"]
    equity = None if capital is None else capital + realized_total + unrealized_total
    free = None if capital is None else capital + realized_total - cost_total
    n_missing = sum(1 for p in positions if p["mark_status"] == "missing")
    n_stale = sum(1 for p in positions if p["mark_status"] == "stale")
    marked_all = bool(positions) and n_missing == 0
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
    breached = [p["symbol"] for p in positions if p["stop_breached"]]
    if breached:
        warnings.append(
            f"Цена не выше записанного стопа: {', '.join(breached)}. Либо стоп уже сработал, а продажа не записана "
            "в журнал, либо исполнение хуже плана — сверьте журнал с брокером."
        )
    big = max(positions, key=lambda p: p["weight_pct"] or 0, default=None)
    if big and len(positions) > 1 and (big["weight_pct"] or 0) > CONCENTRATION_WARN_PCT:
        warnings.append(
            f"Концентрация: {big['symbol']} — {big['weight_pct']:g}% стоимости позиций. Одна бумага определяет "
            "почти весь результат счёта."
        )
    if n_stale:
        warnings.append(f"Цены устарели у {n_stale} поз.: нереализованный результат и риск могут быть неточными.")
    if n_missing:
        warnings.append(f"Нет рыночной цены у {n_missing} поз.: они оценены по цене входа, нереализованный результат неизвестен.")
    if capital is None:
        warnings.append("Капитал счёта не задан: доли и свободные средства посчитать нельзя.")
    if not positions:
        truth_status = "ok"
    elif n_missing == len(positions):
        truth_status = "unmarked"
    elif n_missing or n_stale:
        truth_status = "partial"
    else:
        truth_status = "ok"
    last_entry = max((e.ts for e in entries), default=None)
    out = {
        "market": account.market, "name": account.name, "currency": account.currency,
        "capital": capital, "equity": None if equity is None else round(equity, 2),
        "equity_complete": marked_all and n_stale == 0 if positions else True,
        "free": None if free is None else round(free, 2),
        "realized_total": round(realized_total, 2), "unrealized_total": round(unrealized_total, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_limit": None if daily_limit is None else round(daily_limit, 2), "daily_limit_breached": daily_breached,
        "entries_today": day_entries, "max_trades_per_day": account.max_trades_per_day, "trades_limit_reached": trades_breached,
        "exposure": round(exposure, 2), "exposure_pct": pct(exposure), "exposure_cost": round(cost_total, 2),
        "open_risk": round(open_risk, 2), "heat_pct": heat_pct, "max_open_risk_pct": account.max_open_risk_pct,
        "positions": positions, "unprotected": unprotected, "warnings": warnings,
        "truth": {
            "status": truth_status,
            "sources": {
                "journal": {"entries": len(entries), "last_entry_ts": last_entry},
                "market": {"marked": len(positions) - n_missing - n_stale, "stale": n_stale, "missing": n_missing},
                "broker": {"connected": False},
            },
            "reconciliation": reconcile(positions, broker) if broker is not None
            else {"status": "not_connected", "differences": []},
        },
        "notes": [
            "Риск по стопу — расчётный сценарий, а не гарантированный максимум потерь.",
            "Дневной результат — только зафиксированный продажами; нереализованное изменение в него не входит.",
            "Нереализованный результат — без учёта комиссии выхода и налогов.",
        ],
    }
    if n_missing:
        out["notes"].insert(0, "Часть позиций без рыночных цен: они оценены по цене входа.")
    return out


def reconcile(
    compass_positions: list[dict[str, Any]], broker: list[dict[str, Any]],
    qty_tol: float = QTY_TOL, price_tol_pct: float = PRICE_TOL_PCT,
) -> dict[str, Any]:
    """Сверка позиций журнала с выпиской брокера. Расхождения показываются, а не сливаются: что правильно,
    решает пользователь. broker — [{symbol, qty, avg_price?}]; средняя цена сверяется, только если она есть."""
    ours = {p["symbol"]: p for p in compass_positions}
    theirs: dict[str, dict[str, Any]] = {}
    for b in broker:
        sym = str(b["symbol"])
        prev = theirs.get(sym)
        theirs[sym] = {**b, "qty": float(b["qty"]) + (float(prev["qty"]) if prev else 0.0)}
    diffs: list[dict[str, Any]] = []
    for sym in sorted(set(ours) | set(theirs)):
        o, b = ours.get(sym), theirs.get(sym)
        if o is None and b is not None:
            if b["qty"] > qty_tol:
                diffs.append({"symbol": sym, "kind": "missing_in_journal", "journal_qty": 0.0, "broker_qty": b["qty"],
                              "message": f"{sym}: у брокера {b['qty']:g}, в журнале позиции нет. Запишите сделку или проверьте выписку."})
            continue
        if b is None and o is not None:
            diffs.append({"symbol": sym, "kind": "missing_at_broker", "journal_qty": o["qty"], "broker_qty": 0.0,
                          "message": f"{sym}: в журнале {o['qty']:g}, у брокера позиции нет. Возможно, продажа не записана."})
            continue
        assert o is not None and b is not None
        if abs(o["qty"] - b["qty"]) > qty_tol:
            diffs.append({"symbol": sym, "kind": "qty_mismatch", "journal_qty": o["qty"], "broker_qty": b["qty"],
                          "message": f"{sym}: количество в журнале {o['qty']:g}, у брокера {b['qty']:g}."})
            continue
        bp = b.get("avg_price")
        if bp and o["avg_price"] and abs(o["avg_price"] / float(bp) - 1) * 100 > price_tol_pct:
            diffs.append({"symbol": sym, "kind": "price_mismatch", "journal_price": o["avg_price"], "broker_price": float(bp),
                          "message": f"{sym}: средняя цена в журнале {o['avg_price']:g}, у брокера {float(bp):g} "
                                     "(комиссии и округление могут давать небольшую разницу)."})
    return {"status": "mismatch" if diffs else "match", "differences": diffs, "checked": len(set(ours) | set(theirs))}


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
