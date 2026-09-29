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

import time
from dataclasses import dataclass

from compass.db import Connection
from compass.markets.base import MarketAdapter, MarketError
from compass.models import Candle


@dataclass(frozen=True, slots=True)
class CandlesResult:
    candles: list[Candle]
    stale: bool  # True — источник недоступен, показан кэш
    fetched_at: int | None = None  # секунды UTC последнего успешного REST-обновления
    source: str = ""


class CandleCache:
    def __init__(
        self,
        conn: Connection,
        adapters: dict[str, MarketAdapter],
        min_refresh_s: float = 30.0,
    ) -> None:
        self._conn = conn
        self._adapters = adapters
        self._min_refresh_s = min_refresh_s
        self._lock = conn.lock

    def get(self, market: str, symbol: str, tf: str, limit: int = 500, *, refresh: bool = False) -> CandlesResult:
        adapter = self._adapters.get(market)
        if adapter is None:
            raise MarketError(f"Неизвестный рынок: {market}")
        if tf not in adapter.timeframes:
            raise MarketError(f"Таймфрейм {tf} недоступен для рынка {market}")

        source = getattr(adapter, "source_id", market)
        with self._lock:
            count, last_ts = self._state(source, market, symbol, tf)
            fetched_at = self._fetched_at(source, market, symbol, tf)
        fresh = fetched_at is not None and time.time() - fetched_at < self._min_refresh_s
        stale = False
        if refresh or not (fresh and count >= limit):
            since = last_ts if count >= limit and not refresh else None
            try:
                got = adapter.fetch_candles(symbol, tf, since, limit)
            except MarketError:
                if count == 0:
                    raise
                stale = True
            else:
                with self._lock:
                    self._upsert(source, market, symbol, tf, got)
        with self._lock:
            return CandlesResult(
                self._read(source, market, symbol, tf, limit), stale,
                self._fetched_at(source, market, symbol, tf), source,
            )

    # --- SQL (вызывать только под self._lock) ---

    def _state(self, source: str, market: str, symbol: str, tf: str) -> tuple[int, int | None]:
        row = self._conn.execute(
            "SELECT COUNT(*), MAX(ts) FROM candles WHERE source=? AND market=? AND symbol=? AND tf=?",
            (source, market, symbol, tf),
        ).fetchone()
        return row[0], row[1]

    def _fetched_at(self, source: str, market: str, symbol: str, tf: str) -> int | None:
        row = self._conn.execute(
            "SELECT fetched_at FROM fetch_log WHERE source=? AND market=? AND symbol=? AND tf=?",
            (source, market, symbol, tf),
        ).fetchone()
        return row[0] if row else None

    def _upsert(self, source: str, market: str, symbol: str, tf: str, candles: list[Candle]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(source, market, symbol, tf, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?)",
                (source, market, symbol, tf, int(time.time())),
            )

    def _read(self, source: str, market: str, symbol: str, tf: str, limit: int) -> list[Candle]:
        rows = self._conn.execute(
            "SELECT ts, open, high, low, close, volume FROM candles "
            "WHERE source=? AND market=? AND symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
            (source, market, symbol, tf, limit),
        ).fetchall()
        return [Candle(*r) for r in reversed(rows)]
