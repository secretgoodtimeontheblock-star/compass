"""Стопы, цели, гэпы и размер по риску в бэктесте. Каждая сцена посчитана вручную.

Основа: 20 ровных свечей (open=close=100, high=101, low=99) → ATR(14)=2. Решение на закрытии свечи 19,
вход по открытию свечи 20. Стоп 2·ATR → 100 − 4 = 96. Комиссии и проскальзывание нулевые, если не сказано."""

from __future__ import annotations

import pandas as pd
import pytest

from compass.backtest import Rules, backtest
from compass.risk import position_size

FLAT = 20  # свечи 0..19
Bar = tuple[float, float, float, float]  # open, high, low, close


def scene(bars: list[Bar], target: list[int] | None = None) -> tuple[pd.DataFrame, pd.Series]:
    rows = [(100.0, 101.0, 99.0, 100.0)] * FLAT + bars
    df = pd.DataFrame(
        {
            "ts": [i * 1000 for i in range(len(rows))],
            "open": [r[0] for r in rows], "high": [r[1] for r in rows],
            "low": [r[2] for r in rows], "close": [r[3] for r in rows], "volume": 1.0,
        }
    )
    tgt = target if target is not None else [0] * (FLAT - 1) + [1] * (len(bars) + 1)
    return df, pd.Series(tgt)


def run(bars, target=None, capital=100_000.0, fee=0.0, slip=0.0, **rules):
    df, tgt = scene(bars, target)
    return backtest(df, tgt, capital, fee, slip, Rules(stop_atr_mult=2.0, **rules))


def only(res):
    (t,) = res.trades
    return t


def test_stop_hit_inside_candle_exits_at_stop_price() -> None:
    res = run([(100, 101, 95, 97), (97, 98, 96, 97)])
    t = only(res)
    assert (t.entry_price, t.stop, t.exit_price, t.exit_reason) == (100, 96, 96, "stop")
    assert t.pnl_pct == pytest.approx(-0.04)
    assert res.metrics["stops"] == 1 and res.metrics["gap_exits"] == 0


def test_gap_through_stop_exits_at_open_not_at_stop() -> None:
    res = run([(100, 101, 99, 100), (90, 92, 88, 91)])
    t = only(res)
    assert (t.exit_price, t.exit_reason) == (90, "gap_stop")
    assert t.pnl_pct == pytest.approx(-0.10)  # хуже, чем расчётные −4%: гэп бьёт сильнее стопа
    assert res.metrics["gap_exits"] == 1


def test_target_and_gap_over_target() -> None:
    t = only(run([(100, 109, 99, 108)], target_r=2.0))  # цель 100 + 2·4 = 108
    assert (t.target, t.exit_price, t.exit_reason) == (108, 108, "target")
    assert t.pnl_pct == pytest.approx(0.08)
    g = only(run([(100, 101, 99, 100), (110, 111, 109, 110)], target_r=2.0))
    assert (g.exit_price, g.exit_reason) == (110, "gap_target")  # гэп в нашу пользу — берём открытие


def test_stop_and_target_in_same_candle_takes_the_stop() -> None:
    res = run([(100, 110, 94, 100)], target_r=2.0)  # достигнуты и стоп 96, и цель 108
    t = only(res)
    assert t.exit_reason == "stop" and t.pnl_pct == pytest.approx(-0.04)
    assert res.metrics["ambiguous_bars"] == 1
    # если цели нет — свеча не неоднозначна
    assert run([(100, 110, 94, 100)]).metrics["ambiguous_bars"] == 0


def test_no_reentry_after_stop_until_a_fresh_signal() -> None:
    quiet: Bar = (97, 98, 96.5, 97)
    bars: list[Bar] = [(100, 101, 95, 97), quiet, quiet, quiet, quiet, quiet, quiet, quiet]  # свечи 20..27; стоп на 20-й
    # цель стратегии: 1 на свечах 19–23 (стратегия «держит»), 0 на 24, снова 1 с 25-й
    target = [0] * 19 + [1] * 5 + [0] + [1] * 3
    res = run(bars, target=target)
    assert len(res.trades) == 2  # без блокировки повторный вход случился бы уже на свече 21
    assert res.trades[0].exit_reason == "stop"
    assert res.trades[1].entry_ts == 26 * 1000  # решение на закрытии свечи 25 → открытие 26


def test_entry_below_stop_is_skipped_and_counted() -> None:
    res = run([(95, 96, 94, 95)])  # открытие 95 уже ниже стопа 96
    assert res.trades == [] and res.metrics["skipped_entries"] == 1


def test_stop_uses_only_information_known_at_signal_close() -> None:
    a = only(run([(100, 101, 95, 97), (97, 98, 96, 97)]))
    b = only(run([(100, 500, 95, 400), (97, 98, 96, 97)]))  # другие high/close свечи входа
    assert a.stop == b.stop == 96


