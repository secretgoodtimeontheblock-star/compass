import sqlite3

import pytest

from compass import db
from compass.cache import CandleCache
from compass.markets.base import MarketError
from tests.conftest import ScriptedAdapter, day_candles


def test_exchange_switch_never_reuses_another_exchanges_prices():
    conn = db.connect(":memory:")
    first = ScriptedAdapter({"BTC/USDT": day_candles([10, 11])})
    first.source_id = "crypto:okx"
    second = ScriptedAdapter({"BTC/USDT": MarketError("offline")})
    second.source_id = "crypto:other"
    adapters = {"crypto": first}
    cache = CandleCache(conn, adapters)
    original = cache.get("crypto", "BTC/USDT", "1d", 2)
    adapters["crypto"] = second
    with pytest.raises(MarketError, match="offline"):
        cache.get("crypto", "BTC/USDT", "1d", 2)
    second.data["BTC/USDT"] = day_candles([100, 110])
    assert cache.get("crypto", "BTC/USDT", "1d", 2).candles[-1].close == 110
    adapters["crypto"] = first
    assert cache.get("crypto", "BTC/USDT", "1d", 2) == original
    conn.close()


def test_recovery_requests_full_window_instead_of_incremental_cursor():
    conn = db.connect(":memory:")
    adapter = ScriptedAdapter({"BTC/USDT": day_candles([10, 11])})
    cache = CandleCache(conn, {"crypto": adapter}, min_refresh_s=0)
    cache.get("crypto", "BTC/USDT", "1d", 2)
    cache.get("crypto", "BTC/USDT", "1d", 2)
    assert adapter.calls[-1][1] is not None
    cache.get("crypto", "BTC/USDT", "1d", 2, refresh=True)
    assert adapter.calls[-1][1] is None
    conn.close()


def test_v4_upgrade_preserves_data_and_creates_original_backup(tmp_path):
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(db._V1 + db._V2 + db._V3 + db._V4 + "PRAGMA user_version=4;")
    old.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)", [
        ("moex", "SBER", "1d", 1000, 10, 12, 9, 11, 100),
        ("crypto", "BTC/USDT", "1d", 1000, 100, 120, 90, 110, 200),
    ])
    old.execute("INSERT INTO fetch_log VALUES ('crypto', 'BTC/USDT', '1d', 123)")
    old.execute("INSERT INTO watchlist VALUES ('moex', 'SBER', 'Сбербанк', 123)")
    old.execute("INSERT INTO settings VALUES ('tf_crypto', '\"1h\"')")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert set(conn.execute("SELECT source, close FROM candles")) == {("moex:iss", 11), ("legacy:crypto", 110)}
    assert conn.execute("SELECT source, fetched_at FROM fetch_log").fetchone() == ("legacy:crypto", 123)
    assert conn.execute("SELECT name FROM watchlist").fetchone()[0] == "Сбербанк"
    assert conn.execute("SELECT value FROM settings").fetchone()[0] == '"1h"'
    conn.close()
    backup = sqlite3.connect(str(path) + ".v4.bak")
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
    assert backup.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 2
    backup.close()


def test_v6_upgrade_adds_signal_params_keeping_old_rows_and_backup(tmp_path):
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(db._V1 + db._V2 + db._V3 + db._V4 + db._V5 + db._V6 + "PRAGMA user_version=6;")
    old.execute(
        "INSERT INTO signals (market, symbol, tf, strategy, side, candle_ts, price, stop, created_at) "
        "VALUES ('moex','SBER','1d','donchian','buy',1000,270.5,250.0,5)"
    )
    old.execute(
        "INSERT INTO journal (market, symbol, side, qty, price, fee, ts, created_at, planned_stop, reason) "
        "VALUES ('moex','SBER','buy',10,270.5,1.5,1000,5,250.0,'пробой')"
    )
    old.commit()
    old.close()

    conn = db.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        row = conn.execute("SELECT symbol, price, stop, params FROM signals").fetchone()
        assert row == ("SBER", 270.5, 250.0, None)
        assert conn.execute("SELECT qty, reason FROM journal").fetchone() == (10, "пробой")
    finally:
        conn.close()
    backup = sqlite3.connect(str(path) + ".v6.bak")
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 6
        assert backup.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1
    finally:
        backup.close()
