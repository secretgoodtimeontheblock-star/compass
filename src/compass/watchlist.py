"""Вотчлист: список тикеров, за которыми следим (по обоим рынкам)."""

from __future__ import annotations

import sqlite3
import threading
import time

from compass.models import Instrument


class Watchlist:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def list(self) -> list[Instrument]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, name, market FROM watchlist ORDER BY added_at, symbol"
            ).fetchall()
        return [Instrument(*r) for r in rows]

    def add(self, inst: Instrument) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO watchlist VALUES (?,?,?,?)",
                (inst.market, inst.symbol, inst.name, int(time.time())),
            )

    def remove(self, market: str, symbol: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM watchlist WHERE market=? AND symbol=?", (market, symbol)
            )
        return cur.rowcount > 0
