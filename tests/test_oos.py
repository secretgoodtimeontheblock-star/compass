"""Проверка вне выборки: подбор видит только первую часть истории, проверка — только вторую."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from compass import oos
from compass.api.app import create_app
from compass.backtest import backtest
from compass.db import connect
from compass.experiments import (
    MULTIPLE_TESTING_WARN_AT,
    Experiment,
    ExperimentLog,
    multiple_testing_warning,
)
from compass.strategies import STRATEGIES, candles_to_df
from compass.validation import ENGINE_VERSION
from tests.conftest import DAY, Env, day_candles

GRID = {"fast": [2, 3, 4, 6], "slow": [10, 15, 20]}
SMA = STRATEGIES["sma_cross"]


def wavy(n: int = 600, seed: int = 1, tail_shift: float = 0.0) -> list[float]:
    """Тренд + волны + шум. tail_shift — сдвиг цен второй половины (для проверки отсутствия утечки)."""
    rng = np.random.default_rng(seed)
    out = [100 + 8 * math.sin(i / 4) + i * 0.05 + float(rng.normal(0, 1.2)) for i in range(n)]
    return [v + (tail_shift if i >= int(n * 0.7) else 0.0) for i, v in enumerate(out)]


def frame(closes: list[float]) -> pd.DataFrame:
    return candles_to_df(day_candles(closes))


def run(df: pd.DataFrame, grid=GRID, pct: float = 70, **kw) -> dict:
    return oos.run_oos(SMA, {"fast": 3, "slow": 12}, grid, df, pct, 100_000.0, 0.05, 0.05, None, **kw)


# --- разделение ---


def test_split_is_contiguous_and_disjoint() -> None:
    df = frame(wavy())
    r = run(df)
    s = r["split"]
    assert s["train_candles"] + s["test_candles"] == len(df) == 600
    assert s["train_candles"] == 420 and s["train_from"] == 0 and s["test_to"] == 599 * DAY
    assert s["test_from"] == s["train_to"] + DAY  # ни пропуска, ни пересечения


@pytest.mark.parametrize("pct", [49, 91])
def test_split_rejects_bad_share(pct) -> None:
    with pytest.raises(ValueError):
        oos.split_index(600, pct)


def test_split_rejects_too_little_history() -> None:
    with pytest.raises(ValueError, match="Мало истории"):
        oos.split_index(120, 70)


# --- главное свойство: подбор не видит проверочный период ---


def test_choice_and_train_metrics_do_not_depend_on_test_data() -> None:
    a = run(frame(wavy(tail_shift=0.0)))
    b = run(frame(wavy(tail_shift=-40.0)))  # вторая часть истории совсем другая
    assert a["chosen"]["params"] == b["chosen"]["params"]
    assert a["chosen"]["train"] == b["chosen"]["train"]
    assert a["optimization"]["top"] == b["optimization"]["top"]
    assert a["chosen"]["test"] != b["chosen"]["test"]  # а проверка на изменение отреагировала


def test_chosen_variant_is_the_train_argmax_and_test_matches_a_direct_run() -> None:
    df = frame(wavy())
    r = run(df)
    k = r["split"]["train_candles"]
    best, best_score = None, None
    for fast in GRID["fast"]:
        for slow in GRID["slow"]:
            if fast >= slow:
                continue
            p = {"fast": fast, "slow": slow}
            tr = backtest(df.iloc[:k].reset_index(drop=True), SMA.target(df, p).iloc[:k].reset_index(drop=True))
            sc = oos.score(tr.metrics)
            if sc is not None and (best_score is None or sc > best_score):
                best, best_score = p, sc
    assert best is not None and r["chosen"]["params"] == best and r["chosen"]["train_score"] == best_score
    direct = backtest(df, SMA.target(df, best), start=k)
    assert r["chosen"]["test"]["total_return_pct"] == direct.metrics["total_return_pct"]
    assert r["chosen"]["test"]["trades"] == direct.metrics["trades"]


def test_start_param_trades_nothing_before_start_and_matches_slice_without_atr() -> None:
    df = frame(wavy(300))
    tgt = SMA.target(df, {"fast": 5, "slow": 30})
    k = 150
    part = backtest(df, tgt, start=k)
    assert all(t.entry_ts >= int(df["ts"].iloc[k]) for t in part.trades)
    assert part.equity[0][0] == int(df["ts"].iloc[k]) and len(part.equity) == 300 - k
    sliced = backtest(df.iloc[k:].reset_index(drop=True), tgt.iloc[k:].reset_index(drop=True))
    assert part.equity == sliced.equity and part.trades == sliced.trades  # без ATR тождественно
    with pytest.raises(ValueError):
        backtest(df, tgt, start=299)


# --- выбор, чувствительность, расходы, вердикт ---


def test_no_grid_means_no_optimization_and_reports_it() -> None:
    r = run(frame(wavy()), grid={})
    assert r["optimization"]["enabled"] is False and r["optimization"]["variants"] == 1
    assert r["chosen"]["params"] == {"fast": 3, "slow": 12} and r["baseline"] is None and r["sensitivity"] == []


def test_grid_reports_baseline_neighbors_and_cost_sensitivity() -> None:
    r = run(frame(wavy()))
    assert r["baseline"]["params"] == {"fast": 3, "slow": 12}
    assert r["optimization"]["variants"] + r["optimization"]["dropped_invalid"] == 12
    assert r["sensitivity"] and {x["param"] for x in r["sensitivity"]} <= {"fast", "slow"}
    costs = {c["multiplier"]: c for c in r["cost_sensitivity"]}
    assert set(costs) == {1, 2, 3} and costs[3]["fee_pct"] == pytest.approx(0.15)
    assert costs[3]["trades"] > 0
    assert costs[3]["return_pct"] < costs[2]["return_pct"] < costs[1]["return_pct"]  # дороже исполнение — хуже результат
    assert costs[1]["return_pct"] == r["chosen"]["test"]["total_return_pct"]


def test_invalid_combinations_are_dropped_and_counted() -> None:
    r = run(frame(wavy()), grid={"fast": [10, 40], "slow": [20, 30]})  # fast=40 не короче slow — недопустимо
    assert r["optimization"]["dropped_invalid"] == 2 and r["optimization"]["variants"] == 2


@pytest.mark.parametrize(
    "grid",
    [{"nope": [1]}, {"fast": []}, {"fast": list(range(2, 20))}, {"fast": [2] * 3, "slow": list(range(20, 32))} | {"x": [1]}],
)
def test_bad_grid_is_rejected(grid) -> None:
    with pytest.raises(ValueError):
        run(frame(wavy()), grid=grid)


def test_too_many_combinations_are_rejected() -> None:
    rsi = STRATEGIES["rsi_reversion"]
    grid = {"period": list(range(5, 17)), "buy_below": list(range(10, 22)), "exit_above": list(range(50, 62))}  # 1728
    with pytest.raises(ValueError, match="Слишком много"):
        oos.expand_grid(rsi, {}, grid)


def test_verdict_is_inconclusive_when_test_has_too_few_trades() -> None:
    flat = frame([100.0] * 600)  # сигналов нет совсем
    r = run(flat, grid={})
    assert r["verdict"]["status"] == "inconclusive"
    assert any("сделок" in x for x in r["verdict"]["reasons"])


def test_no_eligible_variant_means_no_choice() -> None:
    r = run(frame([100.0] * 600))
    assert r["chosen"] is None and r["verdict"]["status"] == "inconclusive"


def _m(**kw) -> dict:
    return {"total_return_pct": 5.0, "buy_hold_return_pct": 1.0, "max_drawdown_pct": -4.0, "trades": 12,
            "profit_factor": 1.5, "avg_trade_pct": 0.8, **kw}


def test_verdict_rules() -> None:
    train = _m()
    assert oos._verdict(train, _m(), 1.2)["status"] == "held"
    assert oos._verdict(train, _m(total_return_pct=-3.0), 1.2)["status"] == "degraded"
    assert oos._verdict(train, _m(profit_factor=0.8), 1.2)["status"] == "degraded"
    assert oos._verdict(train, _m(avg_trade_pct=-0.1), 1.2)["status"] == "degraded"
    v = oos._verdict(train, _m(trades=3, total_return_pct=50.0), 1.2)
    assert v["status"] == "inconclusive" and any("нельзя" in x for x in v["reasons"])  # красивая цифра на 3 сделках — не вывод


def test_edge_that_exists_only_in_train_does_not_hold_on_test() -> None:
    up = [100 + i * 0.3 + 8 * math.sin(i / 4) for i in range(420)]
    down = [up[-1] - j * 0.35 + 5 * math.sin(j / 4) for j in range(180)]
    r = run(frame(up + down))
    assert r["chosen"]["train"]["total_return_pct"] > r["chosen"]["test"]["total_return_pct"]
    assert r["chosen"]["test"]["total_return_pct"] < r["chosen"]["test"]["buy_hold_return_pct"] or r["verdict"]["status"] != "held"


# --- история проверок ---


def test_experiment_log_counts_variants_and_is_append_only() -> None:
    conn = connect(":memory:")
    log = ExperimentLog(conn)
    e = Experiment("backtest", "moex", "SBER", "1d", "donchian", "donchian/r1/x", {"entry": 20}, {}, "h", "3", 100, 1, {})
    log.add(e)
    log.add(replace(e, kind="validate", n_variants=12))
    log.add(replace(e, symbol="GAZP"))
    assert log.variants_tried("moex", "SBER", "1d", "donchian") == 13
    assert log.variants_tried("moex", "SBER", "1d", "sma_cross") == 0
    assert [x["symbol"] for x in log.list()] == ["GAZP", "SBER", "SBER"]
    with pytest.raises(sqlite3.DatabaseError, match="неизменяема"):
        conn.execute("UPDATE experiments SET n_variants = 1")
    with pytest.raises(sqlite3.DatabaseError, match="нельзя удалить"):
        conn.execute("DELETE FROM experiments")
    assert multiple_testing_warning(MULTIPLE_TESTING_WARN_AT - 1) is None
    assert "случайно" in multiple_testing_warning(MULTIPLE_TESTING_WARN_AT)


# --- API ---


def client(env: Env, closes: list[float]) -> TestClient:
    env.adapter.data["SBER"] = day_candles(closes)
    env.now[0] = 10_000 * DAY
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


BODY = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 3, "slow": 12}, "limit": 600}


def test_validate_api_end_to_end_and_history_accumulates(env: Env) -> None:
    c = client(env, wavy())
    r = c.post("/api/validate", json={**BODY, "grid": GRID, "train_pct": 70})
    assert r.status_code == 200
    body = r.json()
    assert body["split"]["train_candles"] == 420 and body["chosen"]["params"]
    assert body["strategy_version"].startswith("sma_cross/r1/") and body["run_card"]["engine_version"] == ENGINE_VERSION
    assert body["trials"]["prior_variants"] == 0 and body["trials"]["this_run_variants"] == body["optimization"]["variants"]
    n = body["optimization"]["variants"]
    again = c.post("/api/validate", json={**BODY, "grid": GRID}).json()
    assert again["trials"]["prior_variants"] == n  # прошлый перебор виден и учтён
    plain = c.post("/api/backtest", json=BODY).json()
    assert plain["trials"]["prior_variants"] == 2 * n and plain["trials"]["total_variants"] == 2 * n + 1
    hist = c.get("/api/experiments", params={"symbol": "SBER"}).json()
    assert [h["kind"] for h in hist] == ["backtest", "validate", "validate"]
    assert hist[1]["config"]["grid"] == GRID and hist[1]["result"]["chosen"]


def test_validate_api_warns_after_many_variants_and_validates_input(env: Env) -> None:
    c = client(env, wavy())
    big = {"fast": [2, 3, 4, 5, 6, 7, 8, 9], "slow": [20, 25, 30, 35, 40, 45, 50, 55]}
    first = c.post("/api/validate", json={**BODY, "grid": big}).json()
    assert first["trials"]["total_variants"] >= MULTIPLE_TESTING_WARN_AT and first["trials"]["warning"]
    assert c.post("/api/validate", json={**BODY, "train_pct": 95}).status_code == 422
    assert c.post("/api/validate", json={**BODY, "grid": {"zzz": [1]}}).status_code == 422
    assert c.post("/api/validate", json={**BODY, "strategy": "nope"}).status_code == 404
    short = client(env, wavy(120))
    assert short.post("/api/validate", json={**BODY, "limit": 120}).status_code == 422
