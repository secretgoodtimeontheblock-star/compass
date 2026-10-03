"""Поиск сигналов по вотчлисту.

Сигнал — это СМЕНА целевой позиции стратегии на последней ЗАКРЫТОЙ свече
(0→1 вход, 1→0 выход). Незакрытая свеча игнорируется: по ней стратегия ещё
может передумать. Один и тот же сигнал на одной свече хранится один раз
(UNIQUE в БД) — повторный скан ничего не дублирует и не шлёт повторно.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, tzinfo

from compass.cache import CandleCache
from compass.db import Connection
from compass.indicators import atr
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, closed_candles
from compass.settings import Settings, profile_key
from compass.strategies import STRATEGIES, candles_to_df, strategy_version
from compass.swing import STOP_ATR_MULT, swing_plan
from compass.watchlist import Watchlist

log = logging.getLogger("compass.signals")

ATR_STOP_MULT = STOP_ATR_MULT  # ориентир стопа акций: цена входа − 2·ATR(14)
_SIGNAL_SELECT = (
    "SELECT market, symbol, tf, strategy, side, candle_ts, price, stop, id, created_at, seen, params, "
    "strategy_version, notified_at, dismissed_at, target, leverage, stake FROM signals "
)
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
    params: dict[str, int] | None = None  # параметры стратегии; None — сигнал создан до их сохранения
    strategy_version: str | None = None  # см. strategies.strategy_version; None — сигнал старого образца
    notified_at: int | None = None  # None — внешнее уведомление ещё не ушло (например, тихие часы)
    dismissed_at: int | None = None  # пользователь отклонил сигнал
    target: float | None = None  # цель свинга; None — сигнал без плана на 100 USDT
    leverage: float | None = None
    stake: float | None = None  # изолированная маржа, USDT


@dataclass(frozen=True, slots=True)
class ScanResult:
    new: list[Signal]
    errors: list[str]


def _row_to_signal(r: tuple) -> Signal:
    return Signal(*r[:10], seen=bool(r[10]), params=json.loads(r[11]) if r[11] else None,
                  strategy_version=r[12], notified_at=r[13], dismissed_at=r[14],
                  target=r[15], leverage=r[16], stake=r[17])


class SignalStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def insert(self, s: Signal) -> bool:
        """True — сигнал новый; False — такой уже был."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO signals "
                "(market, symbol, tf, strategy, side, candle_ts, price, stop, created_at, params, strategy_version, "
                "target, leverage, stake) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (s.market, s.symbol, s.tf, s.strategy, s.side, s.candle_ts, s.price, s.stop, int(time.time()),
                 None if s.params is None else json.dumps(s.params, sort_keys=True), s.strategy_version,
                 s.target, s.leverage, s.stake),
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
        sql = _SIGNAL_SELECT + (f"WHERE {' AND '.join(where)} " if where else "") + "ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [_row_to_signal(r) for r in rows]

    def get(self, signal_id: int) -> Signal | None:
        with self._lock:
            row = self._conn.execute(_SIGNAL_SELECT + "WHERE id = ?", (signal_id,)).fetchone()
        return _row_to_signal(row) if row else None

    def pending_notification(self) -> list[Signal]:
        """Сигналы, о которых ещё не уведомляли и которые пользователь не отклонил."""
        with self._lock:
            rows = self._conn.execute(
                _SIGNAL_SELECT + "WHERE notified_at IS NULL AND dismissed_at IS NULL ORDER BY id"
            ).fetchall()
        return [_row_to_signal(r) for r in rows]

    def mark_notified(self, ids: list[int], now_s: int) -> None:
        with self._lock, self._conn:
            self._conn.executemany("UPDATE signals SET notified_at = ? WHERE id = ?", [(now_s, i) for i in ids])

    def dismiss(self, signal_id: int, now_s: int) -> bool:
        with self._lock, self._conn:
            return (
                self._conn.execute(
                    "UPDATE signals SET dismissed_at = ? WHERE id = ? AND dismissed_at IS NULL", (now_s, signal_id)
                ).rowcount
                == 1
            )

    def undismiss(self, signal_id: int) -> bool:
        with self._lock, self._conn:
            return (
                self._conn.execute(
                    "UPDATE signals SET dismissed_at = NULL WHERE id = ? AND dismissed_at IS NOT NULL", (signal_id,)
                ).rowcount
                == 1
            )

    def set_state(self, market: str, symbol: str, status: str, message: str, now_s: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO scan_state (market, symbol, status, message, last_scan_at, last_ok_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(market, symbol) DO UPDATE SET status=excluded.status, "
                "message=excluded.message, last_scan_at=excluded.last_scan_at, "
                "last_ok_at=CASE WHEN excluded.status='ok' THEN excluded.last_scan_at ELSE scan_state.last_ok_at END",
                (market, symbol, status, message, now_s, now_s if status == "ok" else None),
            )

    def states(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT market, symbol, status, message, last_scan_at, last_ok_at FROM scan_state ORDER BY market, symbol"
            ).fetchall()
        keys = ("market", "symbol", "status", "message", "last_scan_at", "last_ok_at")
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def mark_all_seen(self) -> int:
        with self._lock, self._conn:
            return self._conn.execute("UPDATE signals SET seen = 1 WHERE seen = 0").rowcount


def _key(s: Signal) -> tuple:
    return (s.market, s.symbol, s.tf, s.strategy, s.candle_ts, s.side)


def signal_expires_at(s: Signal, valid_bars: int) -> int:
    """Сигнал известен после закрытия свечи (ts + tf) и актуален ещё valid_bars свечей."""
    return s.candle_ts + TIMEFRAME_MS[s.tf] * (1 + valid_bars)


def signal_status(s: Signal, now_ms: int, valid_bars: int, acted: bool) -> str:
    """dismissed — отклонён пользователем; acted — по нему есть план или запись журнала; expired — срок вышел; active."""
    if s.dismissed_at is not None:
        return "dismissed"
    if acted:
        return "acted"
    return "expired" if now_ms >= signal_expires_at(s, valid_bars) else "active"


def in_quiet_hours(quiet: dict, now_ms: int, tz: tzinfo | None = None) -> bool:
    """Тихие часы по местному времени; окно может переходить через полночь (22:00–08:00)."""
    if not quiet.get("enabled"):
        return False
    local = datetime.fromtimestamp(now_ms / 1000, tz)
    now_min = local.hour * 60 + local.minute
    start = int(quiet["from"][:2]) * 60 + int(quiet["from"][3:])
    end = int(quiet["to"][:2]) * 60 + int(quiet["to"][3:])
    return start <= now_min < end if start < end else (now_min >= start or now_min < end)


class SignalEngine:
    def __init__(
        self,
        cache: CandleCache,
        watchlist: Watchlist,
        settings: Settings,
        store: SignalStore,
        notifier,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        local_tz: tzinfo | None = None,
    ) -> None:
        self._cache, self._watchlist, self._settings = cache, watchlist, settings
        self._store, self._notifier, self._now_ms = store, notifier, now_ms
        self._tz = local_tz
        self._scan_lock = threading.Lock()  # ручной скан и фоновый не должны идти одновременно
        self.last_scan_at: int | None = None  # секунды UTC окончания последней проверки

    def scan(self) -> ScanResult:
        with self._scan_lock:
            return self._scan()

    def _scan(self) -> ScanResult:
        cfg = self._settings.all()
        now = self._now_ms()
        now_s = now // 1000
        paused = set(cfg["paused_instruments"])
        new: list[Signal] = []
        errors: list[str] = []
        for inst in self._watchlist.list():
            tf = cfg.get(f"tf_{inst.market}")
            if tf is None:
                continue
            if profile_key(inst.market, inst.symbol) in paused:
                self._store.set_state(inst.market, inst.symbol, "paused", "Наблюдение приостановлено вами", now_s)
                continue
            try:
                result = self._cache.get(inst.market, inst.symbol, tf, HISTORY)
            except MarketError as e:
                errors.append(f"{inst.symbol}: {e}")
                self._store.set_state(inst.market, inst.symbol, "error", str(e)[:200], now_s)
                continue
            if result.stale:
                msg = (
                    "источник недоступен, свечи из кэша; "
                    "поиск новых сигналов приостановлен до обновления данных"
                )
                errors.append(f"{inst.symbol}: {msg}")
                self._store.set_state(inst.market, inst.symbol, "stale", msg, now_s)
                continue
            closed = closed_candles(result.candles, tf, now)
            if len(closed) < 3:
                self._store.set_state(inst.market, inst.symbol, "short", "Слишком мало закрытых свечей", now_s)
                continue
            df = candles_to_df(closed)
            last_atr = atr(df, 14).iloc[-1]
            try:
                chosen = _chosen_strategies(cfg["instrument_strategies"], inst.market, inst.symbol)
            except ValueError as e:
                errors.append(f"{inst.symbol}: {e}")
                self._store.set_state(inst.market, inst.symbol, "error", str(e)[:200], now_s)
                continue
            for strat, params in chosen:
                target = strat.target(df, params)
                prev, cur = int(target.iloc[-2]), int(target.iloc[-1])
                if prev == cur:
                    continue
                price = float(df["close"].iloc[-1])
                side = "buy" if cur == 1 else "exit"
                stop = target_px = leverage = stake = None
                if side == "buy" and not math.isnan(float(last_atr)) and float(last_atr) > 0:
                    if inst.market == "crypto":
                        plan = swing_plan(price, float(last_atr))
                        if plan is not None:
                            stop, target_px, leverage, stake = plan.stop, plan.target, plan.leverage, plan.stake
                    else:
                        stop = round(price - ATR_STOP_MULT * float(last_atr), 8)
                        if stop <= 0:
                            stop = None
                sig = Signal(
                    inst.market, inst.symbol, tf, strat.id, side, int(df["ts"].iloc[-1]), price, stop,
                    params=params, strategy_version=strategy_version(strat, params),
                    target=target_px, leverage=leverage, stake=stake,
                )
                if self._store.insert(sig):
                    new.append(sig)
            self._store.set_state(inst.market, inst.symbol, "ok", "", now_s)
        self.last_scan_at = now_s
        self._deliver(cfg, now, now_s, {_key(s) for s in new})
        return ScanResult(new, errors)

    def _deliver(self, cfg: dict, now: int, now_s: int, new_keys: set[tuple]) -> None:
        """Внешние уведомления. В тихие часы сигналы только сохраняются; после них устаревшее не рассылается,
        а накопившееся уходит одним сообщением."""
        if in_quiet_hours(cfg["quiet_hours"], now, self._tz):
            return
        pending = self._store.pending_notification()
        if not pending:
            return
        valid = cfg["signal_valid_bars"]
        fresh = [s for s in pending if now < signal_expires_at(s, valid)]
        expired = [s for s in pending if s not in fresh]
        if expired:  # устаревший сигнал уже никому не нужен — помечаем, но не шлём
            self._store.mark_notified([s.id for s in expired if s.id is not None], now_s)
        if not fresh:
            return
        older = [s for s in fresh if _key(s) not in new_keys]  # накопились раньше, чем идёт этот скан
        if older and hasattr(self._notifier, "send_digest"):
            self._notifier.send_digest(fresh)
        else:
            for s in fresh:
                self._notifier.send(s)
        self._store.mark_notified([s.id for s in fresh if s.id is not None], now_s)


def _chosen_strategies(profiles: dict, market: str, symbol: str) -> list[tuple]:
    """Сохранённая связка инструмента или все стратегии с параметрами по умолчанию."""
    raw = profiles.get(profile_key(market, symbol))
    if not raw:
        return [(s, s.resolve()) for s in STRATEGIES.values()]
    strat = STRATEGIES[raw["strategy"]]
    return [(strat, strat.resolve({k: int(v) for k, v in raw["params"].items()}))]
