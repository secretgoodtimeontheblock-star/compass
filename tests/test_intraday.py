"""Скальпинг и внутридневная торговля: честные допущения, расходы, короткая история, задержка данных."""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.models import Candle, Instrument, feed_delay_s
from compass.notify import format_signal
from compass.signals import Signal
from tests.conftest import Env

MIN = 60_000
DAY = 86_400_000
MSK = 3 * 3_600_000


def minute_candles(n: int, start_ms: int = 0, drift: float = 0.0, amp: float = 1.0) -> list[Candle]:
    out = []
    price = 100.0
    for i in range(n):
        wave = amp * (1.5 * math.sin(i / 8) + 0.4 * math.sin(i / 3))
        o, c = price, 100 + wave + drift * i
        out.append(Candle(start_ms + i * MIN, o, max(o, c) + 0.2, min(o, c) - 0.2, c, 100.0))
        price = c
    return out


def setup(env: Env, n: int = 3000, drift: float = 0.0, amp: float = 1.0) -> TestClient:
    env.adapter.timeframes = ("1d", "1m")
    env.adapter.data["SBER"] = minute_candles(n, start_ms=(20_000 * DAY) - 3 * 3_600_000 + 10 * 3_600_000, drift=drift, amp=amp)
    env.adapter.lot = 1
    env.now[0] = 30_000 * DAY
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


BODY = {"market": "moex", "symbol": "SBER", "tf": "1m", "strategy": "sma_cross", "params": {"fast": 3, "slow": 12},
        "limit": 3000}


# --- задержка данных ---


def test_feed_delay_only_for_intraday_moex() -> None:
    assert feed_delay_s("moex", "1m") == 900 and feed_delay_s("moex", "1h") == 900
    assert feed_delay_s("moex", "1d") == 0 and feed_delay_s("crypto", "1m") == 0


def test_telegram_text_warns_about_delayed_intraday_only() -> None:
    s = Signal("moex", "SBER", "1m", "donchian", "buy", 1000, 274.5, 270.0)
    assert "задержкой ~15 мин" in format_signal(s)
    assert "задержкой" not in format_signal(Signal("moex", "SBER", "1d", "donchian", "buy", 1000, 274.5, 270.0))
    assert "задержкой" not in format_signal(Signal("crypto", "BTC/USDT", "1m", "donchian", "buy", 1000, 84000.0, 83000.0))


def test_signal_dto_flags_late_signals_when_delay_exceeds_validity(env: Env) -> None:
    env.services.signals.insert(Signal("moex", "SBER", "1m", "donchian", "buy", 1000, 274.5, 270.0))
    env.services.signals.insert(Signal("moex", "SBER", "1h", "donchian", "buy", 1000, 274.5, 270.0))
    env.services.signals.insert(Signal("moex", "SBER", "1d", "donchian", "buy", 1000, 274.5, 270.0))
    env.services.signals.insert(Signal("crypto", "BTC/USDT", "1m", "donchian", "buy", 1000, 84000.0, 83000.0))
    env.now[0] = 1000 + 2 * MIN
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    by = {(s["market"], s["tf"]): s for s in c.get("/api/signals").json()}
    assert by[("moex", "1m")]["late"] is True and by[("moex", "1m")]["delay_seconds"] == 900  # 3 мин жизни < 15 мин задержки
    assert by[("moex", "1h")]["late"] is False and by[("moex", "1h")]["delay_seconds"] == 900
    assert by[("moex", "1d")]["delay_seconds"] == 0 and by[("crypto", "1m")]["late"] is False


# --- внутридневной бэктест ---


def test_intraday_backtest_reports_costs_history_and_delay(env: Env) -> None:
    c = setup(env)
    r = c.post("/api/backtest", json=BODY)
    assert r.status_code == 200
    body = r.json()
    ib = body["intraday"]
    assert ib["tf"] == "1m" and ib["trading_days"] >= 2 and ib["feed_delay_seconds"] == 900
    assert ib["round_trip_cost_pct"] == pytest.approx(0.2)  # 2·(0.05 + 0.05)
    assert [x["multiplier"] for x in ib["cost_stress"]] == [1, 2, 3]
    assert ib["cost_stress"][0]["return_pct"] == body["metrics"]["total_return_pct"]
    assert ib["cost_stress"][2]["return_pct"] < ib["cost_stress"][0]["return_pct"] and ib["cost_stress"][0]["trades"] > 10
    text = " ".join(body["warnings"])
    assert "торговых дней" in text and "через ночь" in text and "задержкой ~15 мин" in text
    # дневной таймфрейм такого блока не получает
    daily = c.post("/api/backtest", json={**BODY, "tf": "1d", "limit": 200})
    assert daily.status_code == 200 and daily.json()["intraday"] is None  # у дневных свечей блока нет


