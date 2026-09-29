from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.backtest import backtest
from compass.indicators import atr, rsi, sma
from compass.risk import position_size
from compass.strategies import STRATEGIES


def frame(opens, closes=None, highs=None, lows=None) -> pd.DataFrame:
    closes = closes if closes is not None else opens
    return pd.DataFrame(
        {
            "ts": [i * 1000 for i in range(len(opens))],
            "open": opens,
            "high": highs if highs is not None else [max(o, c) for o, c in zip(opens, closes)],
            "low": lows if lows is not None else [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1.0] * len(opens),
        }
    ).astype(float).assign(ts=lambda d: d["ts"].astype(int))


# --- индикаторы ---


def test_rsi_of_rising_series_is_100() -> None:
    r = rsi(pd.Series(np.arange(1.0, 40.0)), 14)
    assert r.iloc[:14].isna().all() and r.iloc[-1] == pytest.approx(100.0)


def test_rsi_of_falling_series_is_0() -> None:
    assert rsi(pd.Series(np.arange(40.0, 1.0, -1.0)), 14).iloc[-1] == pytest.approx(0.0)


def test_atr_of_constant_range() -> None:
    df = frame([10.0] * 30, highs=[11.0] * 30, lows=[9.0] * 30)
    assert atr(df, 14).iloc[-1] == pytest.approx(2.0)


def test_sma_warmup_is_nan() -> None:
    assert sma(pd.Series([1.0, 2, 3, 4]), 3).isna().sum() == 2


# --- бэктест ---


def test_fill_is_at_next_open_not_signal_close() -> None:
    df = frame(opens=[10, 10, 10, 20, 20, 20], closes=[10, 10, 15, 20, 20, 30])
    target = pd.Series([0, 0, 1, 1, 1, 1])  # решение на закрытии свечи 2
    res = backtest(df, target, fee_pct=0, slippage_pct=0)
    (t,) = res.trades
    assert t.entry_ts == 3000 and t.entry_price == 20  # открытие свечи 3, не цена 15
    assert t.exit_ts is None  # позиция открыта
    assert res.metrics["open_trade"] == 1
    assert t.pnl_pct == pytest.approx(30 / 20 - 1)  # по закрытию последней свечи


def test_round_trip_pnl_and_costs() -> None:
    df = frame(opens=[10, 10, 10, 12, 12, 12], closes=[10, 10, 11, 12, 12, 12])
    target = pd.Series([0, 1, 1, 0, 0, 0])
    free = backtest(df, target, fee_pct=0, slippage_pct=0)
    assert free.trades[0].entry_price == 10 and free.trades[0].exit_price == 12
    assert free.trades[0].pnl_pct == pytest.approx(0.2)
    costly = backtest(df, target, fee_pct=0.1, slippage_pct=0.1)
    assert costly.trades[0].pnl_pct < free.trades[0].pnl_pct
    assert costly.metrics["trades"] == 1


def test_signal_on_last_candle_is_not_executed() -> None:
    df = frame(opens=[10, 10, 10, 10])
    res = backtest(df, pd.Series([0, 0, 0, 1]), fee_pct=0, slippage_pct=0)
    assert res.trades == [] and res.metrics["total_return_pct"] == 0


def test_max_drawdown_and_buy_hold() -> None:
    df = frame(opens=[100, 100, 200, 100, 100], closes=[100, 200, 200, 100, 100])
    res = backtest(df, pd.Series([1, 1, 1, 1, 1]), fee_pct=0, slippage_pct=0)
    # вход по открытию свечи 1 (100), пик капитала на 200, потом откат к 100
    assert res.metrics["max_drawdown_pct"] == pytest.approx(-50.0)
    assert res.metrics["buy_hold_return_pct"] == 0


def test_backtest_rejects_bad_input() -> None:
    df = frame([1, 2, 3])
    with pytest.raises(ValueError):
        backtest(df, pd.Series([1, 1]))
    with pytest.raises(ValueError):
        backtest(df, pd.Series([1, 1, 1]), capital=0)


# --- стратегии ---


def test_sma_cross_target_follows_trend() -> None:
    closes = list(range(100, 60, -1)) + list(range(60, 140))  # падение, потом рост
    df = frame(closes)
    t = STRATEGIES["sma_cross"].target(df, {"fast": 5, "slow": 20})
    assert t.iloc[:19].eq(0).all()  # прогрев
    assert t.iloc[45] == 0 and t.iloc[-1] == 1


def test_donchian_breakout_and_exit() -> None:
    closes = [10.0] * 25 + [12.0] * 5 + [7.0] * 5
    df = frame(closes)
    t = STRATEGIES["donchian"].target(df, {"entry": 20, "exit": 10})
    assert t.iloc[24] == 0 and t.iloc[25] == 1  # первое закрытие выше максимума 20 свечей
    assert t.iloc[-1] == 0  # выход после пробоя вниз


def test_rsi_strategy_has_hysteresis() -> None:
    closes = [100.0] * 5 + list(np.linspace(100, 60, 20)) + list(np.linspace(60, 100, 20))
    t = STRATEGIES["rsi_reversion"].target(frame(closes), {"period": 5, "buy_below": 30, "exit_above": 50})
    assert t.max() == 1 and t.iloc[-1] == 0 and t.iloc[0] == 0


