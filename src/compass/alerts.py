"""Оповещения о цене: «сообщи, когда цена дойдёт до X».

Проверка идёт не по последней цене, а по свечам с момента создания оповещения: если цена коснулась уровня
между двумя проверками (например, шпилька вверх на минуту), максимум или минимум свечи это покажет.
Оповещение срабатывает один раз; сработавшее хранится и не стирается. Это напоминание пользователя
самому себе, а не сигнал и не рекомендация. Работает только пока приложение запущено."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace

from compass.cache import CandleCache
from compass.db import Connection
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS

log = logging.getLogger("compass.alerts")

MAX_ACTIVE = 100
MAX_ACTIVE_PER_INSTRUMENT = 10
KINDS = ("above", "below")
_COLS = "market, symbol, kind, price, note, id, uid, created_at, status, triggered_at, triggered_price, seen"
# окна проверки: минутные свечи ловят свежие касания, часовые — касания за последние дни, если приложение было закрыто
_FINE_TF, _FINE_LIMIT = "1m", 180
_COARSE_TF, _COARSE_LIMIT = "1h", 120


@dataclass(frozen=True, slots=True)
class Alert:
    market: str
    symbol: str
    kind: str  # above — цена поднялась до уровня или выше; below — опустилась до уровня или ниже
    price: float
    note: str = ""
    id: int | None = None
    uid: str | None = None
    created_at: int | None = None  # секунды UTC
    status: str = "active"
    triggered_at: int | None = None
    triggered_price: float | None = None
    seen: bool = False


def validate(a: Alert) -> None:
    if a.kind not in KINDS:
        raise ValueError("Тип оповещения: above (выше) или below (ниже)")
    if not math.isfinite(a.price) or a.price <= 0:
        raise ValueError("Цена оповещения должна быть больше нуля")
    if len(a.note) > 80:
        raise ValueError("Заметка к оповещению — не длиннее 80 символов")


class AlertStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def add(self, a: Alert) -> Alert:
        validate(a)
        with self._lock, self._conn:
            active = self._conn.execute("SELECT market, symbol FROM alerts WHERE status='active'").fetchall()
            if len(active) >= MAX_ACTIVE:
                raise ValueError(f"Активных оповещений не больше {MAX_ACTIVE}: отмените ненужные")
            if sum(1 for m, s in active if (m, s) == (a.market, a.symbol)) >= MAX_ACTIVE_PER_INSTRUMENT:
                raise ValueError(f"На один инструмент — не больше {MAX_ACTIVE_PER_INSTRUMENT} оповещений")
            now, uid = int(time.time()), uuid.uuid4().hex
            cur = self._conn.execute(
                "INSERT INTO alerts (uid, market, symbol, kind, price, note, created_at) VALUES (?,?,?,?,?,?,?)",
                (uid, a.market, a.symbol, a.kind, a.price, a.note.strip(), now),
            )
        return replace(a, id=int(cur.lastrowid), uid=uid, created_at=now, note=a.note.strip())

    def list(self, status: str | None = None, market: str | None = None, symbol: str | None = None, limit: int = 200) -> list[Alert]:
        where, args = [], []
        for col, v in (("status", status), ("market", market), ("symbol", symbol)):
            if v:
                where.append(f"{col} = ?")
                args.append(v)
        sql = f"SELECT {_COLS} FROM alerts" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        return [_row(r) for r in rows]

    def get(self, alert_id: int) -> Alert | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLS} FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        return _row(row) if row else None

    def cancel(self, alert_id: int) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE alerts SET status='cancelled' WHERE id=? AND status='active'", (alert_id,))
        return cur.rowcount > 0

    def trigger(self, alert_id: int, price: float, ts: int) -> bool:
        """Фиксирует срабатывание один раз: повторный вызов ничего не меняет."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE alerts SET status='triggered', triggered_at=?, triggered_price=? WHERE id=? AND status='active'",
                (ts, price, alert_id),
            )
        return cur.rowcount > 0

    def mark_seen(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE alerts SET seen=1 WHERE status='triggered' AND seen=0")
        return cur.rowcount


def _row(r: tuple) -> Alert:
    return Alert(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], bool(r[11]))


