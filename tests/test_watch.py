"""Пакет F: срок и статус сигналов, тихие часы, пауза, состояние наблюдения."""

from __future__ import annotations

import logging
from datetime import UTC, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from compass import db
from compass.api.app import create_app
from compass.models import Instrument
from compass.notify import TelegramNotifier, format_digest
from compass.signals import Signal, in_quiet_hours, signal_expires_at, signal_status
from tests.conftest import DAY, Env, day_candles, with_next_open

HOUR = 3_600_000
BREAKOUT = [10.0] * 25 + [12.0]
Q = {"enabled": True, "from": "22:00", "to": "08:00"}


def watch(env: Env, symbol: str = "XYZ") -> None:
    env.services.watchlist.add(Instrument(symbol, symbol, "moex"))
    env.adapter.data[symbol] = with_next_open(day_candles(BREAKOUT))


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


def sig(**kw) -> Signal:
    base = {"market": "moex", "symbol": "XYZ", "tf": "1d", "strategy": "donchian", "side": "buy",
            "candle_ts": 25 * DAY, "price": 12.0}
    return Signal(**{**base, **kw})


# --- тихие часы (чистая функция) ---


@pytest.mark.parametrize(
    ("hhmm", "expected"),
    [("21:59", False), ("22:00", True), ("23:59", True), ("00:00", True), ("07:59", True), ("08:00", False), ("12:00", False)],
)
def test_quiet_hours_window_crosses_midnight(hhmm: str, expected: bool) -> None:
    h, m = map(int, hhmm.split(":"))
    now = (10_000 * DAY) + h * HOUR + m * 60_000
    assert in_quiet_hours(Q, now, UTC) is expected


def test_quiet_hours_same_day_window_disabled_and_timezone() -> None:
    day = {"enabled": True, "from": "13:00", "to": "14:00"}
    base = 10_000 * DAY
    assert in_quiet_hours(day, base + 13 * HOUR + 30 * 60_000, UTC) is True
    assert in_quiet_hours(day, base + 14 * HOUR, UTC) is False
    assert in_quiet_hours({**day, "enabled": False}, base + 13 * HOUR + 1, UTC) is False
    msk = timezone(timedelta(hours=3))  # 10:30 UTC = 13:30 МСК
    assert in_quiet_hours(day, base + 10 * HOUR + 30 * 60_000, msk) is True
    assert in_quiet_hours(day, base + 10 * HOUR + 30 * 60_000, UTC) is False


# --- срок и статус ---


def test_expiry_and_status_rules() -> None:
    s = sig(id=1)  # свеча 25 закрывается в 26·DAY; актуален ещё 3 свечи → до 29·DAY
    assert signal_expires_at(s, 3) == 29 * DAY
    assert signal_status(s, 29 * DAY - 1, 3, False) == "active"
    assert signal_status(s, 29 * DAY, 3, False) == "expired"
    assert signal_status(s, 100 * DAY, 3, True) == "acted"  # действовали — важнее срока
    assert signal_status(sig(id=1, dismissed_at=5), 26 * DAY, 3, True) == "dismissed"  # отклонён — важнее всего


def test_api_shows_status_expiry_filter_and_dismiss_restore(env: Env) -> None:
    watch(env)
    env.now[0] = 26 * DAY + 1
    c = client(env)
    assert c.post("/api/scan").json()["new"]
    (s,) = [x for x in c.get("/api/signals").json() if x["strategy"] == "donchian"]
    assert s["status"] == "active" and s["expires_at"] == 29 * DAY
    assert [x["id"] for x in c.get("/api/signals", params={"status": "active"}).json()] and \
        c.get("/api/signals", params={"status": "expired"}).json() == []
    assert c.get("/api/signals", params={"status": "zzz"}).status_code == 422
    env.now[0] = 29 * DAY
    assert c.get("/api/signals", params={"status": "expired"}).json()
    # сменили срок в настройках — статус пересчитан, а не «зашит» при создании
    c.put("/api/settings", json={"signal_valid_bars": 10})
    assert c.get("/api/signals").json()[0]["status"] == "active"
    d = c.post(f"/api/signals/{s['id']}/dismiss").json()
    assert d["status"] == "dismissed" and d["dismissed_at"]
    assert c.get("/api/signals", params={"status": "dismissed"}).json()[0]["id"] == s["id"]
    assert c.post(f"/api/signals/{s['id']}/restore").json()["status"] == "active"
    assert c.post("/api/signals/999/dismiss").status_code == 404
    assert c.put("/api/settings", json={"signal_valid_bars": 0}).status_code == 422