@pytest.mark.parametrize(
    "sid, params, msg",
    [
        ("sma_cross", {"fast": 50, "slow": 20}, "короче"),
        ("sma_cross", {"fast": 1}, "от 2 до 200"),
        ("sma_cross", {"fast": "20"}, "целым"),
        ("sma_cross", {"bogus": 1}, "Неизвестные"),
        ("rsi_reversion", {"buy_below": 45, "exit_above": 40}, "ниже порога выхода"),
    ],
)
def test_param_validation(sid: str, params: dict, msg: str) -> None:
    with pytest.raises(ValueError, match=msg):
        STRATEGIES[sid].resolve(params)


def test_defaults_are_valid_for_every_strategy() -> None:
    for s in STRATEGIES.values():
        assert s.resolve() == {p.name: p.default for p in s.params}


# --- риск ---


def test_position_size_by_risk() -> None:
    # риск 1% от 100 000 = 1000 ₽; стоп на 10 ₽ ниже входа → 100 штук
    p = position_size(100_000, 1, entry=200, stop=190)
    assert (p.qty, p.risk_amount, p.cost, p.capped) == (100, 1000, 20_000, False)


def test_position_size_rounds_down_to_lot() -> None:
    p = position_size(100_000, 1, entry=200, stop=190, lot=10)
    assert p.qty == 100 and p.lots == 10
    p = position_size(100_000, 1, entry=200, stop=191, lot=10)  # 111.1 → 110 (11 лотов)
    assert p.qty == 110 and p.lots == 11


def test_position_size_capped_by_capital() -> None:
    p = position_size(10_000, 50, entry=100, stop=99)  # по риску 5000 шт, денег на 100
    assert p.qty == 100 and p.capped and p.cost == 10_000


def test_position_size_crypto_step() -> None:
    p = position_size(1000, 1, entry=60_000, stop=59_000, qty_step=0.0001)
    assert p.qty == pytest.approx(0.01) and p.risk_amount == pytest.approx(10)


@pytest.mark.parametrize("kw", [{"stop": 200}, {"stop": 250}, {"stop": 0}, {"entry": 0}])
def test_position_size_rejects_bad_input(kw: dict) -> None:
    args = {"capital": 100_000, "risk_pct": 1, "entry": 200, "stop": 190} | kw
    with pytest.raises(ValueError):
        position_size(**args)


def test_position_size_includes_fees_and_slippage() -> None:
    # вручную: вход 100·1.001 = 100.1 (+0.1% комиссии = 100.2001), выход по стопу 90·0.999 = 89.91 (−0.1% = 89.82009)
    p = position_size(10_000, 1, entry=100, stop=90, fee_pct=0.1, slippage_pct=0.1)
    per_unit = 100.2001 - 89.82009
    assert p.qty == 9  # 100 / 10.38 = 9.6 → 9
    assert p.risk_amount == pytest.approx(9 * per_unit, abs=0.01)
    assert p.cost == pytest.approx(9 * 100.2001, abs=0.01)
    assert p.risk_amount_worse > p.risk_amount
    assert p.budget == 100 and p.warning is None
    free = position_size(10_000, 1, entry=100, stop=90)
    assert free.qty == 10 and free.risk_amount_worse == free.risk_amount


def test_position_size_zero_qty_explains_why() -> None:
    lot = position_size(100_000, 0.01, entry=274, stop=260, lot=10)  # лот рискует 140 при бюджете 10
    assert lot.qty == 0 and lot.warning and "лот рискует" in lot.warning
    poor = position_size(100_000, 50, entry=274, stop=270, lot=10, available=1_000)  # лот стоит 2740
    assert poor.qty == 0 and poor.warning and "не хватает" in poor.warning


def test_position_size_limited_by_free_funds_not_capital() -> None:
    p = position_size(100_000, 1, entry=100, stop=99, available=2_000)  # по риску 1000 шт., денег на 20
    assert p.qty == 20 and p.capped and p.warning and "свободными средствами" in p.warning
    assert p.risk_amount == pytest.approx(20)  # риск меньше заданного — и это сказано


@pytest.mark.parametrize("kw", [{"fee_pct": -1}, {"slippage_pct": 100}, {"available": -5}, {"entry": float("nan")}])
def test_position_size_rejects_bad_costs(kw: dict) -> None:
    args = {"capital": 1000, "risk_pct": 1, "entry": 100, "stop": 90, **kw}
    with pytest.raises(ValueError):
        position_size(**args)


def test_position_size_bond_price_is_percent_of_face_and_accrued_is_paid() -> None:
    # ОФЗ: цена 51.152% от номинала 1000 = 511.52 руб. за бумагу; стоп 49% = 490 руб.
    p = position_size(100_000, 1, entry=51.152, stop=49.0, unit_value=10.0, accrued=23.15)
    assert p.qty == 46  # 1000 / (2.152 * 10) = 46.47
    assert p.risk_amount == pytest.approx(46 * 21.52, abs=0.01)  # НКД в потерю по стопу не входит
    assert p.cost == pytest.approx(46 * (511.52 + 23.15), abs=0.01)


def test_position_size_respects_exchange_minimums() -> None:
    tiny = position_size(1_000, 0.1, entry=60_000, stop=59_000, qty_step=1e-8, min_qty=1e-3)  # ровно минимум — допустимо
    assert tiny.qty == pytest.approx(0.001) and tiny.warning is None
    below = position_size(1_000, 0.05, entry=60_000, stop=59_000, qty_step=1e-8, min_qty=1e-3)  # 0.0005 BTC
    assert below.qty == 0 and below.warning and "минимальной заявки" in below.warning
    cost = position_size(100_000, 0.001, entry=100, stop=99, min_cost=500)  # 1 шт. = 100 < 500
    assert cost.qty == 0 and cost.warning and "минимальной заявки" in cost.warning
