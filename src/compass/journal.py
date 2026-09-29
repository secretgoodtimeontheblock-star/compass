"""Журнал сделок: ручные записи пользователя (что он реально купил и продал).
Приложение ничего не исполняет — журнал нужен, чтобы видеть свою позицию,
результат и потом разбирать ошибки.

Учёт — по средней цене: покупка увеличивает позицию и себестоимость, продажа
фиксирует прибыль относительно средней цены. Комиссия входит в себестоимость
покупки и вычитается из выручки продажи. Валюты не смешиваются: сводка идёт
по каждому тикеру отдельно (рубли МосБиржи и USDT крипты не складываются).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Entry:
    market: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    ts: int  # мс UTC — когда сделка совершена
    fee: float = 0.0
    note: str = ""
    signal_id: int | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Position:
    market: str
    symbol: str
    qty: float
    avg_price: float | None  # None — позиции нет
    realized_pnl: float
    fees: float
    trades: int


_EPS = 1e-9


def summarize(entries: list[Entry]) -> list[Position]:
    """Сводка по тикерам. Бросает ValueError, если где-то продаётся больше, чем куплено."""
    by_symbol: dict[tuple[str, str], list[Entry]] = {}
    for e in entries:
        by_symbol.setdefault((e.market, e.symbol), []).append(e)

    out: list[Position] = []
    for (market, symbol), items in sorted(by_symbol.items()):
        qty = cost = realized = fees = 0.0
        # при равных ts покупка раньше продажи: иначе сделки «в одну секунду» ломают учёт
        for e in sorted(items, key=lambda x: (x.ts, x.side != "buy", x.id or 0)):
            fees += e.fee
            if e.side == "buy":
                qty += e.qty
                cost += e.qty * e.price + e.fee
            else:
                if e.qty > qty + _EPS:
                    raise ValueError(
                        f"{symbol}: продажа {e.qty:g} больше позиции {qty:g} на момент сделки"
                    )
                avg = cost / qty
                realized += e.qty * (e.price - avg) - e.fee
                cost -= e.qty * avg
                qty -= e.qty
                if qty < _EPS:
                    qty, cost = 0.0, 0.0
        out.append(
            Position(
                market,
                symbol,
                round(qty, 10),
                round(cost / qty, 8) if qty > 0 else None,
                round(realized, 2),
                round(fees, 2),
                len(items),
            )
        )
    return out


def validate(e: Entry) -> None:
    if e.side not in ("buy", "sell"):
        raise ValueError("Сторона сделки — buy или sell")
    if not (e.qty > 0 and e.price > 0):
        raise ValueError("Количество и цена должны быть больше нуля")
    if e.fee < 0:
        raise ValueError("Комиссия не может быть отрицательной")
    if e.ts <= 0:
        raise ValueError("Некорректное время сделки")
    if len(e.note) > 2000:
        raise ValueError("Заметка слишком длинная (максимум 2000 символов)")


class Journal:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def list(self, market: str | None = None, symbol: str | None = None) -> list[Entry]:
        sql = (
            "SELECT market, symbol, side, qty, price, ts, fee, note, signal_id, id FROM journal"
        )
        where, args = [], []
        if market:
            where.append("market = ?")
            args.append(market)
        if symbol:
            where.append("symbol = ?")
            args.append(symbol)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC"
        with self._lock:
            return [Entry(*r) for r in self._conn.execute(sql, args).fetchall()]

    def positions(self) -> list[Position]:
        return summarize(self.list())

    def add(self, e: Entry) -> Entry:
        validate(e)
        with self._lock, self._conn:
            # проверяем всю историю тикера вместе с новой записью, ДО вставки
            same = [x for x in self._all() if (x.market, x.symbol) == (e.market, e.symbol)]
            summarize([*same, e])
            cur = self._conn.execute(
                "INSERT INTO journal (market, symbol, side, qty, price, ts, fee, note, signal_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (e.market, e.symbol, e.side, e.qty, e.price, e.ts, e.fee, e.note, e.signal_id, int(time.time())),
            )
        return replace(e, id=cur.lastrowid)

    def remove(self, entry_id: int) -> bool:
        with self._lock, self._conn:
            rest = [x for x in self._all() if x.id != entry_id]
            if len(rest) == len(self._all()):
                return False
            # удаление покупки может оставить последующую продажу без позиции
            summarize(rest)
            self._conn.execute("DELETE FROM journal WHERE id = ?", (entry_id,))
        return True

    def _all(self) -> list[Entry]:
        rows = self._conn.execute(
            "SELECT market, symbol, side, qty, price, ts, fee, note, signal_id, id FROM journal"
        ).fetchall()
        return [Entry(*r) for r in rows]
