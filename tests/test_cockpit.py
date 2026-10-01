"""Decision Cockpit: семь вопросов дня из одних и тех же данных, приоритеты и общий статус."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compass import cockpit, portfolio
from compass.accounts import Account
from compass.api.app import create_app
from compass.journal import Entry
from compass.models import Instrument
from compass.portfolio import Mark
from tests.conftest import DAY, Env, day_candles

HOUR = 3_600_000
MSK_MIDNIGHT = 1_790_542_800_000
NOW = MSK_MIDNIGHT + 15 * HOUR


def acc(**kw) -> Account:
    return Account(**{"market": "moex", "name": "Рубли", "currency": "RUB", "capital": 100_000.0, **kw})


def buy(qty, price, ts=NOW - DAY, symbol="SBER", stop=None) -> Entry:
    return Entry("moex", symbol, "buy", qty, price, ts, planned_stop=stop)


def sell(qty, price, ts, symbol="SBER") -> Entry:
    return Entry("moex", symbol, "sell", qty, price, ts)


def mark(price, stale=False) -> Mark:
    return Mark(price, NOW - HOUR, NOW // 1000 - 60, stale, "test", 3600)


WATCH_OK = {"background_scanner": True, "quiet_hours": {"active_now": False}, "warnings": []}


def build(snaps, plans=(), signals=(), watch=None, problems=()):
    return cockpit.build(list(snaps), list(plans), list(signals), watch or WATCH_OK, list(problems), NOW)


def by_id(c, qid):
    return next(q for q in c["questions"] if q["id"] == qid)


def plan(status="open", created_days_ago=1, **kw) -> dict:
    rv = {"status": status, "deviations": [], "risk_exceeded": False, **kw.pop("review", {})}
    return {"id": 1, "market": "moex", "symbol": "SBER", "entry": 100, "stop": 90, "target": 120, "risk_amount": 100,
            "currency": "RUB", "created_at": NOW / 1000 - created_days_ago * 86_400, "review": rv, **kw}


def test_calm_day_has_seven_questions_and_ok_status() -> None:
    snap = portfolio.snapshot(acc(), [buy(100, 100, stop=90)], NOW, {"SBER": mark(105)})
    c = build([snap])
    assert [q["id"] for q in c["questions"]] == ["now", "positions", "risk", "plans", "signals", "rules", "data"]
    assert all(q["question"].endswith("?") and q["answer"] for q in c["questions"])
    assert c["status"] == "ok" and "Всё в порядке" in c["headline"] and c["attention"] == []
    assert "+500.00 RUB" in by_id(c, "positions")["answer"]  # нереализованный результат из Portfolio Truth
    assert by_id(c, "rules")["answer"].startswith("Нарушений")


def test_daily_loss_limit_means_stop_for_today_by_users_own_plan() -> None:
    entries = [buy(100, 100, NOW - DAY), sell(100, 95, NOW - HOUR)]  # −500 при лимите 3% = 3000: не стоп
    assert build([portfolio.snapshot(acc(), entries, NOW)])["status"] == "ok"
    big = [buy(1000, 100, NOW - DAY), sell(1000, 96, NOW - HOUR)]  # −4000 > 3000
    c = build([portfolio.snapshot(acc(), big, NOW)])
    assert c["status"] == "stop" and "остановиться" in c["headline"] or "стоп" in c["headline"]
    assert by_id(c, "rules")["status"] == "bad" and c["attention"][0]["severity"] == "stop"
    assert "daily_loss" in {i["code"] for i in by_id(c, "rules")["items"]}


def test_real_risk_flags_heat_over_limit_and_missing_stop() -> None:
    snap = portfolio.snapshot(acc(), [buy(100, 100, stop=30), buy(10, 100, symbol="GAZP")], NOW, {"SBER": mark(100), "GAZP": mark(100)})
    c = build([snap])
    risk = by_id(c, "risk")
    assert risk["status"] == "bad" and "(7% из 6%)" in risk["answer"]
    texts = " ".join(a["text"] for a in c["attention"])
    assert "суммарный риск 7% выше предела 6%" in texts and "у GAZP не записан стоп" in texts
    assert c["status"] == "attention" and {a["section"] for a in c["attention"]} == {"rules"}  # без дублей из раздела риска
    assert len(texts.split("Рубли:")) - 1 == len(c["attention"]) and all("hint" in a for a in c["attention"])


def test_attention_is_sorted_by_severity() -> None:
    snap = portfolio.snapshot(acc(), [buy(100, 100, symbol="GAZP")], NOW)  # нет стопа и нет цен → bad
    c = build([snap], signals=[{"id": 1, "market": "moex", "symbol": "SBER", "strategy": "donchian", "side": "buy", "seen": False}],
              watch={**WATCH_OK, "background_scanner": False})
    order = [a["severity"] for a in c["attention"]]
    assert order == sorted(order, key={"stop": 0, "bad": 1, "attention": 2, "info": 3}.__getitem__)
    assert order[0] == "bad" and "attention" in order


def test_plans_flag_stale_waiting_exceeded_risk_and_unrecorded_stop() -> None:
    old = plan(created_days_ago=cockpit.PLAN_STALE_DAYS + 1)
    c = build([portfolio.snapshot(acc(), [], NOW)], plans=[old])
    q = by_id(c, "plans")
    assert q["status"] == "attention" and "ждёт входа" in q["items"][0]["flags"][0]
    fresh = build([portfolio.snapshot(acc(), [], NOW)], plans=[plan(created_days_ago=2)])
    assert by_id(fresh, "plans")["status"] == "ok" and "ждут входа — 1" in by_id(fresh, "plans")["answer"]
    exceeded = plan("entered", review={"risk_exceeded": True, "deviations": [{"code": "stop", "actual": None}]})
    c2 = build([portfolio.snapshot(acc(), [], NOW)], plans=[exceeded])
    assert by_id(c2, "plans")["status"] == "bad"
    assert any(i["code"] == "plan_risk" for i in by_id(c2, "rules")["items"])
    assert any("стоп из плана" in f for f in by_id(c2, "plans")["items"][0]["flags"])


def test_signals_unseen_need_attention_and_late_ones_are_noted() -> None:
    sigs = [{"id": 1, "market": "moex", "symbol": "SBER", "strategy": "donchian", "side": "buy", "seen": False, "late": True},
            {"id": 2, "market": "moex", "symbol": "GAZP", "strategy": "donchian", "side": "buy", "seen": True}]
    c = build([portfolio.snapshot(acc(), [], NOW)], signals=sigs)
    q = by_id(c, "signals")
    assert q["status"] == "attention" and "новых 1" in q["answer"] and len(q["items"]) == 2
    assert any("с задержкой" in a["text"] for a in c["attention"])
    assert by_id(build([portfolio.snapshot(acc(), [], NOW)]), "signals")["answer"] == "Актуальных сигналов нет."


def test_data_question_covers_marks_sources_and_reconciliation() -> None:
    held = [buy(10, 100, stop=90)]
    unmarked = build([portfolio.snapshot(acc(), held, NOW)])
    assert by_id(unmarked, "data")["status"] == "bad" and "нет рыночных цен" in unmarked["attention"][0]["text"]
    stale = build([portfolio.snapshot(acc(), held, NOW, {"SBER": mark(100, stale=True)})])
    assert by_id(stale, "data")["status"] == "attention"
    mismatch = build([portfolio.snapshot(acc(), held, NOW, {"SBER": mark(100)}, [{"symbol": "SBER", "qty": 9}])])
    assert by_id(mismatch, "data")["status"] == "bad" and any("расходится с выпиской" in a["text"] for a in mismatch["attention"])
    problem = build([portfolio.snapshot(acc(), [], NOW)], problems=[{"market": "moex", "symbol": "GAZP", "status": "stale", "message": "x"}])
    assert "GAZP: данные не обновляются" in by_id(problem, "data")["answer"] and problem["status"] == "attention"


def test_accounts_stay_separate_and_scanner_off_is_reported() -> None:
    rub = portfolio.snapshot(acc(), [buy(10, 100, stop=90)], NOW, {"SBER": mark(100)})
    usdt = portfolio.snapshot(Account("crypto", "USDT", "USDT", 5_000.0), [], NOW)
    c = build([rub, usdt], watch={**WATCH_OK, "background_scanner": False})
    assert by_id(c, "now")["status"] == "attention" and "Наблюдение не идёт" in by_id(c, "now")["answer"]
    risk_items = by_id(c, "risk")["items"]
    assert [i["currency"] for i in risk_items] == ["RUB", "USDT"]  # валюты не складываются


def test_cockpit_api_assembles_everything_from_the_same_sources(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles([100, 101, 105])
    env.services.watchlist.add(Instrument("SBER", "Сбербанк", "moex"))
    env.services.accounts.update("moex", {"capital": 100_000})
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100,
                                 "ts": env.now[0] - DAY, "planned_stop": 95})
    plan = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190, "reason": "тест"}).json()
    body = c.get("/api/cockpit").json()
    assert body["status"] in ("ok", "attention") and len(body["questions"]) == 7
    pos = next(q for q in body["questions"] if q["id"] == "positions")
    assert pos["items"][0]["mark_price"] == 105 and pos["items"][0]["unrealized_pnl"] == 50
    plans = next(q for q in body["questions"] if q["id"] == "plans")
    assert [p["id"] for p in plans["items"]] == [plan["id"]] and plans["items"][0]["status"] == "open"
    day = next(a for a in c.get("/api/day").json()["accounts"] if a["market"] == "moex")
    assert pos["items"][0]["unrealized_pnl"] == day["positions"][0]["unrealized_pnl"]  # те же числа, что на панели дня
    assert "generated_at" in body and body["notes"]


def test_cockpit_api_with_daily_loss_limit_reports_stop(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles([100, 100, 100])
    env.services.accounts.update("moex", {"capital": 10_000})
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    t = env.now[0] - HOUR
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100, "ts": t - 1000})
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "sell", "qty": 10, "price": 60, "ts": t})
    body = c.get("/api/cockpit").json()
    assert body["status"] == "stop" and body["attention"][0]["severity"] == "stop"
    assert pytest.approx(1) == 1  # статус «stop» не блокирует API: журнал по-прежнему принимает записи
    assert c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 1, "price": 60, "ts": t + 1000}).status_code == 201