def touched(alert: Alert, candles, created_ms: int, tf: str) -> tuple[float, int] | None:
    """Первая свеча после создания оповещения, в которой цена дошла до уровня: (цена касания, время свечи, мс).
    Свеча, начавшаяся до создания, учитывается только если она ещё идёт: иначе сработка была бы «из прошлого»."""
    tf_ms = TIMEFRAME_MS[tf]
    for c in candles:
        if c.ts + tf_ms <= created_ms:
            continue
        if alert.kind == "above" and c.high >= alert.price:
            return (max(alert.price, c.open) if c.open >= alert.price else alert.price), c.ts
        if alert.kind == "below" and c.low <= alert.price:
            return (min(alert.price, c.open) if c.open <= alert.price else alert.price), c.ts
    return None


def format_alert(a: Alert, price: float) -> str:
    arrow = "⬆ поднялась до" if a.kind == "above" else "⬇ опустилась до"
    note = f"\nЗаметка: {a.note}" if a.note else ""
    return (f"🔔 {a.symbol}: цена {arrow} {a.price:g} (последняя {price:g}).{note}\n"
            "Это ваше собственное оповещение, а не сигнал. Не инвестиционная рекомендация.")


class AlertEngine:
    def __init__(
        self, cache: CandleCache, store: AlertStore, notifier=None, now_ms: Callable[[], int] = lambda: int(time.time() * 1000)
    ) -> None:
        self._cache, self._store, self._notifier, self._now_ms = cache, store, notifier, now_ms

    def check(self) -> list[Alert]:
        """Проверяет все активные оповещения; возвращает сработавшие. Сбой источника не роняет проверку:
        оповещение просто остаётся активным до следующего раза."""
        active = self._store.list("active", limit=MAX_ACTIVE)
        by_symbol: dict[tuple[str, str], list[Alert]] = {}
        for a in active:
            by_symbol.setdefault((a.market, a.symbol), []).append(a)
        fired: list[Alert] = []
        for (market, symbol), alerts in by_symbol.items():
            windows = []
            for tf, lim in ((_FINE_TF, _FINE_LIMIT), (_COARSE_TF, _COARSE_LIMIT)):
                try:  # у источника может не быть минутных свечей — тогда работаем по часовым
                    windows.append((tf, self._cache.get(market, symbol, tf, lim)))
                except (MarketError, KeyError):
                    continue
            if not windows:
                continue
            for a in alerts:
                hit = None
                for tf, res in windows:
                    hit = touched(a, res.candles, (a.created_at or 0) * 1000, tf)
                    if hit:
                        break
                if hit is None:
                    continue
                last = next((res.candles[-1].close for _, res in windows if res.candles), hit[0])
                if self._store.trigger(a.id or 0, hit[0], self._now_ms() // 1000):
                    done = replace(a, status="triggered", triggered_price=hit[0])
                    fired.append(done)
                    self._send(done, last)
        return fired

    def _send(self, a: Alert, last: float) -> None:
        send = getattr(self._notifier, "send_text", None)
        if send is None:
            return
        try:
            send(format_alert(a, last))
        except Exception:
            log.exception("Не удалось отправить оповещение %s", a.symbol)


class AlertWatcher:
    """Фоновая проверка раз в минуту. Отдельно от скана сигналов: оповещение о цене не должно ждать 15 минут."""

    def __init__(self, engine: AlertEngine, interval_s: float = 60.0, first_delay_s: float = 10.0) -> None:
        self._engine, self._interval, self._first = engine, interval_s, first_delay_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="compass-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        wait = self._first
        while not self._stop.wait(wait):
            try:
                self._engine.check()
            except Exception:
                log.exception("Сбой проверки оповещений")
            wait = self._interval
