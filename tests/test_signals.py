from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.markets import MarketError
from compass.models import Instrument
from compass.notify import DISCLAIMER, TelegramNotifier, format_signal
from compass.signals import Signal
from tests.conftest import DAY, Env, day_candles

FLAT = [10.0] * 25
BREAKOUT = FLAT + [12.0]  # пробой канала на последней свече → вход по donchian


def watch(env: Env, symbol: str = "XYZ") -> None:
    env.services.watchlist.add(Instrument(symbol, symbol, "moex"))


def closed_after(n_candles: int) -> int:
    """Момент, когда свеча с индексом n_candles-1 уже закрыта."""
    return n_candles * DAY + 1


def donchian(signals: list[Signal]) -> list[Signal]:
    return [s for s in signals if s.strategy == "donchian"]


def test_breakout_on_last_closed_candle_creates_buy_with_stop(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    res = env.services.engine.scan()
    (s,) = donchian(res.new)
    assert s.side == "buy" and s.price == 12.0 and s.candle_ts == 25 * DAY
    assert s.stop is not None and 0 < s.stop < s.price
    assert donchian(env.notifier.sent) == [s]


def test_rescan_does_not_duplicate_or_renotify(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    env.services.engine.scan()
    sent = len(env.notifier.sent)
    again = env.services.engine.scan()
    assert again.new == [] and len(env.notifier.sent) == sent
    assert len(donchian(env.services.signals.list())) == 1


def test_unclosed_last_candle_is_ignored(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = 25 * DAY + 3_600_000  # свеча №25 идёт всего час — ещё не закрыта
    assert donchian(env.services.engine.scan().new) == []


def test_exit_signal(env: Env) -> None:
    watch(env)
    closes = FLAT + [12.0] * 5 + [7.0]
    env.adapter.data["XYZ"] = day_candles(closes)
    env.now[0] = closed_after(len(closes))
    (s,) = donchian(env.services.engine.scan().new)
    assert s.side == "exit" and s.stop is None


def test_one_broken_source_does_not_stop_scan(env: Env) -> None:
    watch(env, "BAD")
    watch(env, "XYZ")
    env.adapter.data["BAD"] = MarketError("источник упал")
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    res = env.services.engine.scan()
    assert len(donchian(res.new)) == 1
    assert len(res.errors) == 1 and "BAD" in res.errors[0]


def test_too_short_history_is_skipped_quietly(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles([10.0, 11.0])
    env.now[0] = closed_after(2)
    res = env.services.engine.scan()
    assert res.new == [] and res.errors == []


def test_stale_cache_does_not_create_signals_and_recovers(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    env.services.cache.get("moex", "XYZ", "1d", 300)
    env.adapter.data["XYZ"] = MarketError("down")
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    result = client.post("/api/scan").json()
    assert result["new"] == []
    assert "XYZ" in result["errors"][0] and "приостановлен" in result["errors"][0]
    assert env.services.signals.list() == [] and env.notifier.sent == []
    assert client.get("/api/candles", params={"market": "moex", "symbol": "XYZ"}).json()["stale"]

    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    assert donchian(env.services.engine.scan().new)
    assert env.notifier.sent
    assert env.services.engine.scan().new == []


# --- уведомления ---


def sample(side: str = "buy") -> Signal:
    return Signal("moex", "SBER", "1d", "donchian", side, 0, 272.4, 260.1 if side == "buy" else None)


def test_format_signal_has_disclaimer_and_levels() -> None:
    text = format_signal(sample())
    assert "ВХОД" in text and "SBER" in text and "260.1" in text and DISCLAIMER in text
    assert "ВЫХОД" in format_signal(sample("exit"))


def test_telegram_dry_run_without_token_does_not_call_network(caplog) -> None:
    def boom(_req):
        raise AssertionError("сеть трогать нельзя")

    n = TelegramNotifier(token="", chat_id="", client=httpx.Client(transport=httpx.MockTransport(boom)))
    with caplog.at_level(logging.INFO, logger="compass.notify"):
        n.send(sample())
    assert "dry-run" in caplog.text


def test_telegram_failure_is_swallowed_and_token_not_logged(caplog) -> None:
    secret = "123456:SECRET-TOKEN"
    n = TelegramNotifier(
        token=secret,
        chat_id="42",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    with caplog.at_level(logging.WARNING, logger="compass.notify"):
        n.send(sample())  # не должен бросить
    assert "Telegram" in caplog.text and secret not in caplog.text


# --- настройки и API ---


def test_saved_strategy_replaces_the_default_scan(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    env.services.settings.update(
        {"instrument_strategies": {"moex|XYZ": {"strategy": "rsi_reversion", "params": {}}}}
    )
    res = env.services.engine.scan()
    assert donchian(res.new) == []
    assert {s.strategy for s in res.new} <= {"rsi_reversion"}
    with pytest.raises(ValueError):
        env.services.settings.update({"instrument_strategies": {"bad": {"strategy": "donchian"}}})


def test_settings_defaults_update_and_validation(env: Env) -> None:
    s = env.services.settings
    assert s.get("risk_pct") == 1.0
    assert s.update({"capital": 250_000, "scan_interval_min": 5})["capital"] == 250_000.0
    assert s.get("scan_interval_min") == 5  # хранится между вызовами
    for bad in [{"capital": 0}, {"risk_pct": 101}, {"scan_interval_min": 0}, {"tf_moex": "4h"}, {"x": 1}, {"capital": True}]:
        with pytest.raises(ValueError):
            s.update(bad)
    assert s.get("capital") == 250_000.0  # неудачное обновление ничего не испортило


def test_settings_update_is_atomic(env: Env) -> None:
    with pytest.raises(ValueError):
        env.services.settings.update({"capital": 500, "risk_pct": 999})
    assert env.services.settings.get("capital") == 100_000.0


def test_api_backtest_signals_risk_settings(env: Env) -> None:
    closes = [100 + (i % 7) * 2 + i * 0.5 for i in range(120)]
    env.adapter.data["SBER"] = day_candles(closes)
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")

    r = client.post("/api/backtest", json={"market": "moex", "symbol": "SBER", "strategy": "donchian"})
    assert r.status_code == 200
    body = r.json()
    assert body["metrics"]["candles"] == 120 and len(body["equity"]) == 120
    assert client.post("/api/backtest", json={"market": "moex", "symbol": "SBER", "strategy": "nope"}).status_code == 404
    bad = client.post(
        "/api/backtest",
        json={"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 60, "slow": 20}},
    )
    assert bad.status_code == 422 and "короче" in bad.json()["detail"]

    r = client.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190})
    assert r.status_code == 200 and r.json()["qty"] == 100 and r.json()["lot_size"] == 10
    assert client.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 210}).status_code == 422

    assert client.put("/api/settings", json={"capital": 50_000}).json()["capital"] == 50_000
    assert client.put("/api/settings", json={"capital": -1}).status_code == 422

    assert [s["id"] for s in client.get("/api/strategies").json()] == ["sma_cross", "rsi_reversion", "donchian"]


def test_api_scan_and_seen_flow(env: Env) -> None:
    watch(env)
    env.adapter.data["XYZ"] = day_candles(BREAKOUT)
    env.now[0] = closed_after(len(BREAKOUT))
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert any(s["strategy"] == "donchian" for s in client.post("/api/scan").json()["new"])
    assert len(client.get("/api/signals", params={"unseen": True}).json()) >= 1
    assert client.post("/api/signals/seen").json()["marked"] >= 1
    assert client.get("/api/signals", params={"unseen": True}).json() == []
    assert len(client.get("/api/signals").json()) >= 1  # история осталась
