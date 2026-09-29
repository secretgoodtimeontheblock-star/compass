from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compass.backtest import Trade, backtest
from compass.validation import (
    data_fingerprint,
    risk_ratios,
    run_card,
    trade_resampling,
    trade_stats,
    walk_forward,
)

DAY = 86_400_000


def _df(closes: list[float]) -> pd.DataFrame:
    c = np.array(closes, dtype=float)
    return pd.DataFrame(
        {"ts": np.arange(len(c)) * DAY, "open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1.0}
    )


def _trades(pnls: list[float]) -> list[Trade]:
    return [Trade(i * DAY, 1.0, i * DAY + 1, 1.0, p) for i, p in enumerate(pnls)]


def test_ratios_none_on_short_history() -> None:
    ts = np.arange(30) * DAY
    r = risk_ratios(np.linspace(100, 110, 30), ts, 100, -1.0)
    assert r["sharpe"] is None and r["calmar"] is None


def test_ratios_on_steady_growth_with_noise() -> None:
    rng = np.random.default_rng(1)
    eq = 100 * np.cumprod(1 + rng.normal(0.004, 0.01, 365))
    r = risk_ratios(eq, np.arange(365) * DAY, 100, -8.0)
    assert r["sharpe"] and r["sharpe"] > 0 and r["sortino"] > r["sharpe"] * 0.5
    assert r["cagr_pct"] and r["calmar"]


def test_trade_stats_streak_and_payoff() -> None:
    s = trade_stats(_trades([0.1, -0.05, -0.05, -0.02, 0.2]))
    assert s["max_consecutive_losses"] == 3
    assert s["payoff_ratio"] == pytest.approx(0.15 / 0.04, abs=0.01)
    assert s["worst_trade_pct"] == -5.0


def test_resampling_needs_enough_trades_and_is_deterministic() -> None:
    assert trade_resampling(_trades([0.1, 0.1])) is None
    a = trade_resampling(_trades([0.1, -0.05, 0.08, -0.03, 0.06, 0.02]))
    assert a == trade_resampling(_trades([0.1, -0.05, 0.08, -0.03, 0.06, 0.02]))
    assert a["return_p5_pct"] <= a["return_p50_pct"] <= a["return_p95_pct"]
    assert a["drawdown_shuffled_p95_pct"] <= 0


def test_all_winners_never_lose() -> None:
    r = trade_resampling(_trades([0.05] * 6))
    assert r["profitable_share_pct"] == 100 and r["drawdown_bootstrap_p95_pct"] == 0


def test_walk_forward_windows_cover_history() -> None:
    closes = [100 + i * 0.3 + (i % 9) for i in range(200)]
    df = _df(closes)
    bt = backtest(df, pd.Series([1] * 200), fee_pct=0, slippage_pct=0)
    wf = walk_forward(df, bt, 100_000, 4)
    assert wf and len(wf["windows"]) == 4 and wf["windows"][0]["start"] == 0
    assert wf["windows"][-1]["end"] == 199 * DAY
    assert walk_forward(_df(closes[:50]), backtest(_df(closes[:50]), pd.Series([1] * 50)), 1e5, 4) is None


def test_run_card_hash_changes_with_data() -> None:
    df = _df([1.0 + i for i in range(40)])
    kw = {"market": "moex", "symbol": "SBER", "tf": "1d", "strategy": "donchian", "params": {}, "capital": 1e5,
              "fee_pct": 0.05, "slippage_pct": 0.05, "stale": False}
    card = run_card(df, **kw)
    assert card["candles"] == 40 and card["data_hash"] == data_fingerprint(df)
    df2 = df.copy()
    df2.loc[10, "close"] += 1
    assert data_fingerprint(df2) != card["data_hash"]


def test_sample_rules_hide_small_sample_stats_but_keep_facts() -> None:
    from compass.validation import MIN_TRADES_FOR_STATS, apply_sample_rules

    m = {"trades": 3, "win_rate_pct": 100.0, "sharpe": 2.5, "profit_factor": None, "total_return_pct": 8.0,
         "max_drawdown_pct": -4.0}
    out, warn = apply_sample_rules(m)
    assert out["win_rate_pct"] is None and out["sharpe"] is None
    assert out["total_return_pct"] == 8.0 and out["max_drawdown_pct"] == -4.0
    assert warn and "3" in warn[0]
    enough = {**m, "trades": MIN_TRADES_FOR_STATS}
    assert apply_sample_rules(enough) == (enough, [])
