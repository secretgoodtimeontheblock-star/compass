"""Validation Lab: окна walk-forward без утечки, карта устойчивости, PBO/DSR, bootstrap, просадки, режимы, вердикт."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from compass import lab
from compass.api.app import create_app
from compass.backtest import Trade
from compass.strategies import STRATEGIES
from tests.conftest import DAY, Env, day_candles

SMA = STRATEGIES["sma_cross"]
GRID = {"fast": [2, 3, 5, 8], "slow": [10, 15, 20, 30]}


def series(n: int = 1200, seed: int = 3, drift: float = 0.03, vol: float = 0.8, wave: float = 12.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = np.maximum(100 + np.cumsum(rng.normal(drift, vol, n)) + wave * np.sin(np.arange(n) / 7), 5.0)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {"ts": np.arange(n) * DAY, "open": opens, "high": np.maximum(opens, closes) + 0.5,
         "low": np.minimum(opens, closes) - 0.5, "close": closes, "volume": 1e6}
    )


def trade(pnl: float, i: int = 0, closed: bool = True) -> Trade:
    return Trade(i * 10, 100.0, i * 10 + 5 if closed else None, 100 * (1 + pnl), pnl, "signal", 1.0, None, None, None, pnl * 100)


# --- окна ---


def test_folds_with_selection_lie_in_the_second_half_and_never_overlap_training() -> None:
    folds = lab.make_folds(1000, 4, True, "rolling")
    assert [(f.test_start, f.test_end) for f in folds] == [(500, 625), (625, 750), (750, 875), (875, 1000)]
    assert all(f.train_end == f.test_start for f in folds)  # обучение заканчивается там, где начинается проверка
    assert [(f.train_start, f.train_end) for f in folds] == [(0, 500), (125, 625), (250, 750), (375, 875)]  # окно скользит
    anchored = lab.make_folds(1000, 4, True, "anchored")
    assert [f.train_start for f in anchored] == [0, 0, 0, 0] and [f.train_end for f in anchored] == [500, 625, 750, 875]


def test_folds_without_selection_split_the_whole_history_evenly() -> None:
    folds = lab.make_folds(1001, 4, False, "rolling")
    assert [(f.test_start, f.test_end) for f in folds] == [(0, 250), (250, 500), (500, 750), (750, 1001)]


def test_folds_reject_short_history_and_bad_arguments() -> None:
    with pytest.raises(ValueError, match="Мало истории"):
        lab.make_folds(200, 4, True, "rolling")
    with pytest.raises(ValueError, match="Мало истории"):
        lab.make_folds(100, 4, False, "rolling")
    with pytest.raises(ValueError, match="Число окон"):
        lab.make_folds(1000, 2, True, "rolling")
    with pytest.raises(ValueError, match="Режим"):
        lab.make_folds(1000, 4, True, "diagonal")


# --- walk-forward не подглядывает ---


def _fold(df, grid=GRID, k=4, mode="rolling", index=1):
    from compass.oos import expand_grid

    folds = lab.make_folds(len(df), k, True, mode)
    combos = expand_grid(SMA, {"fast": 5, "slow": 20}, grid)[0]
    f = folds[index]
    return f, lab.run_fold(SMA, {"fast": 5, "slow": 20}, combos, df, f, 100_000.0, 0.05, 0.05, None, {})


def test_selection_is_blind_to_prices_inside_and_after_the_test_window() -> None:
    df = series()
    f, a = _fold(df)
    shifted = df.copy()
    for col in ("open", "high", "low", "close"):
        shifted.loc[f.test_start:, col] = shifted.loc[f.test_start:, col] * 3.0 + 50  # вся проверка и всё после неё другие
    _, b = _fold(shifted)
    assert a["chosen"] == b["chosen"] and a["row"]["train_score"] == b["row"]["train_score"]
    assert a["row"]["test_return_pct"] != b["row"]["test_return_pct"]  # проверка при этом реально считается по новым ценам


def test_test_window_result_ignores_everything_after_the_window() -> None:
    df = series()
    f, a = _fold(df)
    changed = df.copy()
    for col in ("open", "high", "low", "close"):
        changed.loc[f.test_end:, col] = changed.loc[f.test_end:, col] * 0.2
    _, b = _fold(changed)
    assert a["row"]["test_return_pct"] == b["row"]["test_return_pct"] and a["row"]["test_trades"] == b["row"]["test_trades"]


def test_stitched_return_is_the_product_of_fold_returns() -> None:
    df = series()
    wf = lab.walk_forward_lab(SMA, {"fast": 5, "slow": 20}, GRID, df, 4, "anchored", 100_000.0, 0.05, 0.05, None, {})
    s = wf["summary"]
    assert s["usable_folds"] == 4
    prod = math.prod(1 + r["test_return_pct"] / 100 for r in wf["folds"])
    assert s["stitched_return_pct"] == pytest.approx((prod - 1) * 100, abs=0.05)
    assert len(wf["_equity"]) == sum(r["test_candles"] for r in wf["folds"])
    assert 0 < s["param_consistency"] <= 1


def test_fixed_rule_without_grid_is_evaluated_on_independent_windows() -> None:
    df = series()
    wf = lab.walk_forward_lab(SMA, {"fast": 5, "slow": 20}, {}, df, 4, "rolling", 100_000.0, 0.05, 0.05, None, {})
    assert wf["summary"]["optimized"] is False and wf["summary"]["mode"] == "fixed"
    assert all(r["params"] == {"fast": 5, "slow": 20} and r["train_from"] is None for r in wf["folds"])


def test_fold_without_eligible_variant_is_flat_and_flagged_not_filled_with_defaults() -> None:
    line = np.linspace(100, 200, 1000)  # монотонный рост: средние не пересекаются, закрытых сделок нет
    df = pd.DataFrame({"ts": np.arange(1000) * DAY, "open": line, "high": line + 0.1, "low": line - 0.1, "close": line, "volume": 1e6})
    wf = lab.walk_forward_lab(SMA, {"fast": 5, "slow": 20}, GRID, df, 4, "rolling", 100_000.0, 0.05, 0.05, None, {})
    assert all(r["status"] == "no_choice" and r["params"] is None and r["test_trades"] == 0 for r in wf["folds"])
    assert wf["summary"]["usable_folds"] == 0 and wf["summary"]["stitched_return_pct"] is None


def test_cost_replay_at_multiplier_one_reproduces_the_walk_forward_result() -> None:
    df = series()
    wf = lab.walk_forward_lab(SMA, {"fast": 5, "slow": 20}, GRID, df, 4, "rolling", 100_000.0, 0.05, 0.05, None, {"spread_pct": 0.05})
    stress = lab.replay_with_costs(SMA, wf, df, 100_000.0, 0.05, 0.05, None, {"spread_pct": 0.05})
    assert [x["multiplier"] for x in stress] == [1, 2, 3]
    assert stress[0]["return_pct"] == pytest.approx(wf["summary"]["stitched_return_pct"], abs=0.05)
    assert stress[0]["return_pct"] > stress[1]["return_pct"] > stress[2]["return_pct"]
    assert stress[2]["spread_pct"] == pytest.approx(0.15)


# --- карта устойчивости ---


def cells(values: dict[tuple[int, int], float], thin: bool = False) -> tuple[list[list[int]], dict, dict]:
    axes = [sorted({a for a, _ in values}), sorted({b for _, b in values})]
    out = {k: {"valid": True, "return_pct": v, "score": v / 10 if v > 0 else -1.0, "thin": thin} for k, v in values.items()}
    return axes, out, dict(out)


def test_isolated_peak_is_fragile_and_plateau_is_stable() -> None:
    peak = {(a, b): 2.0 for a in (1, 2, 3) for b in (1, 2, 3)} | {(2, 2): 60.0}
    assert lab._stability(*cells(peak))["verdict"] == "fragile"
    plateau = {(a, b): 50.0 + a + b for a in (1, 2, 3) for b in (1, 2, 3)}
    st = lab._stability(*cells(plateau))
    assert st["verdict"] == "stable" and st["positive_share_pct"] == 100.0
    losing = {(a, b): -5.0 + a for a in (1, 2, 3) for b in (1, 2, 3)}
    assert lab._stability(*cells(losing))["verdict"] == "no_edge"
    assert lab._stability(*cells({(1, 1): 10.0, (1, 2): 5.0}))["verdict"] == "inconclusive"


def test_stability_compares_growth_so_long_histories_do_not_flip_the_verdict() -> None:
    # доходности огромны из-за длинной истории, но соседи не намного хуже по скорости роста
    big = {(a, b): 12_000.0 - 3_000 * (abs(a - 2) + abs(b - 2)) for a in (1, 2, 3) for b in (1, 2, 3)}
    assert lab._stability(*cells(big))["verdict"] == "stable"


def test_parameter_map_returns_matrix_and_rejects_three_axes() -> None:
    df = series()
    pm = lab.parameter_map(SMA, {"fast": 5, "slow": 20}, GRID, df, 100_000.0, 0.05, 0.05, None, {})
    assert pm["available"] and pm["params"] == ["fast", "slow"] and len(pm["matrix"]) == 4 and len(pm["matrix"][0]) == 4
    assert pm["_returns"].shape == (pm["variants"], len(df))
    odd = lab.parameter_map(SMA, {"fast": 5, "slow": 20}, {"fast": [5, 30], "slow": [10, 20]}, df, 1e5, 0.05, 0.05, None, {})
    assert [c["valid"] for c in odd["matrix"][1]] == [False, False] and odd["variants"] == 2  # быстрая 30 не короче медленной
    rsi = STRATEGIES["rsi_reversion"]
    with pytest.raises(ValueError, match="не больше 2"):
        lab.parameter_map(rsi, {}, {"period": [5, 10], "buy_below": [20, 30], "exit_above": [50, 60]}, df, 1e5, 0.05, 0.05, None, {})
    assert lab.parameter_map(SMA, {}, {}, df, 1e5, 0.05, 0.05, None, {})["available"] is False


# --- PBO и Deflated Sharpe ---


def test_pbo_is_about_one_half_on_average_for_pure_noise_and_near_zero_when_one_variant_truly_wins() -> None:
    # на шуме «лучший внутри» не лучше случайного «снаружи»: отдельный запуск шумит (0.05…0.85), среднее — около 0.5
    values = [lab.pbo(np.random.default_rng(s).normal(0, 0.01, (20, 400)))["value"] for s in range(40)]
    assert 0.35 <= float(np.mean(values)) <= 0.65
    for seed in range(10):
        real = np.random.default_rng(seed).normal(0, 0.01, (20, 400))
        real[7] += 0.004  # настоящее преимущество во всех частях истории
        p = lab.pbo(real)
        assert p["value"] <= 0.1 and p["splits"] == 70 and p["variants"] == 20


def test_pbo_needs_enough_variants_and_bars() -> None:
    assert lab.pbo(None)["available"] is False
    assert lab.pbo(np.zeros((2, 400)))["available"] is False
    assert lab.pbo(np.zeros((5, 40)))["available"] is False


def test_deflated_sharpe_shrinks_with_the_number_of_trials_and_psr_is_its_single_trial_case() -> None:
    rng = np.random.default_rng(1)
    r = rng.normal(0.0008, 0.01, 500)  # Sharpe по свечам ≈ 0.08
    trials = rng.normal(0.0, 0.05, 50)
    one = lab.deflated_sharpe(r, 1, None)
    many = lab.deflated_sharpe(r, 100, trials)
    assert one["available"] and one["benchmark_sharpe_per_bar"] == 0.0
    assert many["benchmark_sharpe_per_bar"] > 0 and many["value"] < one["value"]
    # PSR для нормального ряда: Φ(SR·√(T−1)/√(1+SR²/2)); сверяем порядок величины
    sr = r.mean() / r.std(ddof=1)
    expected = lab._N.cdf(sr * math.sqrt(499) / math.sqrt(1 + sr**2 / 2))
    assert one["value"] == pytest.approx(expected, abs=0.03)


def test_deflated_sharpe_is_low_for_a_losing_series_and_unavailable_for_flat_or_short() -> None:
    rng = np.random.default_rng(2)
    assert lab.deflated_sharpe(rng.normal(-0.001, 0.01, 400), 1, None)["value"] < 0.2
    assert lab.deflated_sharpe(np.zeros(100), 1, None)["available"] is False
    assert lab.deflated_sharpe(rng.normal(0, 0.01, 10), 1, None)["available"] is False


# --- распределение результата ---


def test_bootstrap_says_no_edge_probability_zero_for_all_wins_and_about_half_for_zero_mean() -> None:
    wins = lab.bootstrap_ci([trade(0.02, i) for i in range(30)])
    assert wins["prob_no_edge_pct"] == 0.0 and wins["mean_p5_pct"] == pytest.approx(2.0)
    zero = lab.bootstrap_ci([trade(0.05 if i % 2 else -0.05, i) for i in range(40)])
    assert 30 <= zero["prob_no_edge_pct"] <= 70 and zero["mean_p5_pct"] < 0 < zero["mean_p95_pct"]
    assert lab.bootstrap_ci([trade(0.01, i) for i in range(4)]) is None
    assert lab.bootstrap_ci([trade(0.01, i, closed=False) for i in range(10)]) is None  # открытые сделки не считаются


def test_concentration_exposes_a_result_carried_by_one_trade() -> None:
    trades = [trade(0.50, 0)] + [trade(-0.01, i) for i in range(1, 12)]
    c = lab.concentration(trades)
    assert c["top3_profit_share_pct"] == 100.0 and c["best_trade_pct"] == 50.0
    assert c["total_return_pct"] > 0 > c["return_without_best_pct"]
    spread = lab.concentration([trade(0.02, i) for i in range(20)])
    assert spread["top3_profit_share_pct"] == pytest.approx(15.0)  # 3 из 20 равных
    assert lab.concentration([trade(0.1, i) for i in range(3)]) is None


def test_drawdown_profile_gives_depth_duration_and_recovery() -> None:
    eq = np.concatenate([np.linspace(1.0, 1.2, 10), [1.1, 1.0, 0.96, 1.0, 1.1, 1.2, 1.3], np.linspace(1.3, 1.5, 6)])
    d = lab.drawdown_profile(np.arange(len(eq)) * DAY, eq)
    assert d["max_drawdown_pct"] == pytest.approx(-20.0)
    assert d["peak_to_trough_bars"] == 3 and d["recovered"] is True and d["recovery_bars"] == 3
    assert d["peak_to_trough_days"] == 3.0 and d["recovery_days"] == 3.0 and d["current_drawdown_pct"] == 0.0
    assert d["longest_underwater_bars"] == 5  # 1.1, 1.0, 0.96, 1.0, 1.1 под пиком 1.2
    stuck = np.concatenate([np.linspace(1.0, 1.2, 10), np.linspace(1.2, 0.9, 15)])
    s = lab.drawdown_profile(np.arange(len(stuck)) * DAY, stuck)
    assert s["recovered"] is False and s["recovery_bars"] is None and s["current_drawdown_pct"] == pytest.approx(-25.0)
    assert lab.drawdown_profile(np.arange(5) * DAY, np.ones(5)) is None


def test_tail_risk_reports_expected_shortfall_not_just_the_threshold() -> None:
    r = np.full(100, 0.001)
    r[:10] = np.linspace(-0.05, -0.005, 10)
    eq = np.cumprod(1 + r)
    t = lab.tail_risk(eq, [])
    assert t["worst_bar_pct"] == pytest.approx(-5.0, abs=0.01)
    assert t["cvar95_bar_pct"] <= t["var95_bar_pct"] < 0  # средняя потеря хвоста хуже его порога
    assert "cvar95_trade_pct" not in t
    many = lab.tail_risk(eq, [trade(-0.05 if i < 2 else 0.01, i) for i in range(40)])
    assert many["cvar95_trade_pct"] == pytest.approx(-5.0)
    assert lab.tail_risk(eq[:20], []) is None


def test_regime_split_assigns_profit_to_the_regime_it_came_from() -> None:
    n = 400
    close = np.concatenate([np.linspace(100, 200, 200), np.linspace(200, 100, 200)])  # рост, потом падение
    df = pd.DataFrame({"ts": np.arange(n) * DAY, "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0})
    ts = df["ts"].to_numpy()[120:]
    ret = np.where(close[120:] >= np.concatenate([[close[119]], close[120:-1]]), 0.002, 0.0)  # зарабатываем только на росте
    eq = np.cumprod(1 + ret)
    out = lab.regime_split(df, ts, eq)
    by = {r["id"]: r for r in out["regimes"]}
    assert by["trend_up"]["return_pct"] > 0 and by["trend_down"]["return_pct"] == pytest.approx(0.0, abs=0.5)
    assert out["dominant"] == "trend_up"
    assert by["trend_down"]["buy_hold_pct"] < 0  # на падении «купил и держи» теряет
    assert lab.regime_split(df, ts[:20], eq[:20]) is None


# --- вердикт простым языком ---


def wf_summary(**kw) -> dict:
    base = {"mode": "rolling", "optimized": True, "folds": 4, "usable_folds": 4, "profitable_folds": 4, "stitched_return_pct": 18.0,
            "trades": 40}
    folds = [{"status": "ok"}] * 4
    return {"summary": {**base, **kw}, "folds": kw.get("_folds", folds)}


def verdict(wf=None, pmap=None, robust=None, multi=None, stress=None):
    return lab.build_verdict(
        wf or wf_summary(), pmap or {"available": True, "stability": {"verdict": "stable", "text": "ок"}},
        robust or {}, multi or {"pbo": {"available": False}, "dsr": {"available": False}},
        stress if stress is not None else [{"return_pct": 18.0}, {"return_pct": 15.0}, {"return_pct": 12.0}], 1200,
    )


def codes(v) -> set[str]:
    return {f["code"] for f in v["findings"]}


def test_verdict_robust_when_windows_params_and_costs_all_hold() -> None:
    v = verdict()
    assert v["status"] == "robust" and "survived_unseen" in codes(v) and "params_stable" in codes(v)
    assert "не гарантирует" in v["disclaimer"]


def test_verdict_insufficient_when_too_few_trades_or_windows() -> None:
    v = verdict(wf=wf_summary(trades=4))
    assert v["status"] == "insufficient" and "not_enough_data" in codes(v)
    flat = wf_summary(usable_folds=0, profitable_folds=0, stitched_return_pct=None, trades=0)
    flat["folds"] = [{"status": "no_choice"}] * 4
    f = verdict(wf=flat)
    assert f["status"] == "insufficient" and "ни один вариант не набрал" in f["findings"][0]["text"]


def test_verdict_names_each_failure_in_plain_words() -> None:
    assert "unstable" in codes(verdict(wf=wf_summary(profitable_folds=1, stitched_return_pct=-4.0)))
    assert verdict(wf=wf_summary(profitable_folds=1, stitched_return_pct=-4.0))["status"] == "fragile"
    fragile_map = {"available": True, "stability": {"verdict": "fragile", "text": "пик"}}
    assert "params_fragile" in codes(verdict(pmap=fragile_map)) and verdict(pmap=fragile_map)["status"] == "fragile"
    costs = verdict(stress=[{"return_pct": 8.0}, {"return_pct": 2.0}, {"return_pct": -3.0}])
    assert "costs_kill" in codes(costs) and costs["status"] == "fragile"
    conc = verdict(robust={"concentration": {"top3_profit_share_pct": 90.0, "return_without_best_pct": -2.0, "total_return_pct": 10.0}})
    assert "concentrated" in codes(conc) and conc["status"] == "fragile"
    pbo = verdict(multi={"pbo": {"available": True, "value": 0.7}, "dsr": {"available": False}})
    assert "overfit_selection" in codes(pbo) and pbo["status"] == "fragile"


def test_verdict_warns_but_does_not_condemn_on_soft_signals() -> None:
    dsr = verdict(multi={"pbo": {"available": False}, "dsr": {"available": True, "value": 0.7, "trials": 40}})
    assert "multiple_testing" in codes(dsr) and dsr["status"] in ("robust", "mixed")
    regime = verdict(robust={"regimes": {"regimes": [{"id": "trend_up", "label": "тренд вверх"}], "dominant": "trend_up"}})
    assert "regime_dependent" in codes(regime)
    boot = verdict(robust={"bootstrap": {"prob_no_edge_pct": 40.0, "mean_trade_pct": 1.0, "mean_p5_pct": -0.5, "mean_p95_pct": 2.0}})
    assert "edge_uncertain" in codes(boot)
    two = verdict(multi={"pbo": {"available": False}, "dsr": {"available": True, "value": 0.7, "trials": 40}},
                  robust={"regimes": {"regimes": [{"id": "trend_up", "label": "x"}], "dominant": "trend_up"}})
    assert two["status"] == "mixed"  # два предупреждения — уже не «устойчиво»


# --- API ---


def client(env: Env, n: int = 1200) -> TestClient:
    df = series(n)
    env.adapter.data["SBER"] = day_candles(list(df["close"]))
    env.now[0] = 10_000 * DAY
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


BODY = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 1200}


def test_lab_api_end_to_end_and_every_run_counts_toward_multiple_testing(env: Env) -> None:
    c = client(env)
    r = c.post("/api/lab", json={**BODY, "grid": GRID, "folds": 4, "mode": "anchored"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"]["status"] in ("robust", "mixed", "fragile", "insufficient") and body["verdict"]["findings"]
    assert body["walk_forward"]["summary"]["folds"] == 4 and len(body["walk_forward"]["folds"]) == 4
    assert body["parameter_map"]["params"] == ["fast", "slow"] and len(body["parameter_map"]["matrix"]) == 4
    assert "_returns" not in str(body["parameter_map"].keys())
    assert body["multiple_testing"]["variants_this_run"] == 16 and body["multiple_testing"]["pbo"]["available"]
    assert [x["multiplier"] for x in body["robustness"]["cost_stress"]] == [1, 2, 3]
    assert body["run_card"]["spread_pct"] == 0.05 and body["trials"]["this_run_variants"] == 16
    again = c.post("/api/lab", json={**BODY, "grid": GRID}).json()
    assert again["multiple_testing"]["prior_variants"] == 16 and again["trials"]["total_variants"] == 32
    hist = c.get("/api/experiments").json()
    assert hist[0]["config"]["lab"] is True and hist[0]["n_variants"] == 16


def test_lab_api_fixed_rule_without_grid(env: Env) -> None:
    body = client(env).post("/api/lab", json={**BODY, "folds": 5}).json()
    assert body["walk_forward"]["summary"]["optimized"] is False and body["parameter_map"]["available"] is False
    assert body["multiple_testing"]["variants_this_run"] == 1


def test_lab_api_validates_input(env: Env) -> None:
    c = client(env)
    assert c.post("/api/lab", json={**BODY, "folds": 2}).status_code == 422
    assert c.post("/api/lab", json={**BODY, "folds": 9}).status_code == 422
    assert c.post("/api/lab", json={**BODY, "mode": "diagonal"}).status_code == 422
    three = {"period": [5, 10], "buy_below": [20, 30], "exit_above": [50, 60]}
    assert c.post("/api/lab", json={**BODY, "strategy": "rsi_reversion", "params": {}, "grid": three}).status_code == 422
    assert c.post("/api/lab", json={**BODY, "grid": {"nope": [1, 2]}}).status_code == 422
    assert c.post("/api/lab", json={**BODY, "strategy": "nope"}).status_code == 404


def test_lab_api_reports_short_history_in_plain_words(env: Env) -> None:
    c = client(env, n=300)
    r = c.post("/api/lab", json={**BODY, "limit": 300, "grid": GRID})
    assert r.status_code == 422 and "Мало истории" in r.json()["detail"]
