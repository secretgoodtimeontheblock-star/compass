"""Свинг на 100 USDT: потеря при стопе 30–100% маржи, прибыль на цели 30–300%."""

from __future__ import annotations

import pytest

from compass.settings import DEFAULTS
from compass.signals import Signal
from compass.swing import MAX_LEVERAGE, outcome, swing_plan


def test_crypto_default_timeframe_is_daily() -> None:
    assert DEFAULTS["tf_crypto"] == "1d"


@pytest.mark.parametrize("atr_pct", [0.2, 0.8, 1.5, 3, 6, 12, 25, 40])
def test_hundred_usdt_stays_inside_the_payoff_band(atr_pct: float) -> None:
    price = 100.0
    plan = swing_plan(price, price * atr_pct / 100)
    assert plan is not None and plan.stake == 100
    assert 1 <= plan.leverage <= MAX_LEVERAGE == 200
    got = outcome(price, plan.stop, plan.target, plan.leverage, plan.stake)
    assert got is not None
    assert 30 <= got["loss_pct"] <= 100
    assert 30 <= got["profit_pct"] <= 300
    assert got["profit_usdt"] >= got["loss_usdt"]


def test_wide_daily_stop_uses_modest_leverage() -> None:
    # 2·ATR = 12% цены → чтобы стоп стоил ~50% от 100 USDT, хватает около 4×
    plan = swing_plan(2500.0, 2500.0 * 0.06)
    assert plan is not None
    assert plan.leverage <= 5
    got = outcome(2500.0, plan.stop, plan.target, plan.leverage, plan.stake)
    assert got is not None and 40 <= got["loss_pct"] <= 60


def test_leverage_stops_at_200_and_names_the_loss_risk() -> None:
    # 2·ATR = 0,1% цены: без потолка плечо было бы 500×, с потолком — 200×.
    plan = swing_plan(100.0, 0.05)
    assert plan is not None and plan.leverage == 200
    got = outcome(100.0, plan.stop, plan.target, plan.leverage, plan.stake)
    assert got is not None and got["loss_pct"] >= 30
    assert "Высокий риск потери" in got["risk_note"] and "200" in got["risk_note"]


def test_store_roundtrips_the_swing_plan(env) -> None:
    plan = swing_plan(80.0, 2.4)
    assert plan is not None
    saved = Signal(
        "crypto", "SOL/USDT", "1d", "donchian", "buy", 1, 80.0, plan.stop,
        target=plan.target, leverage=plan.leverage, stake=plan.stake,
    )
    assert env.services.signals.insert(saved)
    got = env.services.signals.list(market="crypto")[0]
    assert (got.target, got.leverage, got.stake) == (plan.target, plan.leverage, plan.stake)
    assert got.stop == plan.stop
