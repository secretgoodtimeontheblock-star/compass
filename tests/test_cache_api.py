from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.db import connect
from compass.markets import MarketError
from compass.models import Instrument
from compass.watchlist import Watchlist
from tests.conftest import Env, day_candles

CLOSES = [10.0, 11, 12, 13, 14]


def load(env: Env, symbol: str = "SBER") -> None:
    env.adapter.data[symbol] = day_candles(CLOSES)


def test_cold_start_then_incremental_fetch(env: Env) -> None:
    load(env)
    cache = env.services.cache
    assert len(cache.get("moex", "SBER", "1d", 5).candles) == 5
    env.adapter.data["SBER"] = day_candles(CLOSES + [15.0])
    res = cache.get("moex", "SBER", "1d", 5)
    assert [c for _, c in env.adapter.calls] == [None, 4 * 86_400_000]  # докачка, не с нуля
    assert res.candles[-1].close == 15.0


def test_last_candle_is_overwritten_not_skipped(env: Env) -> None:
    load(env)
    cache = env.services.cache
    cache.get("moex", "SBER", "1d", 5)
    env.adapter.data["SBER"] = day_candles(CLOSES[:-1] + [99.0])  # свеча была незакрытой
    assert cache.get("moex", "SBER", "1d", 5).candles[-1].close == 99.0


def test_throttle_skips_source() -> None:
    from compass.cache import CandleCache
    from tests.conftest import ScriptedAdapter

    ad = ScriptedAdapter({"SBER": day_candles(CLOSES)})
    cache = CandleCache(connect(":memory:"), {"moex": ad}, min_refresh_s=3600)
    cache.get("moex", "SBER", "1d", 5)
    cache.get("moex", "SBER", "1d", 5)
    assert len(ad.calls) == 1


def test_source_down_with_cache_returns_stale(env: Env) -> None:
    load(env)
    cache = env.services.cache
    cache.get("moex", "SBER", "1d", 5)
    env.adapter.data["SBER"] = MarketError("down")
    res = cache.get("moex", "SBER", "1d", 5)
    assert res.stale and len(res.candles) == 5


def test_source_down_without_cache_raises(env: Env) -> None:
    env.adapter.data["SBER"] = MarketError("down")
    with pytest.raises(MarketError):
        env.services.cache.get("moex", "SBER", "1d", 5)


def test_db_from_future_version_is_refused(tmp_path) -> None:
    import sqlite3

    p = tmp_path / "x.sqlite3"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError, match="более новой"):
        connect(p)


def test_v1_database_is_migrated_to_latest(tmp_path) -> None:
    import sqlite3

    from compass import db

    p = tmp_path / "v1.sqlite3"
    c = sqlite3.connect(p)
    c.executescript(db._V1)
    c.execute("PRAGMA user_version = 1")
    c.execute("INSERT INTO watchlist VALUES ('moex','SBER','Сбер',1)")
    c.commit()
    c.close()
    conn = connect(p)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 3
    assert conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 1  # данные целы
    conn.execute("SELECT COUNT(*) FROM signals")  # таблицы новых версий появились
    conn.execute("SELECT COUNT(*) FROM journal")


def test_api_candles_watchlist_and_errors(env: Env) -> None:
    load(env)
    client = TestClient(create_app(env.services))

    r = client.get("/api/candles", params={"market": "moex", "symbol": "SBER", "tf": "1d", "limit": 3})
    assert r.status_code == 200 and len(r.json()["candles"]) == 3 and r.json()["stale"] is False

    assert client.get("/api/candles", params={"market": "nope", "symbol": "X"}).status_code == 404
    assert client.get("/api/candles", params={"market": "moex", "symbol": "X", "tf": "5m"}).status_code == 502

    assert client.post("/api/watchlist", json={"market": "moex", "symbol": "SBER"}).status_code == 201
    client.post("/api/watchlist", json={"market": "moex", "symbol": "SBER"})  # дубль игнорируется
    assert len(client.get("/api/watchlist").json()) == 1
    assert client.delete("/api/watchlist/moex/SBER").status_code == 204
    assert client.delete("/api/watchlist/moex/SBER").status_code == 404


def test_api_watchlist_delete_symbol_with_slash(env: Env) -> None:
    wl: Watchlist = env.services.watchlist
    wl.add(Instrument("BTC/USDT", "BTC/USDT", "moex"))
    client = TestClient(create_app(env.services))
    assert client.delete("/api/watchlist/moex/BTC/USDT").status_code == 204