def test_signal_with_journal_entry_or_plan_is_acted(env: Env) -> None:
    watch(env)
    env.now[0] = 26 * DAY + 1
    c = client(env)
    c.post("/api/scan")
    (s,) = [x for x in c.get("/api/signals").json() if x["strategy"] == "donchian"]
    env.now[0] = 100 * DAY  # срок давно вышел
    assert c.get("/api/signals").json()[0]["status"] == "expired"
    c.post("/api/journal", json={"market": "moex", "symbol": "XYZ", "side": "buy", "qty": 10, "price": 12,
                                 "ts": 1_700_000_000_000, "signal_id": s["id"]})
    assert {x["id"]: x["status"] for x in c.get("/api/signals").json()}[s["id"]] == "acted"


# --- тихие часы в движке ---


def engine_at(env: Env, hour_utc: float):
    env.services.engine._tz = UTC
    env.now[0] = 26 * DAY + int(hour_utc * HOUR)


def test_quiet_hours_store_signal_but_defer_notification_and_send_one_digest(env: Env) -> None:
    env.services.settings.update({"quiet_hours": Q, "signal_valid_bars": 3})
    watch(env)
    watch(env, "ABC")
    engine_at(env, 23)  # 23:00 UTC — тихие часы
    res = env.services.engine.scan()
    assert len(res.new) >= 2 and env.notifier.sent == [] and env.notifier.digests == []
    assert env.services.signals.list() and all(s.notified_at is None for s in env.services.signals.list())
    env.services.engine.scan()  # ещё один скан в тихие часы — по-прежнему тишина
    assert env.notifier.sent == [] and env.notifier.digests == []
    env.now[0] = 27 * DAY + 9 * HOUR  # 09:00 следующих суток: тихие часы кончились, сигналы ещё актуальны
    env.services.engine.scan()
    assert env.notifier.sent == [] and len(env.notifier.digests) == 1  # одним сообщением, не россыпью
    assert {x.symbol for x in env.notifier.digests[0]} == {"XYZ", "ABC"}
    assert all(s.notified_at is not None for s in env.services.signals.list())
    env.services.engine.scan()
    assert len(env.notifier.digests) == 1  # повторно не шлём


def test_signal_expired_during_quiet_hours_is_not_sent_afterwards(env: Env) -> None:
    env.services.settings.update({"quiet_hours": Q, "signal_valid_bars": 1})
    watch(env)
    engine_at(env, 23)
    env.services.engine.scan()
    env.now[0] = 40 * DAY + 12 * HOUR  # прошло много суток и тихие часы кончились: сигнал устарел
    env.services.engine.scan()
    assert env.notifier.sent == [] and env.notifier.digests == []
    assert all(s.notified_at is not None for s in env.services.signals.list())  # помечен, но не отправлен


def test_dismissed_signal_is_never_sent(env: Env) -> None:
    env.services.settings.update({"quiet_hours": Q})
    watch(env)
    engine_at(env, 23)
    env.services.engine.scan()
    for s in env.services.signals.list():
        env.services.signals.dismiss(s.id, 1)
    env.now[0] = 27 * DAY + 9 * HOUR
    env.services.engine.scan()
    assert env.notifier.sent == [] and env.notifier.digests == []


def test_outside_quiet_hours_signals_go_out_immediately_one_by_one(env: Env) -> None:
    env.services.settings.update({"quiet_hours": Q})
    watch(env)
    engine_at(env, 12)
    env.services.engine.scan()
    assert [s.symbol for s in env.notifier.sent if s.strategy == "donchian"] == ["XYZ"] and env.notifier.digests == []


def test_quiet_hours_validation(env: Env) -> None:
    c = client(env)
    assert c.put("/api/settings", json={"quiet_hours": {"enabled": True, "from": "23:00", "to": "07:30"}}).status_code == 200
    for bad in ({"enabled": "yes"}, {"from": "25:00"}, {"to": "7:5"}, {"enabled": True, "from": "10:00", "to": "10:00"},
                {"zzz": 1}, "night"):
        assert c.put("/api/settings", json={"quiet_hours": bad}).status_code == 422, bad


