"""Модель исполнения: спред и ликвидность в бэктесте, профили рынков. Числа посчитаны вручную."""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from compass import execution
from compass.api.app import create_app
from compass.backtest import backtest
from compass.execution import ExecutionModel
from tests.conftest import DAY, Env, day_candles


def frame(opens, closes=None, volumes=None) -> pd.DataFrame:
    closes = closes or opens
    n = len(opens)
    return pd.DataFrame(
        {
            "ts": [i * DAY for i in range(n)], "open": opens, "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
            "low": [min(o, c) for o, c in zip(opens, closes, strict=True)], "close": closes,
            "volume": volumes or [1_000_000.0] * n,
        }
    )


# --- модель ---


def test_effective_slippage_adds_half_the_spread_and_round_trip_counts_it_whole() -> None:
    m = ExecutionModel(fee_pct=0.05, slippage_pct=0.05, spread_pct=0.10)
    assert m.effective_slippage_pct == pytest.approx(0.10)  # 0.05 + 0.10/2
    assert m.round_trip_cost_pct == pytest.approx(0.30)  # 2·0.05 + 2·0.05 + 0.10
    w = m.worse(3)
    assert (w.fee_pct, w.slippage_pct, w.spread_pct) == pytest.approx((0.15, 0.15, 0.30))


def test_model_validates_and_overrides_come_from_the_market_profile() -> None:
    with pytest.raises(ValueError, match="Спред"):
        ExecutionModel(0.05, 0.05, 100).check()
    with pytest.raises(ValueError, match="Доля объёма"):
        ExecutionModel(0.05, 0.05, 0.0, 0).check()
    base = execution.model_for("crypto")
    assert base.fee_pct == 0.1 and base.spread_pct == execution.DEFAULT_SPREAD_PCT["crypto"]
    custom = execution.model_for("moex", fee_pct=0.3, spread_pct=0.0, max_participation_pct=10)
    assert (custom.fee_pct, custom.spread_pct, custom.max_participation_pct) == (0.3, 0.0, 10)
    assert execution.model_for("unknown").spread_pct == 0.0  # для неизвестного рынка спред не выдумывается


def test_cost_story_tells_in_words_how_the_result_changes() -> None:
    m = ExecutionModel(0.05, 0.05, 0.05)
    text = execution.cost_story(8.0, -2.0, m)
    assert "+8.0%" in text and "-2.0%" in text and "исчезает" in text
    assert "исчезает" not in execution.cost_story(8.0, 5.0, m)


def test_participation_is_unknown_for_zero_volume() -> None:
    assert execution.participation_pct(50, 1000) == 5.0
    assert execution.participation_pct(50, 0) is None


# --- бэктест ---


def test_spread_worsens_entry_and_exit_by_half_each() -> None:
    df = frame([100.0] * 5)
    target = pd.Series([0, 1, 1, 0, 0])
    plain = backtest(df, target, 100_000, 0.0, 0.0)
    spread = backtest(df, target, 100_000, 0.0, 0.0, spread_pct=0.2)
    assert plain.metrics["total_return_pct"] == 0
    t = spread.trades[0]
    assert t.entry_price == pytest.approx(100.1) and t.exit_price == pytest.approx(99.9)  # ±0.1% = половина спреда
    assert spread.metrics["total_return_pct"] == pytest.approx(-0.2, abs=0.01)  # круг обошёлся в спред целиком
    assert spread.metrics["spread_pct"] == 0.2


def test_participation_is_reported_even_without_a_cap() -> None:
    df = frame([100.0] * 5, volumes=[1000.0] * 5)
    res = backtest(df, pd.Series([0, 1, 1, 0, 0]), 10_000, 0.0, 0.0)
    assert res.trades[0].qty == pytest.approx(100)  # 10 000 / 100
    assert res.metrics["max_participation_pct"] == pytest.approx(10.0)  # 100 из 1000
    assert res.metrics["liquidity_capped_entries"] == 0


