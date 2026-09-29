"""План сделки — неизменяемый снимок того, что пользователь решил ДО входа.

В плане фиксируются инструмент, источник данных, версия стратегии, вход, стоп, цель, расходы,
количество и причина. Числа считает код (тот же расчёт, что и в /api/risk). Изменить или удалить
план нельзя (триггеры в БД): разбор «план/факт» честен только пока план остался таким, каким
был написан. Хотите другой план — создайте новый.

Статус плана не хранится: он выводится из связанных записей журнала (нет записей — план открыт,
есть покупки — вход выполнен, проданы все — закрыт).
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

from compass.db import Connection
from compass.journal import Entry

_COLUMNS = (
    "id, uid, created_at, market, symbol, source, strategy, strategy_version, params, signal_id, entry, stop, "
    "target, qty, lots, capital, risk_pct, available, fee_pct, slippage_pct, cost, risk_amount, "
    "risk_amount_worse, budget, currency, unit_value, reward_risk, reason, warnings"
)


@dataclass(frozen=True, slots=True)
class Plan:
    market: str
    symbol: str
    source: str  # идентификатор источника данных: moex:iss, crypto:okx
    entry: float
    stop: float
    qty: float
    lots: float
    capital: float
    risk_pct: float
    fee_pct: float
    slippage_pct: float
    cost: float
    risk_amount: float
    risk_amount_worse: float
    budget: float
    target: float | None = None
    strategy: str | None = None  # None — ручной план без стратегии
    strategy_version: str | None = None
    params: dict[str, int] | None = None
    signal_id: int | None = None
    available: float | None = None
    currency: str | None = None
    unit_value: float = 1.0  # деньги за единицу цены на бумагу (облигации: номинал/100)
    reward_risk: float | None = None
    reason: str = ""
    warnings: tuple[str, ...] = ()
    id: int | None = None
    uid: str | None = None
    created_at: int | None = None


def validate(p: Plan) -> None:
    nums = (p.entry, p.stop, p.qty, p.capital, p.cost, p.risk_amount, p.budget)
    if not all(math.isfinite(x) for x in nums):
        raise ValueError("В плане должны быть конечные числа")
    if p.stop <= 0 or p.entry <= p.stop:
        raise ValueError("Стоп должен быть ниже входа и больше нуля (только лонг)")
    if p.target is not None and p.target <= p.entry:
        raise ValueError("Цель должна быть выше входа")
    if p.qty <= 0:
        raise ValueError("В плане нулевое количество: покупать нечего")
    if len(p.reason) > 500:
        raise ValueError("Причина слишком длинная (максимум 500 символов)")
    if not p.reason.strip():
        raise ValueError("Укажите причину входа: план без причины нечем сверять")


class PlanStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def add(self, p: Plan) -> Plan:
        validate(p)
        p = replace(p, uid=p.uid or uuid.uuid4().hex, created_at=p.created_at or int(time.time()))
        with self._lock, self._conn:
            cur = self._insert(p)
        return replace(p, id=cur.lastrowid)

    def get(self, plan_id: int) -> Plan | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return _row(row) if row else None

    def get_by_uid(self, uid: str) -> Plan | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM plans WHERE uid = ?", (uid,)).fetchone()
        return _row(row) if row else None

    def list(self, market: str | None = None, symbol: str | None = None, limit: int = 100) -> list[Plan]:
        where, args = [], []
        if market:
            where.append("market = ?")
            args.append(market)
        if symbol:
            where.append("symbol = ?")
            args.append(symbol)
        sql = f"SELECT {_COLUMNS} FROM plans" + (f" WHERE {' AND '.join(where)}" if where else "")
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [_row(r) for r in rows]

    # --- резервная копия ---

    def export(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM plans ORDER BY id").fetchall()
        return [{k: v for k, v in _dict(_row(r)).items() if k != "id"} for r in rows]

    def check_import(self, items: list[dict[str, Any]]) -> list[Plan]:
        """Разбирает и проверяет планы из копии, ничего не записывая."""
        fields = set(Plan.__dataclass_fields__) - {"id"}
        out: list[Plan] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("uid") or set(item) - fields:
                raise ValueError("План в резервной копии повреждён")
            p = Plan(**{**item, "warnings": tuple(item.get("warnings") or ())})
            validate(p)
            out.append(p)
        return out

    def import_new(self, plans: list[Plan]) -> tuple[int, int]:
        """Вызывать под блокировкой соединения внутри общей транзакции. Известные uid пропускает."""
        known = {r[0] for r in self._conn.execute("SELECT uid FROM plans").fetchall()}
        fresh = [p for p in plans if p.uid not in known]
        if len({p.uid for p in fresh}) != len(fresh):
            raise ValueError("В резервной копии повторяются идентификаторы планов")
        for p in fresh:
            self._insert(p)
        return len(fresh), len(plans) - len(fresh)

    def _insert(self, p: Plan):
        return self._conn.execute(
            "INSERT INTO plans (uid, created_at, market, symbol, source, strategy, strategy_version, params, "
            "signal_id, entry, stop, target, qty, lots, capital, risk_pct, available, fee_pct, slippage_pct, cost, "
            "risk_amount, risk_amount_worse, budget, currency, unit_value, reward_risk, reason, warnings) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                p.uid, p.created_at, p.market, p.symbol, p.source, p.strategy, p.strategy_version,
                None if p.params is None else json.dumps(p.params, sort_keys=True), p.signal_id, p.entry, p.stop,
                p.target, p.qty, p.lots, p.capital, p.risk_pct, p.available, p.fee_pct, p.slippage_pct, p.cost,
                p.risk_amount, p.risk_amount_worse, p.budget, p.currency, p.unit_value, p.reward_risk, p.reason,
                json.dumps(list(p.warnings), ensure_ascii=False),
            ),
        )


def _row(r: tuple) -> Plan:
    (id_, uid, created_at, market, symbol, source, strategy, version, params, signal_id, entry, stop, target, qty,
     lots, capital, risk_pct, available, fee_pct, slip_pct, cost, risk_amount, risk_worse, budget, currency,
     unit_value, reward_risk, reason, warnings) = r
    return Plan(
        market, symbol, source, entry, stop, qty, lots, capital, risk_pct, fee_pct, slip_pct, cost, risk_amount,
        risk_worse, budget, target, strategy, version, json.loads(params) if params else None, signal_id,
        available, currency, unit_value, reward_risk, reason, tuple(json.loads(warnings or "[]")), id_, uid,
        created_at,
    )


def _dict(p: Plan) -> dict[str, Any]:
    d = {k: getattr(p, k) for k in Plan.__dataclass_fields__}
    d["warnings"] = list(p.warnings)
    return d


def plan_dto(p: Plan) -> dict[str, Any]:
    return _dict(p)


def review(plan: Plan, entries: list[Entry]) -> dict[str, Any]:
    """Сравнение плана с фактом. Только записанные пользователем сделки и числа плана: ничего не
    додумывается. Результат считается по ценам без комиссий — их видно отдельной строкой."""
    linked = [e for e in entries if e.plan_uid == plan.uid and e.deleted_at is None]
    buys = [e for e in linked if e.side == "buy"]
    sells = [e for e in linked if e.side == "sell"]
    bought = sum(e.qty for e in buys)
    sold = sum(e.qty for e in sells)
    fees = sum(e.fee for e in linked)
    out: dict[str, Any] = {
        "plan_id": plan.id,
        "status": "open" if not buys else ("closed" if sold >= bought - 1e-9 else "entered"),
        "bought_qty": bought,
        "sold_qty": sold,
        "fees": round(fees, 2),
        "deviations": [],
        "entries": len(linked),
    }
    if not buys:
        return out
    avg_entry = sum(e.qty * e.price for e in buys) / bought
    out["avg_entry"] = round(avg_entry, 8)
    devs: list[dict[str, Any]] = out["deviations"]

    entry_dev = (avg_entry / plan.entry - 1) * 100
    devs.append({
        "code": "entry", "label": "Цена входа", "planned": plan.entry, "actual": round(avg_entry, 8),
        "diff_pct": round(entry_dev, 3), "worse": avg_entry > plan.entry,
    })
    qty_dev = (bought / plan.qty - 1) * 100
    devs.append({
        "code": "qty", "label": "Количество", "planned": plan.qty, "actual": bought,
        "diff_pct": round(qty_dev, 2), "worse": bought > plan.qty * 1.0000001,  # больше плана — больше риска
    })
    stops = [e.planned_stop for e in buys if e.planned_stop is not None]
    used_stop = None
    if not stops:
        devs.append({"code": "stop", "label": "Стоп", "planned": plan.stop, "actual": None, "diff_pct": None,
                     "worse": True})
    else:
        used_stop = min(stops)  # самый далёкий стоп из записанных — консервативно для оценки риска
        devs.append({
            "code": "stop", "label": "Стоп", "planned": plan.stop, "actual": used_stop,
            "diff_pct": round((used_stop / plan.stop - 1) * 100, 3), "worse": used_stop < plan.stop,
        })
    risk_stop = used_stop if used_stop is not None else plan.stop
    actual_risk = bought * (avg_entry - risk_stop) * plan.unit_value
    out["actual_risk_at_stop"] = round(actual_risk, 2)
    out["risk_budget"] = plan.budget
    out["risk_exceeded"] = actual_risk > plan.budget * 1.0000001
    if sells:
        avg_exit = sum(e.qty * e.price for e in sells) / sold
        out["avg_exit"] = round(avg_exit, 8)
        out["result_pct"] = round((avg_exit / avg_entry - 1) * 100, 3)
        risk_per_unit = avg_entry - risk_stop
        # R — доля первоначального риска на единицу; считается по записанному стопу
        out["r_multiple"] = round((avg_exit - avg_entry) / risk_per_unit, 2) if risk_per_unit > 0 else None
        out["planned_reward_risk"] = plan.reward_risk
    out["notes"] = [
        "Результат — по ценам сделок без комиссий; комиссии указаны отдельно.",
        "Фактический риск по стопу считается от записанной цены входа и стопа, не от плана.",
    ]
    return out