def test_close_eod_flat_overnight_via_api_and_no_night_warning(env: Env) -> None:
    c = setup(env, drift=0.03, amp=0.02)  # растущий рынок: правило почти всё время «в рынке»
    body = c.post("/api/backtest", json={**BODY, "close_eod": True}).json()
    assert body["metrics"]["eod_exits"] >= 1 and body["run_card"]["rules"]["close_eod"] is True
    assert any("закрываются к концу торгового дня" in a for a in body["run_card"]["assumptions"])
    assert not any("через ночь" in w for w in body["warnings"])
    for t in body["trades"]:
        if t["exit_ts"] is not None:
            assert (t["entry_ts"] + MSK) // DAY == (t["exit_ts"] + MSK) // DAY  # сутки по Москве совпадают
    assert {t["exit_reason"] for t in body["trades"]} & {"eod"}


def test_close_eod_combines_with_stop_and_risk_sizing(env: Env) -> None:
    c = setup(env, drift=0.03, amp=0.02)
    body = c.post("/api/backtest", json={**BODY, "close_eod": True, "stop_atr_mult": 3, "risk_pct": 0.5}).json()
    assert body["run_card"]["rules"]["stop_atr_mult"] == 3 and body["metrics"]["eod_exits"] >= 1
    assert any(t["exit_reason"] in ("stop", "gap_stop") for t in body["trades"]) or body["metrics"]["stops"] == 0


def test_deep_history_limits_and_short_history_warning(env: Env) -> None:
    c = setup(env)
    assert c.get("/api/candles", params={"market": "moex", "symbol": "SBER", "tf": "1m", "limit": 20000}).status_code == 200
    assert c.get("/api/candles", params={"market": "moex", "symbol": "SBER", "tf": "1m", "limit": 20001}).status_code == 422
    assert c.post("/api/backtest", json={**BODY, "limit": 20000}).status_code == 200
    assert c.post("/api/backtest", json={**BODY, "limit": 20001}).status_code == 422


# --- частота проверки против длины свечи ---


def test_watch_warns_when_scan_interval_is_longer_than_signal_window(env: Env) -> None:
    env.adapter.timeframes = ("1d", "1m")
    env.services.settings._tfs["moex"] = ("1d", "1m")
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert c.get("/api/watch").json()["warnings"] == [] or all("moex" not in w or "проверка" not in w for w in c.get("/api/watch").json()["warnings"])
    c.put("/api/settings", json={"tf_moex": "1m", "scan_interval_min": 15, "signal_valid_bars": 3})
    warns = " ".join(c.get("/api/watch").json()["warnings"])
    assert "проверка идёт раз в 15 мин" in warns and "3 мин" in warns and "задержкой ~15 мин" in warns
    c.put("/api/settings", json={"scan_interval_min": 1})
    warns = " ".join(c.get("/api/watch").json()["warnings"])
    assert "проверка идёт раз в" not in warns and "задержкой" in warns  # частоту исправили, а задержку — нет


def test_scan_interval_can_be_one_minute_for_intraday(env: Env) -> None:
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert c.put("/api/settings", json={"scan_interval_min": 1}).status_code == 200
    assert c.put("/api/settings", json={"scan_interval_min": 0}).status_code == 422
    assert Instrument("X", "X", "moex").symbol == "X"


def test_cost_stress_warning_rules() -> None:
    from compass.validation import cost_stress_warning

    def stress(a, b, c):
        return [{"multiplier": 1, "return_pct": a}, {"multiplier": 2, "return_pct": b}, {"multiplier": 3, "return_pct": c}]

    assert "втрое выше" in cost_stress_warning(stress(5.0, 1.0, -0.5))
    assert "втрое выше" in cost_stress_warning(stress(5.0, 1.0, 0.0))  # ровно ноль — тоже «не в плюсе»
    assert cost_stress_warning(stress(5.0, 3.0, 1.0)) is None  # устойчив к расходам
    assert cost_stress_warning(stress(-1.0, -2.0, -3.0)) is None  # и так в минусе: другое предупреждение не нужно
