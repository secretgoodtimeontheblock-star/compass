"""Кэш свечей в SQLite поверх адаптеров рынков.

Правила:
- холодный старт (в кэше меньше `limit` свечей) — тянем `limit` последних;
- иначе докачиваем с последней сохранённой свечи (она могла быть незакрытой,
  поэтому перезаписывается, а не пропускается);
- не чаще одного обращения к источнику на (рынок, тикер, таймфрейм) за
  `min_refresh_s` — панель графика не должна долбить МосБиржу/биржу;
- источник упал, а кэш есть — отдаём кэш с пометкой `stale=True`, а не ошибку.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass

from compass.markets.base import MarketAdapter, MarketError
from compass.models import Candle


@dataclass(frozen=True, slots=True)
class CandlesResult:
    candles: list[Candle]
    stale: bool  # True — источник недоступен, показан кэш


class CandleCache:
    def __init__(
        self,
        conn: sqlite3.Connection,
        adapters: dict[str, MarketAdapter],
        min_refresh_s: float = 30.0,
    ) -> None:
        self._conn = conn
        self._adapters = adapters
        self._min_refresh_s = min_refresh_s
        self._lock = threading.Lock()  # общее sqlite-соединение на пул потоков

    def get(self, market: str, symbol: str, tf: str, limit: int = 500) -> CandlesResult:
        adapter = self._adapters.get(market)
        if adapter is None:
            raise MarketError(f"Неизвестный рынок: {market}")
        if tf not in adapter.timeframes:
            raise MarketError(f"Таймфрейм {tf} недоступен для рынка {market}")

        with self._lock:
            count, last_ts = self._state(market, symbol, tf)
            fetched_at = self._fetched_at(market, symbol, tf)
        fresh = fetched_at is not None and time.time() - fetched_at < self._min_refresh_s
        stale = False
        if not (fresh and count >= limit):
            since = last_ts if count >= limit else None
            try:
                got = adapter.fetch_candles(symbol, tf, since, limit)
            except MarketError:
                if count == 0:
                    raise
                stale = True
            else:
                with self._lock:
                    self._upsert(market, symbol, tf, got)
        with self._lock:
            return CandlesResult(self._read(market, symbol, tf, limit), stale)

    # --- SQL (вызывать только под self._lock) ---

    def _state(self, market: str, symbol: str, tf: str) -> tuple[int, int | None]:
        row = self._conn.execute(
            "SELECT COUNT(*), MAX(ts) FROM candles WHERE market=? AND symbol=? AND tf=?",
            (market, symbol, tf),
        ).fetchone()
        return row[0], row[1]

    def _fetched_at(self, market: str, symbol: str, tf: str) -> int | None:
        row = self._conn.execute(
            "SELECT fetched_at FROM fetch_log WHERE market=? AND symbol=? AND tf=?",
            (market, symbol, tf),
        ).fetchone()
        return row[0] if row else None

    def _upsert(self, market: str, symbol: str, tf: str, candles: list[Candle]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                [(market, symbol, tf, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?)",
                (market, symbol, tf, int(time.time())),
            )

    def _read(self, market: str, symbol: str, tf: str, limit: int) -> list[Candle]:
        rows = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE market=? AND symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
            (market, symbol, tf, limit),
        ).fetchall()
        return [Candle(*r) for r in reversed(rows)]