def test_participation_cap_shrinks_entry_to_the_allowed_share_of_volume() -> None:
    df = frame([100.0] * 5, volumes=[1000.0] * 5)
    target = pd.Series([0, 1, 1, 0, 0])
    res = backtest(df, target, 10_000, 0.0, 0.0, max_participation_pct=5)
    assert res.trades[0].qty == pytest.approx(50)  # 5% от 1000
    assert res.metrics["liquidity_capped_entries"] == 1 and res.metrics["max_participation_pct"] == pytest.approx(5.0)
    assert res.metrics["illiquid_exits"] == 0


def test_cap_rounds_down_to_lot_when_rules_carry_a_lot() -> None:
    from compass.backtest import Rules

    df = frame([100.0] * 5, volumes=[1000.0] * 5)
    res = backtest(df, pd.Series([0, 1, 1, 0, 0]), 10_000, 0.0, 0.0, Rules(lot=10), max_participation_pct=7.5)
    assert res.trades[0].qty == 70  # 75 акций допустимо, лот 10 → 70


def test_zero_volume_fill_is_counted_not_capped() -> None:
    df = frame([100.0] * 5, volumes=[0.0] * 5)
    res = backtest(df, pd.Series([0, 1, 1, 0, 0]), 10_000, 0.0, 0.0, max_participation_pct=5)
    assert res.trades[0].qty == pytest.approx(100)  # неизвестный объём не режет размер
    assert res.metrics["zero_volume_fills"] == 2 and res.metrics["max_participation_pct"] is None


def test_exit_larger_than_cap_is_flagged_not_split() -> None:
    df = frame([100.0] * 6, volumes=[1000.0, 1000.0, 1000.0, 100.0, 1000.0, 1000.0])
    res = backtest(df, pd.Series([0, 1, 1, 1, 0, 0]), 4_000, 0.0, 0.0, max_participation_pct=5)
    assert res.trades[0].qty == pytest.approx(40)  # допустимо 5% от 1000 = 50, денег хватает на 40
    assert res.metrics["illiquid_exits"] == 0
    df2 = frame([100.0] * 6, volumes=[1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 100.0])
    res2 = backtest(df2, pd.Series([0, 1, 1, 1, 0, 0]), 4_000, 0.0, 0.0, max_participation_pct=5)
    assert res2.metrics["illiquid_exits"] == 1  # выход по открытию свечи 5: 40 из 100 — 40% объёма


def test_invalid_cap_is_rejected() -> None:
    df = frame([100.0] * 5)
    with pytest.raises(ValueError, match="Доля объёма"):
        backtest(df, pd.Series([0, 1, 1, 0, 0]), 1000, 0.0, 0.0, max_participation_pct=0)
    with pytest.raises(ValueError):
        backtest(df, pd.Series([0, 1, 1, 0, 0]), 1000, 0.0, 0.0, spread_pct=-1)


# --- API ---


def wavy(n: int = 300) -> list[float]:
    import math

    return [100 + 20 * math.sin(i / 8) + i * 0.02 for i in range(n)]


def client(env: Env) -> TestClient:
    env.adapter.data["SBER"] = day_candles(wavy())
    env.now[0] = 10_000 * DAY
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


BODY = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 300}


def test_backtest_api_uses_market_spread_by_default_and_reports_execution(env: Env) -> None:
    r = client(env).post("/api/backtest", json=BODY).json()
    ex = r["execution"]
    assert ex["model"]["spread_pct"] == execution.DEFAULT_SPREAD_PCT["moex"]
    assert ex["model"]["round_trip_cost_pct"] == pytest.approx(0.25)
    assert [x["multiplier"] for x in ex["cost_stress"]] == [1, 2, 3]
    assert ex["cost_stress"][0]["return_pct"] == r["metrics"]["total_return_pct"]
    assert "Историческая оценка исходит из расходов" in ex["story"]
    assert r["run_card"]["spread_pct"] == 0.05 and any("спред" in a for a in r["run_card"]["assumptions"])


