"""Скринер: факты по закрытым свечам, без оценок; сбой одного источника не ломает остальные."""

from __future__ import annotations

import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.markets import MarketError
from compass.models import Candle, Instrument
from compass.screener import MIN_BARS, screen_row
from tests.conftest import DAY, Env, day_candles


def frame(closes, volumes=None, high_add=1.0, low_sub=1.0) -> pd.DataFrame:
    volumes = volumes or [100.0] * len(closes)
    return pd.DataFrame(
        {
            "ts": [i * DAY for i in range(len(closes))], "open": closes, "high": [c + high_add for c in closes],
            "low": [c - low_sub for c in closes], "close": closes, "volume": volumes,
        }
    )


def test_screen_row_numbers_by_hand() -> None:
    closes = [100.0] * 30 + [110.0]  # последняя свеча +10% к предыдущей
    vols = [100.0] * 30 + [300.0]  # объём втрое выше среднего за 20 свечей
    r = screen_row(frame(closes, vols), [108.0, 90.0], {"donchian": 1})
    assert r["last"] == 110 and r["change_bar_pct"] == 10.0 and r["change_20_pct"] == 10.0
    assert r["volume_ratio"] == 3.0
    assert r["above_sma20"] is True and r["above_sma50"] is None  # свечей меньше 50 — не выдумываем
    assert r["nearest_level"] == {"price": 108.0, "distance_pct": 1.85}
    assert r["rule_state"] == {"donchian": 1} and r["bars"] == 31
    assert r["atr_pct"] is not None and 1.5 < r["atr_pct"] < 2.5  # диапазон 2 при цене ~100–110


def test_screen_row_handles_flat_series_and_missing_levels() -> None:
    r = screen_row(frame([50.0] * 40), [], {})
    assert r["nearest_level"] is None and r["change_bar_pct"] == 0.0
    assert r["rsi14"] is None or math.isfinite(r["rsi14"])  # плоский ряд не должен давать inf/NaN в JSON
    zero_vol = screen_row(frame([50.0] * 40, [0.0] * 40), [], {})
    assert zero_vol["volume_ratio"] is None  # деление на нулевой средний объём не показываем


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


def watch(env: Env, symbol: str, closes) -> None:
    env.services.watchlist.add(Instrument(symbol, symbol, "moex"))
    env.adapter.data[symbol] = day_candles(closes) if not isinstance(closes, Exception) else closes


def test_screener_api_rows_errors_levels_signals_and_no_future(env: Env) -> None:
    watch(env, "XYZ", [10.0] * 45 + [12.0])  # пробой на последней закрытой свече
    watch(env, "SHORT", [10.0] * 10)
    watch(env, "BAD", MarketError("источник лёг"))
    env.now[0] = 46 * DAY + 1
    c = client(env)
    c.post("/api/levels", json={"market": "moex", "symbol": "XYZ", "price": 11.0, "label": "уровень"})
    c.post("/api/scan")
    body = c.get("/api/screener").json()
    rows = {r["symbol"]: r for r in body["rows"]}
    assert set(rows) == {"XYZ", "SHORT", "BAD"} and rows["SHORT"]["status"] == "short" and rows["BAD"]["status"] == "error"
    assert "источник лёг" in rows["BAD"]["message"]
    x = rows["XYZ"]
    assert x["status"] == "ok" and x["last"] == 12.0 and x["nearest_level"]["price"] == 11.0
    assert x["rule_state"]["donchian"] == 1 and any(s["strategy"] == "donchian" and s["side"] == "buy" for s in x["signals"])
    assert any("не рекомендация" in n for n in body["notes"])
    # незакрытая свеча в скринер не попадает: до закрытия последней свечи «последняя цена» — предыдущая
    env.now[0] = 45 * DAY + 1  # свеча 45 (цена 12) ещё не закрыта
    assert {r["symbol"]: r for r in c.get("/api/screener").json()["rows"]}["XYZ"]["last"] == 10.0


def test_screener_marks_stale_cache_and_paused(env: Env) -> None:
    watch(env, "XYZ", [10.0] * 46)
    env.now[0] = 47 * DAY
    c = client(env)
    assert c.get("/api/screener").json()["rows"][0]["status"] == "ok"
    env.adapter.data["XYZ"] = MarketError("down")
    c.put("/api/settings", json={"paused_instruments": ["moex|XYZ"]})
    r = c.get("/api/screener").json()["rows"][0]
    assert r["status"] == "stale" and "кэш" in r["message"] and r["paused"] is True


def test_min_bars_constant_is_used(env: Env) -> None:
    watch(env, "EDGE", [10.0] * (MIN_BARS - 1))
    env.now[0] = 100 * DAY
    assert client(env).get("/api/screener").json()["rows"][0]["status"] == "short"
    env.adapter.data["EDGE"] = [Candle(i * DAY, 10, 11, 9, 10, 5.0) for i in range(MIN_BARS)]
    assert client(env).get("/api/screener").json()["rows"][0]["status"] in ("ok", "stale")
    assert pytest is not None
