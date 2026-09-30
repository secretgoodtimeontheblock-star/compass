from __future__ import annotations

import threading
from dataclasses import dataclass, field

import pytest

from compass.ai.providers import ModelInfo
from compass.ai.service import AiService
from compass.api.app import Services
from compass.cache import CandleCache
from compass.db import connect
from compass.experiments import ExperimentLog
from compass.journal import Journal
from compass.markets import MarketError
from compass.models import Candle, Instrument, InstrumentInfo
from compass.plans import PlanStore
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

    def instrument_info(self, symbol: str) -> InstrumentInfo:
        return InstrumentInfo(symbol, self.id, "scripted", lot=self.lot, price_step=0.01, currency="RUB", trading_open=True)


@dataclass
class RecordingNotifier:
    sent: list[Signal] = field(default_factory=list)

    def send(self, signal: Signal) -> None:
        self.sent.append(signal)


class FakeProvider:
    """Провайдер AI без сети: отдаёт заданный ответ и считает вызовы."""

    def __init__(self, pid: str, cloud: bool, reply: str = "Ответ AI. Решение остаётся за вами.") -> None:
        self.id, self.name, self.cloud, self.default_model = pid, f"fake-{pid}", cloud, "m1"
        self.reply = reply
        self.calls: list[tuple[str, str, str]] = []  # (system, prompt, model)
        self.error: Exception | None = None
        self.gate: threading.Event | None = None  # если задан — ask() ждёт (проверка параллельности)

    def available(self) -> tuple[bool, str]:
        return True, ""

    def models(self) -> list[ModelInfo]:
        return [ModelInfo("m1", "Модель 1"), ModelInfo("m2", "Модель 2")]

    def ask(self, system: str, prompt: str, model: str) -> str:
        self.calls.append((system, prompt, model))
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return self.reply


@dataclass
class Env:
    services: Services
    adapter: ScriptedAdapter
    notifier: RecordingNotifier
    now: list[int]  # изменяемые «часы»: now[0] — текущее время, мс
    cloud: FakeProvider  # зарегистрирован как «cursor» (облачный)
    local: FakeProvider  # зарегистрирован как «ollama» (локальный)


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
    cloud, local = FakeProvider("cursor", cloud=True), FakeProvider("ollama", cloud=False)
    ai = AiService({"cursor": cloud, "ollama": local}, settings, conn, slot_wait_s=0.05)
    svc = Services(
        adapters, cache, watchlist, settings, store, engine, Journal(conn), ai, now_ms=lambda: now[0],
        plans=PlanStore(conn),
        experiments=ExperimentLog(conn),
    )
    return Env(svc, adapter, notifier, now, cloud, local)


def _unused() -> None:  # держим импорт MarketError доступным тестам через conftest
    raise MarketError("")
