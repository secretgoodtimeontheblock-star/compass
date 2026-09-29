from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from compass.api.app import Services
from compass.cache import CandleCache
from compass.db import connect
from compass.journal import Journal
from compass.markets import MarketError
from compass.models import Candle, Instrument
from compass.settings import Settings
from compass.signals import Signal, SignalEngine, SignalStore
from compass.watchlist import Watchlist

DAY = 86_400_000


def day_candles(closes: list[float], opens: list[float] | None = None) -> list[Candle]:
    opens = opens or closes
    return [
        Candle(i * DAY, o, max(o, c), min(o, c), c, 100.0)
        for i, (o, c) in enumerate(zip(opens, closes, strict=True))
    ]


class ScriptedAdapter:
    """Отдаёт заранее заданные свечи по тикеру; значение-исключение — «источник упал»."""

    id = "moex"
    name = "scripted"
    timeframes = ("1d",)

    def __init__(self, data: dict[str, list[Candle] | Exception] | None = None) -> None:
        self.data = data or {}
        self.calls: list[tuple[str, int | None]] = []
        self.lot = 10

    def fetch_candles(self, symbol: str, tf: str, since_ms: int | None, limit: int) -> list[Candle]:
        self.calls.append((symbol, since_ms))
        item = self.data.get(symbol, [])
        if isinstance(item, Exception):
            raise item
        return [c for c in item if since_ms is None or c.ts >= since_ms][-limit:]

    def search(self, q: str) -> list[Instrument]:
        return [Instrument("SBER", "Сбербанк", "moex")]

    def lot_size(self, symbol: str) -> int:
        return self.lot


@dataclass
class RecordingNotifier:
    sent: list[Signal] = field(default_factory=list)

    def send(self, signal: Signal) -> None:
        self.sent.append(signal)


@dataclass
class Env:
    services: Services
    adapter: ScriptedAdapter
    notifier: RecordingNotifier
    now: list[int]  # изменяемые «часы»: now[0] — текущее время, мс


@pytest.fixture
def env() -> Env:
    conn = connect(":memory:")
    adapter = ScriptedAdapter()
    adapters = {"moex": adapter}
    cache = CandleCache(conn, adapters, min_refresh_s=0)
    watchlist = Watchlist(conn)
    settings = Settings(conn, {"moex": adapter.timeframes})
    store = SignalStore(conn)
    notifier = RecordingNotifier()
    now = [10_000 * DAY]
    engine = SignalEngine(cache, watchlist, settings, store, notifier, now_ms=lambda: now[0])
    svc = Services(adapters, cache, watchlist, settings, store, engine, Journal(conn))
    return Env(svc, adapter, notifier, now)


def _unused() -> None:  # держим импорт MarketError доступным тестам через conftest
    raise MarketError("")
