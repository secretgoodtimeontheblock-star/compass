"""Журнал сделок: ручные записи пользователя (что он реально купил и продал).
Приложение ничего не исполняет — журнал нужен, чтобы видеть свою позицию,
результат и потом разбирать ошибки.

Сделки бывают трёх режимов: реальные (`real`), учебные (`paper`) и исторические
(`historical`, разбор старой или чужой истории). Позиции и статистика считаются по каждому
режиму отдельно и не смешиваются: учебная сделка не должна влиять на реальный результат.
Удаление мягкое: запись помечается удалённой и может быть возвращена.

Учёт — по средней цене: покупка увеличивает позицию и себестоимость, продажа
фиксирует прибыль относительно средней цены. Комиссия входит в себестоимость
покупки и вычитается из выручки продажи. Валюты не смешиваются: сводка идёт
по каждому тикеру отдельно (рубли МосБиржи и USDT крипты не складываются).
"""

from __future__ import annotations

import csv
import io
import time
import uuid
from dataclasses import dataclass, replace

from compass.db import Connection

BACKUP_FORMAT = "compass-journal"
BACKUP_VERSION = 1
MODES = ("real", "paper", "historical")
_COLUMNS = (
    "market, symbol, side, qty, price, ts, fee, note, signal_id, planned_stop, reason, id, mode, uid, deleted_at"
)
_EPS = 1e-9


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
    planned_stop: float | None = None
    reason: str = ""
    id: int | None = None
    mode: str = "real"  # real | paper | historical
    uid: str | None = None  # стабильный идентификатор: по нему копия восстанавливается без дублей
    deleted_at: int | None = None  # секунды UTC; None — запись действует


@dataclass(frozen=True, slots=True)
class Position:
    market: str
    symbol: str
    qty: float
    avg_price: float | None  # None — позиции нет
    realized_pnl: float
    fees: float
    trades: int
    mode: str = "real"


def summarize(entries: list[Entry]) -> list[Position]:
    """Сводка по режиму и тикеру. Бросает ValueError, если где-то продаётся больше, чем куплено."""
    by_symbol: dict[tuple[str, str, str], list[Entry]] = {}
    for e in entries:
        by_symbol.setdefault((e.mode, e.market, e.symbol), []).append(e)

    out: list[Position] = []
    for (mode, market, symbol), items in sorted(by_symbol.items()):
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
                mode,
            )
        )
    return out


def validate(e: Entry) -> None:
    if e.mode not in MODES:
        raise ValueError("Режим сделки — real, paper или historical")
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
    if len(e.reason) > 500:
        raise ValueError("Причина слишком длинная (максимум 500 символов)")
    if e.planned_stop is not None and not (e.planned_stop > 0):
        raise ValueError("Плановый стоп должен быть больше нуля")