def test_risk_sizing_matches_calculator_and_loses_exactly_one_r() -> None:
    res = run([(100, 101, 95, 97), (97, 98, 96, 97)], risk_pct=1.0)
    t = only(res)
    assert t.qty == 250 and t.risk_amount == pytest.approx(1000)  # 1000 / 4 руб. на бумагу
    assert t.pnl_amount == pytest.approx(-1000)
    assert res.metrics["avg_r"] == -1.0
    assert res.equity[-1][1] == pytest.approx(99_000)  # проиграли ровно 1% капитала, не весь


def test_risk_sizing_with_costs_uses_the_same_formula_as_risk_calculator() -> None:
    fee, slip = 0.1, 0.1
    res = run([(100, 101, 95, 97), (97, 98, 96, 97)], fee=fee, slip=slip, risk_pct=1.0)
    t = only(res)
    expected = position_size(100_000, 1.0, 100.0, 96.0, fee_pct=fee, slippage_pct=slip)
    assert t.qty == pytest.approx(expected.qty)
    assert t.entry_price == pytest.approx(100.1)  # открытие + проскальзывание
    assert t.exit_price == pytest.approx(96 * 0.999)  # стоп − проскальзывание
    assert t.risk_amount == pytest.approx(expected.risk_amount, abs=0.02)
    assert t.pnl_amount == pytest.approx(-t.risk_amount, abs=0.02)  # стоп сработал ровно по расчёту


def test_lot_rounding_and_too_small_risk_skips_the_entry() -> None:
    t = only(run([(100, 101, 95, 97), (97, 98, 96, 97)], risk_pct=1.0, lot=100))
    assert t.qty == 200  # 250 → 2 лота по 100
    res = run([(100, 101, 99, 100)], risk_pct=0.001, lot=1)  # бюджет 1 при потере 4 на бумагу
    assert res.trades == [] and res.metrics["skipped_entries"] == 1


def test_mae_mfe_and_bars_held_for_signal_exit() -> None:
    bars: list[Bar] = [(100, 104, 98, 101), (101, 103, 97, 99), (99, 99, 99, 99)]
    target = [0] * (FLAT - 1) + [1, 1, 0, 0]  # решение выйти на закрытии свечи 21, выход по открытию 22
    res = backtest(*scene(bars, target), 100_000.0, 0.0, 0.0, Rules(stop_atr_mult=10.0))
    t = only(res)
    assert t.exit_reason == "signal" and t.exit_price == 99
    assert t.mae_pct == pytest.approx(-3.0) and t.mfe_pct == pytest.approx(4.0)  # свеча выхода в диапазон не входит
    assert t.bars == 2 and res.metrics["avg_mae_pct"] == -3.0


def test_open_position_at_end_is_marked_open_with_stop() -> None:
    res = run([(100, 101, 99, 100), (100, 102, 99, 101)])
    t = only(res)
    assert t.exit_ts is None and t.exit_reason == "open" and t.stop == 96
    assert res.metrics["open_trade"] == 1 and res.metrics["trades"] == 0


def test_bond_price_in_percent_of_face_scales_money() -> None:
    res = run([(100, 101, 95, 97), (97, 98, 96, 97)], risk_pct=1.0, unit_value=10.0)
    t = only(res)
    assert t.qty == 25  # 1000 руб. / (4 пункта · 10 руб.)
    assert res.equity[-1][1] == pytest.approx(99_000)


@pytest.mark.parametrize(
    "rules",
    [
        Rules(risk_pct=1.0), Rules(target_r=2.0), Rules(stop_atr_mult=0), Rules(stop_atr_mult=2, target_r=0),
        Rules(stop_atr_mult=2, risk_pct=0), Rules(stop_atr_mult=2, risk_pct=101), Rules(stop_atr_mult=99),
        Rules(stop_atr_mult=2, lot=0), Rules(stop_atr_mult=2, unit_value=0),
    ],
)
def test_bad_rules_are_rejected(rules: Rules) -> None:
    df, tgt = scene([(100, 101, 99, 100)] * 3)
    with pytest.raises(ValueError):
        backtest(df, tgt, rules=rules)


def test_default_mode_is_unchanged_by_rules_none() -> None:
    df, tgt = scene([(100, 105, 99, 104), (104, 110, 103, 109), (109, 109, 100, 101)], [0] * 19 + [1, 1, 0, 0])
    a, b = backtest(df, tgt), backtest(df, tgt, rules=Rules())
    assert a.equity == b.equity and a.trades == b.trades
    (t,) = a.trades
    assert t.exit_reason == "signal" and t.stop is None and t.risk_amount is None
