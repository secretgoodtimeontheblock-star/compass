"""Слой дисциплины: слабые допущения бэктеста, сравнение планов, «что изменилось», глоссарий и AI-пересказ фактов."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from compass import discipline, glossary
from compass.api.app import create_app
from tests.conftest import DAY, Env, day_candles
from tests.test_plans import make_plan

# --- глоссарий ---


def test_every_code_points_to_an_existing_term_and_terms_are_complete() -> None:
    assert glossary.validate() == []
    for t in glossary.all_terms():
        assert t["term"] and len(t["short"]) > 20 and len(t["why"]) > 20 and not any(ch.isdigit() for ch in t["term"][:1])
    assert glossary.term("nope") is None and glossary.concepts_for("nope") == []


def test_lab_cockpit_and_weakness_codes_are_all_mapped_to_concepts() -> None:
    lab_codes = ["not_enough_data", "unstable", "params_fragile", "costs_kill", "concentrated", "edge_uncertain",
                 "overfit_selection", "multiple_testing", "regime_dependent", "drawdown_open"]
    rule_codes = ["daily_loss", "trades_limit", "heat", "stop_breached", "no_stop", "plan_risk"]
    weak_codes = ["few_trades", "short_history", "no_spread", "high_participation", "gaps", "ambiguous", "no_stops",
                  "prior_variants", "integrity_failed", "stale_data", "below_buy_hold", "in_sample_only", "delayed_feed", "open_trade"]
    for code in lab_codes + rule_codes + weak_codes:
        assert glossary.concepts_for(code), code
    assert glossary.attach([{"code": "costs_kill"}])[0]["learn"] == ["slippage", "spread", "fees"]


# --- слабые допущения бэктеста ---


def result(**over) -> dict:
    base = {
        "metrics": {"trades": 40, "total_return_pct": 30.0, "buy_hold_return_pct": 20.0, "ambiguous_bars": 0, "gap_exits": 0},
        "execution": {"model": {"spread_pct": 0.05}, "max_participation_pct": 1.0},
        "coverage": {"first_ts": 0, "last_ts": 3 * 365 * DAY, "candles": 900},
        "integrity": {"passed": True, "summary": ""},
        "run_card": {"rules": {"stop_atr_mult": 2.0}}, "trials": {"total_variants": 1}, "stale": False, "intraday": None,
    }
    return {**base, **over}


def codes(ws) -> list[str]:
    return [w["code"] for w in ws]


def test_clean_backtest_has_only_the_unavoidable_in_sample_caveat() -> None:
    ws = discipline.backtest_weaknesses(result())
    assert codes(ws) == ["in_sample_only"] and ws[0]["severity"] == "info" and ws[0]["learn"]


def test_each_weak_assumption_is_detected_with_the_right_severity_and_ordered() -> None:
    bad = result(
        metrics={"trades": 6, "total_return_pct": 5.0, "buy_hold_return_pct": 20.0, "ambiguous_bars": 3, "gap_exits": 2, "open_trade": 1},
        execution={"model": {"spread_pct": 0.0}, "max_participation_pct": 35.0},
        coverage={"first_ts": 0, "last_ts": 100 * DAY, "candles": 100},
        integrity={"passed": False, "summary": "Решение зависит от будущего."},
        run_card={"rules": None}, trials={"total_variants": 25}, stale=True, intraday={"feed_delay_seconds": 900},
    )
    ws = discipline.backtest_weaknesses(bad)
    got = {w["code"]: w["severity"] for w in ws}
    assert got["integrity_failed"] == "bad" and got["few_trades"] == "bad" and got["high_participation"] == "bad"
    assert got["short_history"] == "warn" and got["no_spread"] == "warn" and got["prior_variants"] == "warn"
    assert got["stale_data"] == "warn" and got["delayed_feed"] == "warn"
    assert {"ambiguous", "gaps", "no_stops", "below_buy_hold", "open_trade", "in_sample_only"} <= set(got)
    sev = [{"bad": 0, "warn": 1, "info": 2}[w["severity"]] for w in ws]
    assert sev == sorted(sev) and ws[0]["code"] == "integrity_failed"
    mid = discipline.backtest_weaknesses(result(metrics={"trades": 20, "total_return_pct": 1, "buy_hold_return_pct": 0},
                                                execution={"model": {"spread_pct": 0.05}, "max_participation_pct": 8.0}))
    assert {w["code"]: w["severity"] for w in mid}["few_trades"] == "warn"
    assert {w["code"]: w["severity"] for w in mid}["high_participation"] == "warn"


# --- сравнение с прежними планами ---


def hist(n: int, risk: float = 100.0, strategy: str | None = None):
    out = []
    for i in range(n):
        p = make_plan(risk_amount=risk, strategy=strategy, entry=100.0, stop=90.0, cost=1000.0)
        object.__setattr__(p, "id", i + 1)
        out.append((p, {"status": "closed", "result_pct": 5.0 if i % 2 else -2.0, "r_multiple": 1.5 if i % 2 else -1.0}))
    return out


def test_comparison_flags_what_is_much_bigger_or_smaller_than_usual_without_judging() -> None:
    plan = make_plan(risk_amount=300.0, cost=1000.0, strategy="donchian")  # риск втрое выше медианы 100
    object.__setattr__(plan, "id", 99)
    c = discipline.trade_comparison(plan, hist(5, risk=100.0))
    d = {x["metric"]: x for x in c["differences"]}
    assert c["enough"] and c["previous_plans"] == 5
    assert d["risk_pct"]["ratio"] == 3.0 and d["risk_pct"]["note"] == "заметно больше обычного"
    assert d["stop_distance_pct"]["note"] is None and d["size_pct"]["note"] is None
    assert c["outcomes"]["closed"] == 5 and c["outcomes"]["wins"] == 2
    assert "не оценка качества" in c["note"] and "Малое число" in c["note"]
    small = make_plan(risk_amount=20.0, cost=1000.0)
    object.__setattr__(small, "id", 98)
    assert {x["metric"]: x for x in discipline.trade_comparison(small, hist(5, risk=100.0))["differences"]}["risk_pct"]["note"] == "заметно меньше обычного"


def test_comparison_needs_enough_previous_plans_and_ignores_itself() -> None:
    plan = make_plan()
    object.__setattr__(plan, "id", 99)
    c = discipline.trade_comparison(plan, hist(2))
    assert c["enough"] is False and c["differences"] == [] and "нужно хотя бы 3" in c["note"]
    selfless = discipline.trade_comparison(plan, [(plan, {"status": "open"})] + hist(2))
    assert selfless["previous_plans"] == 2  # сам план в «прежние» не попадает


def test_comparison_reports_same_strategy_history() -> None:
    plan = make_plan(strategy="donchian")
    object.__setattr__(plan, "id", 50)
    c = discipline.trade_comparison(plan, hist(4, strategy="donchian") + hist(3, strategy="sma_cross")[:0])
    assert c["same_strategy"] == {"strategy": "donchian", "closed": 4, "wins": 2, "median_result_pct": 1.5}


# --- нарушения и «что изменилось» ---


def weekly(**kw) -> dict:
    base = {"name": "Рубли", "currency": "RUB", "days": 7, "closed_trades": 8, "realized": -1200.0, "entries": 10,
            "entries_without_stop": 3, "entries_without_plan": 4, "fees": 150.0, "days_over_daily_limit": ["2026-09-29"],
            "days_over_trades_limit": [], "plan_deviations": {"plans_reviewed": 5, "worse_entry": 2, "bigger_qty": 1, "risk_exceeded": 1, "no_stop": 0}}
    return {**base, **kw}


def test_violations_combine_cockpit_rules_with_the_weekly_journal() -> None:
    cockpit = {"status": "attention", "questions": [{"id": "rules", "items": [{"text": "Рубли: у GAZP не записан стоп."}]}]}
    d = discipline.discipline_facts(cockpit, [weekly()])
    text = " ".join(d["violations"])
    for expected in ("у GAZP не записан стоп", "без записанного стопа за 7 дн. — 3 из 10", "выше бюджета плана в 1 из 5",
                     "количество больше планового в 1 из 5", "вход хуже плановой цены в 2 из 5",
                     "превышением дневного лимита убытка — 1", "входов без плана — 4 из 10"):
        assert expected in text, expected
    assert "дней с превышением лимита входов" not in text
    clean = discipline.discipline_facts({"status": "ok", "questions": [{"id": "rules", "items": []}]},
                                        [weekly(entries_without_stop=0, entries_without_plan=0, days_over_daily_limit=[],
                                                plan_deviations={"plans_reviewed": 5, "worse_entry": 0, "bigger_qty": 0, "risk_exceeded": 0, "no_stop": 0})])
    assert clean["violations"] == [] and "Отступлений от правил по данным нет" in discipline.facts_text_discipline(clean)


def test_what_changed_keeps_only_events_inside_the_window() -> None:
    now = 10 * DAY
    acc = [{"name": "Рубли", "currency": "RUB", "equity": 100_000, "unrealized_total": 500.0, "daily_pnl": 0.0, "heat_pct": 1.2, "positions": [1]}]
    entries = [{"ts": now - 3_600_000, "symbol": "SBER", "side": "buy", "qty": 10, "price": 100},
               {"ts": now - 5 * DAY, "symbol": "GAZP", "side": "buy", "qty": 1, "price": 1}]
    sigs = [{"symbol": "SBER", "strategy": "donchian", "side": "buy", "created_at": (now - 7_200_000) // 1000, "status": "active"},
            {"symbol": "LKOH", "strategy": "donchian", "side": "buy", "created_at": (now - 4 * DAY) // 1000}]
    c = discipline.what_changed(now, 24, acc, entries, sigs, [], [{"symbol": "SBER", "change_pct": -2.5, "mark_price": 97.5}])
    assert [e["symbol"] for e in c["entries"]] == ["SBER"] and [s["symbol"] for s in c["signals"]] == ["SBER"] and not c["empty"]
    text = discipline.facts_text_changed(c)
    assert "SBER покупка 10 × 100" in text and "-2.50%" in text and "GAZP" not in text and "LKOH" not in text
    quiet = discipline.what_changed(now, 24, acc, [], [], [], [])
    assert quiet["empty"] and "не было новых записей" in discipline.facts_text_changed(quiet)


# --- API ---


def wavy(n: int = 300) -> list[float]:
    return [100 + 15 * math.sin(i / 7) + i * 0.03 for i in range(n)]


def client(env: Env) -> TestClient:
    env.adapter.data["SBER"] = day_candles(wavy())
    env.now[0] = 10_000 * DAY
    env.services.accounts.update("moex", {"capital": 100_000})
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


BODY = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 300}


def test_backtest_api_lists_weak_assumptions_with_concepts_to_learn(env: Env) -> None:
    r = client(env).post("/api/backtest", json=BODY).json()
    ws = r["weaknesses"]
    assert ws and ws[-1]["code"] == "in_sample_only" and all("learn" in w for w in ws)
    assert "no_stops" in {w["code"] for w in ws} and glossary.term(ws[-1]["learn"][0])


def test_glossary_and_discipline_endpoints(env: Env) -> None:
    c = client(env)
    terms = c.get("/api/glossary").json()
    assert len(terms) >= 25 and {"id", "term", "short", "why"} <= set(terms[0])
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100, "ts": env.now[0] - 3_600_000})
    changed = c.get("/api/discipline/changed", params={"hours": 24}).json()
    assert changed["hours"] == 24 and changed["entries"][0]["symbol"] == "SBER" and "Запись журнала: SBER покупка 10" in changed["text"]
    assert any(m["symbol"] == "SBER" for m in changed["moves"])
    viol = c.get("/api/discipline/violations").json()
    assert any("не записан стоп" in v for v in viol["violations"]) and viol["weeks"][0]["entries"] >= 0
    assert c.get("/api/discipline/changed", params={"hours": 0}).status_code == 422


def test_compare_trade_endpoint_uses_only_earlier_plans(env: Env) -> None:
    c = client(env)
    ids = []
    for risk_entry in (200, 200, 200, 200, 400):
        ids.append(c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": risk_entry, "stop": risk_entry * 0.95, "reason": "тест"}).json()["id"])
    first = c.get("/api/discipline/compare-trade", params={"plan_id": ids[0]}).json()
    assert first["previous_plans"] == 0 and first["enough"] is False
    last = c.get("/api/discipline/compare-trade", params={"plan_id": ids[-1]}).json()
    assert last["previous_plans"] == 4 and last["enough"] is True
    assert c.get("/api/discipline/compare-trade", params={"plan_id": 999}).status_code == 404


def test_ai_endpoints_send_code_calculated_facts_and_never_record_experiments(env: Env) -> None:
    c = client(env)
    env.services.settings.update({"ai_provider": "ollama"})
    before = len(c.get("/api/experiments").json())
    r = c.post("/api/ai/explain-backtest", json=BODY)
    assert r.status_code == 200 and r.json()["text"].startswith("Ответ AI")
    system, prompt, _ = env.local.calls[-1]
    assert "Найденные кодом слабые допущения" in prompt and "[info]" in prompt and "не говори, что купить" not in prompt
    assert len(c.get("/api/experiments").json()) == before  # объяснение не увеличивает число проб

    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100, "ts": env.now[0] - 3_600_000})
    assert c.post("/api/ai/what-changed", json={"hours": 24}).status_code == 200
    assert "Запись журнала: SBER покупка 10" in env.local.calls[-1][1] and "не предсказывай цены" in env.local.calls[-1][0]
    assert c.post("/api/ai/discipline", json={"days": 7}).status_code == 200
    assert "Отступления от ваших правил" in env.local.calls[-1][1]
    pid = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190, "reason": "тест"}).json()["id"]
    assert c.post("/api/ai/compare-trade", json={"plan_id": pid}).status_code == 200
    assert "Прежних планов: 0" in env.local.calls[-1][1]
    assert c.post("/api/ai/compare-trade", json={"plan_id": 999}).status_code == 404


def test_ai_concept_explanation_uses_the_glossary_definition(env: Env) -> None:
    c = client(env)
    env.services.settings.update({"ai_provider": "ollama"})
    assert c.post("/api/ai/explain-concept", json={"concept_id": "slippage", "context": "результат при втрое больших расходах"}).status_code == 200
    system, prompt, _ = env.local.calls[-1]
    assert "Определение в Compass:" in prompt and "Проскальзывание" in prompt and "втрое больших" in prompt
    assert c.post("/api/ai/explain-concept", json={"concept_id": "nope"}).status_code == 404
