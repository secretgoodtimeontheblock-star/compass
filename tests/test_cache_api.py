from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.cache import CandleCache
from compass.db import connect
from compass.markets import MarketError
from compass.models import Candle, Instrument
from compass.watchlist import Watchlist


class FakeAdapter:
    id = "moex"
    name = "fake"
    timeframes = ("1d",)

    def __init__(self) -> None:
        self.calls: list[int | None] = []
        self.fail = False
        self.data = [Candle(i * 1000, 1, 2, 0, 1.5, 10) for i in range(1, 6)]

    def fetch_candles(self, symbol, tf, since_ms, limit):
        self.calls.append(since_ms)
        if self.fail:
            raise MarketError("down")
        return [c for c in self.data if since_ms is None or c.ts >= since_ms][-limit:]

    def search(self, q):
        return [Instrument("SBER", "Сбербанк", "moex")]


@pytest.fixture
def env():
    conn = connect(":memory:")
    ad = FakeAdapter()
    cache = CandleCache(conn, {"moex": ad}, min_refresh_s=0)
    return ad, cache, conn


def test_cold_start_then_incremental_fetch(env) -> None:
    ad, cache, _ = env
    assert len(cache.get("moex", "SBER", "1d", 5).candles) == 5
    ad.data.append(Candle(6000, 1, 2, 0, 1.5, 10))
    res = cache.get("moex", "SBER", "1d", 5)
    assert ad.calls == [None, 5000]  # докачка с последней свечи, не с нуля
    assert res.candles[-1].ts == 6000


def test_last_candle_is_overwritten_not_skipped(env) -> None:
    ad, cache, _ = env
    cache.get("moex", "SBER", "1d", 5)
    ad.data[-1] = Candle(5000, 1, 9, 0, 8.0, 99)  # свеча была незакрытой
    assert cache.get("moex", "SBER", "1d", 5).candles[-1].close == 8.0


def test_throttle_skips_source(env) -> None:
    ad, _, conn = env
    cache = CandleCache(conn, {"moex": ad}, min_refresh_s=3600)
    cache.get("moex", "SBER", "1d", 5)
    cache.get("moex", "SBER", "1d", 5)
    assert len(ad.calls) == 1


def test_source_down_with_cache_returns_stale(env) -> None:
    ad, cache, _ = env
    cache.get("moex", "SBER", "1d", 5)
    ad.fail = True
    res = cache.get("moex", "SBER", "1d", 5)
    assert res.stale and len(res.candles) == 5


def test_source_down_without_cache_raises(env) -> None:
    ad, cache, _ = env
    ad.fail = True
    with pytest.raises(MarketError):
        cache.get("moex", "SBER", "1d", 5)


def test_db_from_future_version_is_refused(tmp_path) -> None:
    import sqlite3

    p = tmp_path / "x.sqlite3"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match="более новой"):
        connect(p)


def test_api_candles_watchlist_and_errors(env) -> None:
    ad, cache, conn = env
    client = TestClient(create_app({"moex": ad}, cache, Watchlist(conn)))

    r = client.get("/api/candles", params={"market": "moex", "symbol": "SBER", "tf": "1d", "limit": 3})
    assert r.status_code == 200 and len(r.json()["candles"]) == 3 and r.json()["stale"] is False

    assert client.get("/api/candles", params={"market": "nope", "symbol": "X"}).status_code == 404
    assert client.get("/api/candles", params={"market": "moex", "symbol": "X", "tf": "5m"}).status_code == 502

    assert client.post("/api/watchlist", json={"market": "moex", "symbol": "SBER"}).status_code == 201
    client.post("/api/watchlist", json={"market": "moex", "symbol": "SBER"})  # дубль игнорируется
    assert len(client.get("/api/watchlist").json()) == 1
    assert client.delete("/api/watchlist/moex/SBER").status_code == 204
    assert client.delete("/api/watchlist/moex/SBER").status_code == 404


def test_api_watchlist_delete_symbol_with_slash(env) -> None:
    ad, cache, conn = env
    wl = Watchlist(conn)
    wl.add(Instrument("BTC/USDT", "BTC/USDT", "moex"))
    client = TestClient(create_app({"moex": ad}, cache, wl))
    assert client.delete("/api/watchlist/moex/BTC/USDT").status_code == 204
