from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from compass.ai.prompts import Prompt
from compass.journal import Entry
from compass.models import Instrument
from compass.signals import Signal
from tests.conftest import Env


@pytest.mark.parametrize("operation", ["settings", "journal", "signals", "watchlist", "cache", "ai"])
def test_service_cannot_commit_another_threads_transaction(env: Env, operation: str) -> None:
    """Чужой commit не сохраняет запись, которую её владелец собирается откатить."""
    svc = env.services
    conn = svc.cache._conn
    svc.settings.update({"ai_provider": "ollama"})
    started = Event()
    completed = Event()
    actions = {
        "settings": lambda: svc.settings.update({"capital": 200_000}),
        "journal": lambda: svc.journal.add(Entry("moex", "SBER", "buy", 10, 100, 1)),
        "signals": lambda: svc.signals.insert(Signal("moex", "SBER", "1d", "donchian", "buy", 1, 100)),
        "watchlist": lambda: svc.watchlist.add(Instrument("SBER", "Сбер", "moex")),
        "cache": lambda: svc.cache.get("moex", "SBER", "1d"),
        "ai": lambda: svc.ai.run(Prompt("test", "system", "question", "", audit=False)),
    }

    def write() -> None:
        started.set()
        actions[operation]()
        completed.set()

    # shutdown пула должен идти ПОСЛЕ освобождения блокировки даже при падении assert.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with conn.lock:
            try:
                conn.execute("INSERT INTO watchlist VALUES ('moex', 'ROLLBACK', 'temporary', 1)")
                future = pool.submit(write)
                assert started.wait(2)
                assert not completed.wait(0.1)
            finally:
                conn.rollback()
        future.result(timeout=5)
    assert completed.is_set()
    assert all(item.symbol != "ROLLBACK" for item in svc.watchlist.list())


def test_parallel_scan_journal_and_settings(env: Env) -> None:
    from tests.conftest import DAY, day_candles

    svc = env.services
    svc.watchlist.add(Instrument("SBER", "Сбер", "moex"))
    env.adapter.data["SBER"] = day_candles([10.0] * 25 + [12.0])
    env.now[0] = 26 * DAY + 1

    def work(i: int) -> None:
        svc.engine.scan()
        svc.journal.add(Entry("moex", "SBER", "buy", 1, 100, i + 1))
        svc.settings.update({"capital": 100_000 + i})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(24)))
    assert svc.journal.positions()[0].qty == 24
    assert len(svc.journal.list()) == 24
    assert len([s for s in svc.signals.list() if s.strategy == "donchian"]) == 1
    assert len(env.notifier.sent) == len(svc.signals.list())
