"""Поиск сигналов по вотчлисту.

Сигнал — это СМЕНА целевой позиции стратегии на последней ЗАКРЫТОЙ свече
(0→1 вход, 1→0 выход). Незакрытая свеча игнорируется: по ней стратегия ещё
может передумать. Один и тот же сигнал на одной свече хранится один раз
(UNIQUE в БД) — повторный скан ничего не дублирует и не шлёт повторно.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from compass.cache import CandleCache
from compass.indicators import atr
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS
from compass.settings import Settings
from compass.strategies import STRATEGIES, candles_to_df
from compass.watchlist import Watchlist

log = logging.getLogger("compass.signals")

ATR_STOP_MULT = 2.0  # ориентир стопа: цена входа − 2·ATR(14)
HISTORY = 300  # свечей для расчёта — с запасом над прогревом любой стратегии


@dataclass(frozen=True, slots=True)
class Signal:
    market: str
    symbol: str
    tf: str
    strategy: str
    side: str  # "buy" | "exit"
    candle_ts: int
    price: float
    stop: float | None = None
    id: int | None = None
    created_at: int | None = None
    seen: bool = False


@dataclass(frozen=True, slots=True)
class ScanResult:
    new: list[Signal]
    errors: list[str]


class SignalStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def insert(self, s: Signal) -> bool:
        """True — сигнал новый; False — такой уже был."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO signals "
                "(market, symbol, tf, strategy, side, candle_ts, price, stop, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (s.market, s.symbol, s.tf, s.strategy, s.side, s.candle_ts, s.price, s.stop, int(time.time())),
            )
        return cur.rowcount == 1

    def list(
        self,
        limit: int = 100,
        unseen_only: bool = False,
        market: str | None = None,
        symbol: str | None = None,
    ) -> list[Signal]:
        where, args = [], []
        if unseen_only:
            where.append("seen = 0")
        if market:
            where.append("market = ?")
            args.append(market)
        if symbol:
            where.append("symbol = ?")
            args.append(symbol)
        sql = (
            "SELECT market, symbol, tf, strategy, side, candle_ts, price, stop, id, created_at, seen "
            "FROM signals " + (f"WHERE {' AND '.join(where)} " if where else "") + "ORDER BY id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [Signal(*r[:10], seen=bool(r[10])) for r in rows]

    def get(self, signal_id: int) -> Signal | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT market, symbol, tf, strategy, side, candle_ts, price, stop, id, created_at, seen "
                "FROM signals WHERE id = ?",
                (signal_id,),
            ).fetchone()
        return Signal(*row[:10], seen=bool(row[10])) if row else None

    def mark_all_seen(self) -> int:
        with self._lock, self._conn:
            return self._conn.execute("UPDATE signals SET seen = 1 WHERE seen = 0").rowcount


class SignalEngine:
    def __init__(
        self,
        cache: CandleCache,
        watchlist: Watchlist,
        settings: Settings,
        store: SignalStore,
        notifier,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self._cache, self._watchlist, self._settings = cache, watchlist, settings
        self._store, self._notifier, self._now_ms = store, notifier, now_ms
        self._scan_lock = threading.Lock()  # ручной скан и фоновый не должны идти одновременно

    def scan(self) -> ScanResult:
        with self._scan_lock:
            return self._scan()

    def _scan(self) -> ScanResult:
        cfg = self._settings.all()
        new: list[Signal] = []
        errors: list[str] = []
        for inst in self._watchlist.list():
            tf = cfg.get(f"tf_{inst.market}")
            if tf is None:
                continue
            try:
                candles = self._cache.get(inst.market, inst.symbol, tf, HISTORY).candles
            except MarketError as e:
                errors.append(f"{inst.symbol}: {e}")
                continue
            # свеча закрыта, только если её окно целиком в прошлом
            cutoff = self._now_ms() - TIMEFRAME_MS[tf]
            closed = [c for c in candles if c.ts <= cutoff]
            if len(closed) < 3:
                continue
            df = candles_to_df(closed)
            last_atr = atr(df, 14).iloc[-1]
            for strat in STRATEGIES.values():
                target = strat.target(df, strat.resolve())
                prev, cur = int(target.iloc[-2]), int(target.iloc[-1])
                if prev == cur:
                    continue
                price = float(df["close"].iloc[-1])
                side = "buy" if cur == 1 else "exit"
                stop = None
                if side == "buy" and not math.isnan(last_atr):
                    stop = round(price - ATR_STOP_MULT * float(last_atr), 8)
                    if stop <= 0:
                        stop = None
                sig = Signal(inst.market, inst.symbol, tf, strat.id, side, int(df["ts"].iloc[-1]), price, stop)
                if self._store.insert(sig):
                    new.append(sig)
                    self._notifier.send(sig)
        return ScanResult(new, errors)