def test_spread_lowers_the_result_and_zero_spread_is_allowed(env: Env) -> None:
    c = client(env)
    free = c.post("/api/backtest", json={**BODY, "spread_pct": 0}).json()
    wide = c.post("/api/backtest", json={**BODY, "spread_pct": 1.0}).json()
    assert wide["metrics"]["total_return_pct"] < free["metrics"]["total_return_pct"]
    assert free["run_card"]["spread_pct"] == 0
    assert c.post("/api/backtest", json={**BODY, "spread_pct": -1}).status_code == 422


def test_backtest_api_warns_when_orders_would_take_much_of_the_candle_volume(env: Env) -> None:
    c = client(env)
    r = c.post("/api/backtest", json={**BODY, "capital": 10_000_000}).json()  # свечи с объёмом 100 штук
    assert r["metrics"]["max_participation_pct"] > execution.LIQUIDITY_WARN_PCT
    assert any("объёма свечи" in w for w in r["warnings"])
    capped = c.post("/api/backtest", json={**BODY, "capital": 10_000_000, "max_volume_pct": 5}).json()
    assert capped["execution"]["model"]["max_participation_pct"] == 5
    assert capped["metrics"]["liquidity_capped_entries"] > 0
    assert any("урезан по объёму" in w for w in capped["warnings"])
    assert c.post("/api/backtest", json={**BODY, "max_volume_pct": 0}).status_code == 422


def test_validate_api_stress_includes_spread(env: Env) -> None:
    from tests.test_oos import GRID
    from tests.test_oos import wavy as oos_wavy

    env.adapter.data["SBER"] = day_candles(oos_wavy())
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    body = {**BODY, "params": {"fast": 3, "slow": 12}, "limit": 600}
    r = c.post("/api/validate", json={**body, "grid": GRID, "train_pct": 70}).json()
    stress = r["cost_sensitivity"]
    assert [x["spread_pct"] for x in stress] == pytest.approx([0.05, 0.10, 0.15])
    assert r["run_card"]["spread_pct"] == 0.05


def test_risk_api_includes_half_the_spread_in_effective_slippage(env: Env) -> None:
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    c.put("/api/accounts/moex", json={"capital": 1_000_000})
    base = {"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190}
    r = c.post("/api/risk", json=base).json()
    assert r["spread_pct"] == 0.05 and r["slippage_input_pct"] == 0.05
    assert r["slippage_pct"] == pytest.approx(0.075)  # 0.05 + 0.05/2
    flat = c.post("/api/risk", json={**base, "spread_pct": 0}).json()
    assert flat["slippage_pct"] == pytest.approx(0.05) and flat["risk_amount"] < r["risk_amount"] or flat["qty"] >= r["qty"]


def test_replay_session_inherits_the_same_effective_slippage(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles(wavy(400))
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    s = c.post("/api/replay", json={"market": "moex", "symbol": "SBER", "replay_bars": 60}).json()
    assert s["slippage_pct"] == pytest.approx(0.075) and s["fee_pct"] == 0.05
    z = c.post("/api/replay", json={"market": "moex", "symbol": "SBER", "replay_bars": 60, "spread_pct": 0}).json()
    assert z["slippage_pct"] == pytest.approx(0.05)


def test_markets_api_carries_beginner_profile_with_plain_language(env: Env) -> None:
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    (m,) = c.get("/api/markets").json()
    prof = m["profile"]
    assert prof["market"] == "moex" and prof["label"] == "МосБиржа (акции)"
    assert prof["round_trip_cost_pct"] == pytest.approx(0.25) and "обходится" in prof["summary"]
    assert any("лот" in x for x in prof["why_it_differs"]) and prof["session"]["continuous"] is False
    crypto = execution.PROFILES["crypto"].dto()
    assert crypto["session"]["continuous"] is True and crypto["spread_pct"] == execution.DEFAULT_SPREAD_PCT["crypto"]