# --- пауза и состояние наблюдения ---


def test_paused_instrument_is_skipped_and_reported(env: Env) -> None:
    watch(env)
    watch(env, "ABC")
    env.now[0] = 26 * DAY + 1
    c = client(env)
    assert c.put("/api/settings", json={"paused_instruments": ["moex|XYZ"]}).status_code == 200
    res = env.services.engine.scan()
    assert {s.symbol for s in res.new} == {"ABC"}
    by = {i["symbol"]: i for i in c.get("/api/watch").json()["instruments"]}
    assert by["XYZ"]["paused"] is True and by["XYZ"]["status"] == "paused" and by["ABC"]["status"] == "ok"
    assert c.put("/api/settings", json={"paused_instruments": ["broken"]}).status_code == 422


def test_watch_status_reports_data_problems_and_states_the_app_must_be_running(env: Env) -> None:
    from compass.markets import MarketError

    watch(env)
    watch(env, "BAD")
    env.adapter.data["BAD"] = MarketError("источник лёг")
    env.now[0] = 26 * DAY + 1
    c = client(env)
    assert c.get("/api/watch").json()["last_scan_at"] is None
    env.services.engine.scan()
    w = c.get("/api/watch").json()
    assert w["last_scan_at"] == env.now[0] // 1000 and w["background_scanner"] is False
    by = {i["symbol"]: i for i in w["instruments"]}
    assert by["XYZ"]["status"] == "ok" and by["XYZ"]["last_ok_at"] and by["BAD"]["status"] == "error"
    assert "источник лёг" in by["BAD"]["message"]
    assert any("только при запущенном приложении" in n for n in w["notes"])
    assert [p["symbol"] for p in c.get("/api/day").json()["problem_sources"]] == ["BAD"]
    # источник вернулся — проблема пропала, а время последнего успеха сохранилось
    env.adapter.data["BAD"] = day_candles(BREAKOUT)
    env.services.engine.scan()
    assert c.get("/api/day").json()["problem_sources"] == []


def test_stale_cache_marks_state_and_creates_no_signal(env: Env) -> None:
    from compass.markets import MarketError

    watch(env)
    env.now[0] = 26 * DAY + 1
    env.services.engine.scan()
    env.services.signals._conn.execute("DELETE FROM signals")
    env.adapter.data["XYZ"] = MarketError("down")
    res = env.services.engine.scan()
    assert res.new == [] and res.errors
    (st,) = [s for s in env.services.signals.states() if s["symbol"] == "XYZ"]
    assert st["status"] == "stale" and st["last_ok_at"] is not None


# --- Telegram: дайджест ---


def test_digest_text_and_telegram_sends_single_message(caplog) -> None:
    text = format_digest([sig(id=i, symbol=f"T{i}") for i in range(12)])
    assert "накопившиеся за тихие часы: 12" in text and "и ещё 2" in text and "Не инвестиционная" in text
    calls = []
    client_ = httpx.Client(transport=httpx.MockTransport(lambda r: (calls.append(r), httpx.Response(200, json={}))[1]))
    TelegramNotifier("t", "c", client_).send_digest([sig(id=1), sig(id=2, symbol="ABC")])
    assert len(calls) == 1
    with caplog.at_level(logging.INFO, logger="compass.notify"):
        TelegramNotifier("", "", client_).send_digest([sig(id=1)])
    assert "telegram dry-run" in caplog.text and len(calls) == 1


# --- миграция ---


def test_v11_upgrade_marks_old_signals_as_already_notified(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    scripts = "".join(getattr(db, f"_V{i}") for i in range(1, 12))
    old.executescript(scripts + "PRAGMA user_version=11;")
    old.execute("INSERT INTO signals (market, symbol, tf, strategy, side, candle_ts, price, created_at) "
                "VALUES ('moex','SBER','1d','donchian','buy',1000,270.5,777)")
    old.commit()
    old.close()
    conn = db.connect(path)
    try:
        # старый сигнал не должен внезапно рассылаться после обновления
        assert conn.execute("SELECT notified_at, dismissed_at FROM signals").fetchone() == (777, None)
        assert conn.execute("SELECT COUNT(*) FROM scan_state").fetchone()[0] == 0
    finally:
        conn.close()
    assert sqlite3.connect(str(path) + ".v11.bak").execute("PRAGMA user_version").fetchone()[0] == 11