class Journal:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def list(self, market: str | None = None, symbol: str | None = None, mode: str | None = "real") -> list[Entry]:
        """Действующие записи. По умолчанию только реальные; mode=None — всех режимов."""
        where, args = ["deleted_at IS NULL"], []
        if market:
            where.append("market = ?")
            args.append(market)
        if symbol:
            where.append("symbol = ?")
            args.append(symbol)
        if mode:
            where.append("mode = ?")
            args.append(mode)
        sql = f"SELECT {_COLUMNS} FROM journal WHERE {' AND '.join(where)} ORDER BY ts DESC, id DESC"
        with self._lock:
            return [Entry(*r) for r in self._conn.execute(sql, args).fetchall()]

    def deleted(self) -> list[Entry]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM journal WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, id DESC"
            ).fetchall()
        return [Entry(*r) for r in rows]

    def positions(self, mode: str | None = "real") -> list[Position]:
        return summarize(self.list(mode=mode))

    def add(self, e: Entry) -> Entry:
        validate(e)
        e = replace(e, uid=e.uid or uuid.uuid4().hex, deleted_at=None)
        with self._lock, self._conn:
            # проверяем всю историю тикера вместе с новой записью, ДО вставки
            summarize([*self._active(e.mode, e.market, e.symbol), e])
            cur = self._insert(e)
        return replace(e, id=cur.lastrowid)

    def remove(self, entry_id: int) -> bool:
        """Мягкое удаление: запись остаётся в базе и возвращается через restore()."""
        with self._lock, self._conn:
            target = self._get(entry_id, deleted=False)
            if target is None:
                return False
            # удаление покупки может оставить последующую продажу без позиции
            summarize([x for x in self._active(target.mode, target.market, target.symbol) if x.id != entry_id])
            self._conn.execute("UPDATE journal SET deleted_at = ? WHERE id = ?", (int(time.time()), entry_id))
        return True

    def restore(self, entry_id: int) -> bool:
        with self._lock, self._conn:
            target = self._get(entry_id, deleted=True)
            if target is None:
                return False
            summarize([*self._active(target.mode, target.market, target.symbol), replace(target, deleted_at=None)])
            self._conn.execute("UPDATE journal SET deleted_at = NULL WHERE id = ?", (entry_id,))
        return True

    # --- резервная копия ---

    def backup(self) -> dict:
        """Полная копия журнала: все режимы и удалённые записи."""
        with self._lock:
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM journal ORDER BY ts, id").fetchall()
        entries = []
        for r in rows:
            e = Entry(*r)
            entries.append({k: getattr(e, k) for k in Entry.__dataclass_fields__ if k != "id"})
        return {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": int(time.time()), "entries": entries}

    def restore_backup(self, payload: dict) -> dict:
        """Добавляет записи, которых ещё нет (по uid); существующие не трогает — повторное
        восстановление ничего не дублирует. Всё или ничего: если итоговые позиции не сходятся,
        не сохраняется ни одна запись."""
        if payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_VERSION:
            raise ValueError("Это не резервная копия журнала Compass или её версия не поддерживается")
        raw = payload.get("entries")
        if not isinstance(raw, list):
            raise ValueError("В резервной копии нет списка записей")  # noqa: TRY004
        fields = set(Entry.__dataclass_fields__) - {"id"}
        incoming: list[Entry] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("uid") or set(item) - fields:
                raise ValueError("Запись резервной копии повреждена")
            e = Entry(**item)
            validate(e)
            incoming.append(e)
        with self._lock, self._conn:
            known = {r[0] for r in self._conn.execute("SELECT uid FROM journal").fetchall()}
            fresh = [e for e in incoming if e.uid not in known]
            if len({e.uid for e in fresh}) != len(fresh):
                raise ValueError("В резервной копии повторяются идентификаторы записей")
            for mode, market, symbol in {(e.mode, e.market, e.symbol) for e in fresh}:
                mine = [
                    e for e in fresh
                    if (e.mode, e.market, e.symbol) == (mode, market, symbol) and e.deleted_at is None
                ]
                summarize([*self._active(mode, market, symbol), *mine])
            for e in fresh:
                self._insert(e)
        return {"added": len(fresh), "skipped": len(incoming) - len(fresh)}

    def to_csv(self) -> str:
        """Хронологический экспорт действующих записей всех режимов. BOM — чтобы Excel открыл кириллицу."""
        buf = io.StringIO()
        buf.write("﻿")
        writer = csv.writer(buf)
        writer.writerow(
            [
                "market", "symbol", "side", "qty", "price", "fee", "ts_ms", "reason", "planned_stop",
                "note", "signal_id", "mode", "uid",
            ]
        )
        for e in sorted(self.list(mode=None), key=lambda x: (x.ts, x.id or 0)):
            writer.writerow(
                [
                    e.market, e.symbol, e.side, e.qty, e.price, e.fee, e.ts, e.reason,
                    "" if e.planned_stop is None else e.planned_stop, e.note,
                    "" if e.signal_id is None else e.signal_id, e.mode, e.uid or "",
                ]
            )
        return buf.getvalue()

    # --- внутреннее (вызывать под блокировкой) ---

    def _insert(self, e: Entry):
        return self._conn.execute(
            "INSERT INTO journal (market, symbol, side, qty, price, ts, fee, note, signal_id, "
            "planned_stop, reason, mode, uid, deleted_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                e.market, e.symbol, e.side, e.qty, e.price, e.ts, e.fee, e.note, e.signal_id, e.planned_stop,
                e.reason, e.mode, e.uid, e.deleted_at, int(time.time()),
            ),
        )

    def _active(self, mode: str, market: str, symbol: str) -> list[Entry]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM journal WHERE deleted_at IS NULL AND mode = ? AND market = ? AND symbol = ?",
            (mode, market, symbol),
        ).fetchall()
        return [Entry(*r) for r in rows]

    def _get(self, entry_id: int, deleted: bool) -> Entry | None:
        cond = "IS NOT NULL" if deleted else "IS NULL"
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM journal WHERE id = ? AND deleted_at {cond}", (entry_id,)
        ).fetchone()
        return Entry(*row) if row else None
