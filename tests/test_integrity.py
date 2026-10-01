"""Проверки целостности бэктеста: правило без заглядывания проходит, подглядывающее — ловится."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from compass import integrity
from compass.api.app import create_app
from compass.backtest import Rules, backtest
from compass.strategies import STRATEGIES, Param, Strategy
from tests.conftest import DAY, Env, day_candles


def walk(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    opens = np.concatenate([[100.0], closes[:-1]]) * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"ts": np.arange(n) * DAY, "open": opens, "high": high, "low": low, "close": closes, "volume": 1e6})


def peeking_target(df: pd.DataFrame, p: dict[str, int]) -> pd.Series:
    """Покупаем, если ЗАВТРА цена выше: классическое заглядывание вперёд."""
    return (df["close"].shift(-1) > df["close"]).astype(int)


PEEKER = Strategy("peek", "Подглядывающее", "тест", (Param("x", "x", 1, 1, 5),), peeking_target)


# --- каждое встроенное правило не должно смотреть в будущее (автоматический тест на утечку) ---


@pytest.mark.parametrize("sid", sorted(STRATEGIES))
def test_every_builtin_strategy_is_causal_on_random_data(sid: str) -> None:
    strat = STRATEGIES[sid]
    for seed in (1, 2, 3):
        res = integrity.check_causality(strat, strat.resolve({}), walk(seed=seed))
        assert res["status"] == "pass", (sid, seed, res["detail"])


def test_peeking_rule_is_caught_with_a_plain_explanation() -> None:
    res = integrity.check_causality(PEEKER, PEEKER.resolve({}), walk())
    assert res["status"] == "fail" and "подглядывает" not in res["detail"] and "будущие свечи" in res["detail"]


def test_causality_skips_when_history_is_too_short() -> None:
    res = integrity.check_causality(STRATEGIES["sma_cross"], {"fast": 5, "slow": 20}, walk(25))
    assert res["status"] == "skipped"


# --- проверки исполнения ---


def run(df: pd.DataFrame, strat_id: str = "sma_cross", params=None, slip=0.05, fee=0.05, rules=None, capital=100_000.0):
    strat = STRATEGIES[strat_id]
    p = strat.resolve(params if params is not None else ({"fast": 5, "slow": 20} if strat_id == "sma_cross" else {}))
    target = strat.target(df, p)
    bt = backtest(df, target, capital, fee, slip, rules)
    return strat, p, target, bt


def test_honest_backtest_passes_every_check() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    assert bt.trades, "в сцене должны быть сделки"
    rep = integrity.integrity_report(strat, p, df, target, bt, 100_000.0, 0.05, None, 0.05)
    assert rep["passed"], rep
    assert {c["id"]: c["status"] for c in rep["checks"]}["execution_timing"] == "pass"
    assert "пройдены" in rep["summary"]


def test_stop_and_target_runs_pass_too_including_open_trade_at_the_end() -> None:
    df = walk(500, seed=11)
    rules = Rules(stop_atr_mult=2.0, target_r=2.0, risk_pct=1.0)
    strat, p, target, bt = run(df, rules=rules)
    rep = integrity.integrity_report(strat, p, df, target, bt, 100_000.0, 0.05, rules, 0.05)
    assert rep["passed"], rep["summary"]
    # открытая сделка в конце ряда тоже сходится по капиталу
    df2 = df.iloc[:300].reset_index(drop=True)
    t2 = pd.Series([0] * 250 + [1] * 50)
    bt2 = backtest(df2, t2, 50_000.0, 0.1, 0.05)
    assert bt2.trades[-1].exit_ts is None
    assert integrity.check_equity(bt2, df2, 50_000.0, None, 0.1)["status"] == "pass"


def test_execution_timing_catches_same_bar_fill() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    t0 = bt.trades[0]
    # подделка: вход по цене закрытия сигнальной свечи — то есть «исполнение в ту же секунду»
    i = int(np.flatnonzero(df["ts"].to_numpy() == t0.entry_ts)[0])
    fake = replace(t0, entry_price=float(df["close"].iloc[i - 1]))
    bad = replace(bt, trades=[fake, *bt.trades[1:]])
    res = integrity.check_execution_timing(df, target, bad, 0.05)
    assert res["status"] == "fail" and "не равна открытию" in res["detail"]


def test_execution_timing_catches_entry_without_signal() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    zero = pd.Series(np.zeros(len(df), dtype=int))
    res = integrity.check_execution_timing(df, zero, bt, 0.05)
    assert res["status"] == "fail" and "без сигнала" in res["detail"]


def test_overlap_and_zero_quantity_are_detected() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    assert len(bt.trades) >= 2
    a, b = bt.trades[0], bt.trades[1]
    overlapping = replace(bt, trades=[a, replace(b, entry_ts=a.entry_ts)])
    assert integrity.check_no_overlap(overlapping)["status"] == "fail"
    assert integrity.check_no_overlap(replace(bt, trades=[replace(a, qty=0.0)]))["status"] == "fail"
    assert integrity.check_no_overlap(bt)["status"] == "pass"


def test_equity_mismatch_is_detected() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    cheated = replace(bt, equity=[*bt.equity[:-1], (bt.equity[-1][0], bt.equity[-1][1] * 1.05)])
    res = integrity.check_equity(cheated, df, 100_000.0, None, 0.05)
    assert res["status"] == "fail" and "не сходится" in res["detail"]


def test_data_order_catches_duplicates_nan_and_inconsistent_prices() -> None:
    df = walk(50)
    assert integrity.check_data_order(df)["status"] == "pass"
    dup = df.copy()
    dup.loc[10, "ts"] = dup.loc[9, "ts"]
    assert integrity.check_data_order(dup)["status"] == "fail"
    nan = df.copy()
    nan.loc[5, "close"] = math.nan
    assert integrity.check_data_order(nan)["status"] == "fail"
    bad = df.copy()
    bad.loc[7, "high"] = bad.loc[7, "low"] - 1
    assert integrity.check_data_order(bad)["status"] == "fail"


def test_ambiguity_is_informational() -> None:
    df = walk()
    strat, p, target, bt = run(df)
    amb = replace(bt, metrics={**bt.metrics, "ambiguous_bars": 3})
    assert integrity.check_ambiguity(amb)["status"] == "info"
    assert integrity.check_ambiguity(bt)["status"] in ("pass", "info")


# --- API ---


def test_backtest_api_returns_integrity_report(env: Env) -> None:
    closes = [100 + 15 * math.sin(i / 7) + i * 0.03 for i in range(300)]
    env.adapter.data["SBER"] = day_candles(closes)
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    body = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 300}
    r = c.post("/api/backtest", json=body).json()
    assert r["integrity"]["passed"] is True
    ids = [x["id"] for x in r["integrity"]["checks"]]
    assert ids == ["causality", "data_order", "execution_timing", "no_overlap", "equity_consistency", "ambiguity"]
    assert not any("целостности" in w for w in r["warnings"])


def test_backtest_api_puts_failed_integrity_first_among_warnings(env: Env, monkeypatch) -> None:
    from compass.api import app as app_mod

    monkeypatch.setattr(app_mod, "integrity_report", lambda *a, **k: {"passed": False, "checks": [], "summary": "Проверка целостности НЕ пройдена: тест."})
    closes = [100 + 15 * math.sin(i / 7) for i in range(200)]
    env.adapter.data["SBER"] = day_candles(closes)
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    r = c.post("/api/backtest", json={"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 200}).json()
    assert r["warnings"][0].startswith("Проверка целостности НЕ пройдена")
