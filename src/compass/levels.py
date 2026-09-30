"""Сохранённые уровни цены: горизонтальные линии пользователя на графике инструмента.

Уровень — заметка пользователя («вчерашний максимум», «поддержка»), а не расчёт и не прогноз: приложение
не оценивает, удержится ли он. Удаление мягкое и обратимое."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, replace

from compass.db import Connection

MAX_PER_INSTRUMENT = 50


@dataclass(frozen=True, slots=True)
class Level:
    market: str
    symbol: str
    price: float
    label: str = ""
    id: int | None = None
    uid: str | None = None
    created_at: int | None = None
    deleted_at: int | None = None


_COLS = "market, symbol, price, label, id, uid, created_at, deleted_at"


def validate(lv: Level) -> None:
    if not math.isfinite(lv.price) or lv.price <= 0:
        raise ValueError("Цена уровня должна быть больше нуля")
    if len(lv.label) > 60:
        raise ValueError("Подпись уровня — не длиннее 60 символов")


class LevelStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def add(self, lv: Level) -> Level:
        validate(lv)
        lv = replace(lv, label=lv.label.strip(), uid=uuid.uuid4().hex, created_at=int(time.time()), deleted_at=None)
        with self._lock, self._conn:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM levels WHERE market=? AND symbol=? AND deleted_at IS NULL", (lv.market, lv.symbol)
            ).fetchone()
            if n >= MAX_PER_INSTRUMENT:
                raise ValueError(f"Уровней на инструмент — не больше {MAX_PER_INSTRUMENT}")
            cur = self._conn.execute(
                "INSERT INTO levels (market, symbol, price, label, uid, created_at) VALUES (?,?,?,?,?,?)",
                (lv.market, lv.symbol, lv.price, lv.label, lv.uid, lv.created_at),
            )
        return replace(lv, id=cur.lastrowid)

    def list(self, market: str, symbol: str, deleted: bool = False) -> list[Level]:
        cond = "IS NOT NULL" if deleted else "IS NULL"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLS} FROM levels WHERE market=? AND symbol=? AND deleted_at {cond} ORDER BY price DESC, id",
                (market, symbol),
            ).fetchall()
        return [Level(*r) for r in rows]

    def remove(self, level_id: int) -> bool:
        with self._lock, self._conn:
            return (
                self._conn.execute(
                    "UPDATE levels SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (int(time.time()), level_id)
                ).rowcount
                == 1
            )

    def restore(self, level_id: int) -> bool:
        with self._lock, self._conn:
            return (
                self._conn.execute(
                    "UPDATE levels SET deleted_at=NULL WHERE id=? AND deleted_at IS NOT NULL", (level_id,)
                ).rowcount
                == 1
            )
