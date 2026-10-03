"""Планы сделок: неизменяемость, связь с сигналом и журналом, сравнение план/факт, версия стратегии."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.db import connect
from compass.journal import Entry, Journal
from compass.models import Instrument
from compass.plans import Plan, PlanStore, review
from compass.strategies import STRATEGIES, strategy_version
from compass.validation import ENGINE_VERSION
from tests.conftest import DAY, Env, day_candles, with_next_open

BREAKOUT = [10.0] * 45 + [12.0]


def make_plan(**over) -> Plan:
    base = {
        "market": "moex", "symbol": "SBER", "source": "moex:iss", "entry": 100.0, "stop": 90.0, "qty": 10, "lots": 10, "capital": 10_000,
        "risk_pct": 1.0, "fee_pct": 0.05, "slippage_pct": 0.05, "cost": 1001.0, "risk_amount": 102.0, "risk_amount_worse": 104.0,
        "budget": 100.0, "target": 130.0, "reason": "пробой", "reward_risk": 3.0,
    }
    return Plan(**{**base, **over})


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


# --- версия стратегии ---


def test_strategy_version_is_stable_and_sensitive_to_params_and_revision() -> None:
    s = STRATEGIES["donchian"]
    a = strategy_version(s, {"entry": 20, "exit": 10})
    assert a == strategy_version(s, {"exit": 10, "entry": 20})  # порядок ключей не важен
    assert a != strategy_version(s, {"entry": 21, "exit": 10})
    assert a.startswith("donchian/r1/")
    from dataclasses import replace

    assert a != strategy_version(replace(s, version=2), {"entry": 20, "exit": 10})


def test_signal_and_backtest_carry_the_same_strategy_version(env: Env) -> None:
    env.services.watchlist.add(Instrument("XYZ", "XYZ", "moex"))
    env.adapter.data["XYZ"] = with_next_open(day_candles(BREAKOUT))
    env.now[0] = len(BREAKOUT) * DAY + 1
    env.services.settings.update(
        {"instrument_strategies": {"moex|XYZ": {"strategy": "donchian", "params": {"entry": 5, "exit": 3}}}}
    )
    env.services.engine.scan()
    (sig,) = env.services.signals.list()
    expected = strategy_version(STRATEGIES["donchian"], {"entry": 5, "exit": 3})
    assert sig.strategy_version == expected
    c = client(env)
    r = c.post("/api/backtest", json={"market": "moex", "symbol": "XYZ", "strategy": "donchian",
                                      "params": {"entry": 5, "exit": 3}, "limit": 100}).json()
    assert r["run_card"]["strategy_version"] == expected


# --- хранилище ---


def test_plan_is_immutable_in_database() -> None:
    conn = connect(":memory:")
    store = PlanStore(conn)
    plan = store.add(make_plan())
    assert plan.id and plan.uid and store.get(plan.id).entry == 100.0
    with pytest.raises(sqlite3.DatabaseError, match="неизменяем"):
        conn.execute("UPDATE plans SET stop = 50 WHERE id = ?", (plan.id,))
    with pytest.raises(sqlite3.DatabaseError, match="нельзя удалить"):
        conn.execute("DELETE FROM plans WHERE id = ?", (plan.id,))
    assert store.get(plan.id).stop == 90.0


@pytest.mark.parametrize(
    "over",
    [{"stop": 100.0}, {"stop": 0.0}, {"target": 100.0}, {"target": 90.0}, {"qty": 0}, {"reason": "  "},
     {"reason": "x" * 501}, {"entry": float("nan")}],
)
def test_plan_rejects_nonsense(over) -> None:
    with pytest.raises(ValueError):
        PlanStore(connect(":memory:")).add(make_plan(**over))


# --- API ---


def test_manual_plan_matches_risk_calculator_and_snapshots_source(env: Env) -> None:
    c = client(env)
    body = {"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190, "target": 230, "reason": "  пробой уровня "}
    risk = c.post("/api/risk", json={k: body[k] for k in ("market", "symbol", "entry", "stop")}).json()
    plan = c.post("/api/plans", json=body)
    assert plan.status_code == 201
    p = plan.json()
    assert (p["qty"], p["cost"], p["risk_amount"], p["risk_amount_worse"], p["budget"]) == (
        risk["qty"], risk["cost"], risk["risk_amount"], risk["risk_amount_worse"], risk["budget"])
    assert p["reward_risk"] == 3.0 and p["reason"] == "пробой уровня" and p["source"] == "moex"
    assert p["strategy"] is None and p["currency"] == "RUB" and p["assumptions"]
    # смена настроек и комиссий после создания не переписывает план
    c.put("/api/settings", json={"capital": 1_000_000, "risk_pct": 5})
    assert c.get(f"/api/plans/{p['id']}").json()["qty"] == p["qty"]
    assert [x["id"] for x in c.get("/api/plans", params={"symbol": "SBER"}).json()] == [p["id"]]
    assert c.get("/api/plans/999").status_code == 404


def test_plan_validation_errors_are_422(env: Env) -> None:
    c = client(env)
    ok = {"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190, "reason": "причина"}
    assert c.post("/api/plans", json={**ok, "reason": ""}).status_code == 422
    assert c.post("/api/plans", json={**ok, "stop": 210}).status_code == 422
    assert c.post("/api/plans", json={**ok, "target": 150}).status_code == 422
    assert c.post("/api/plans", json={**ok, "params": {"fast": 1}}).status_code == 422
    assert c.post("/api/plans", json={**ok, "market": "nope"}).status_code == 404
    assert c.post("/api/plans", json={**ok, "strategy": "nope"}).status_code == 404
    assert c.post("/api/plans", json={**ok, "signal_id": 999}).status_code == 404
    zero = c.post("/api/plans", json={**ok, "capital": 100_000, "risk_pct": 0.01, "entry": 274, "stop": 260})
    assert zero.status_code == 422 and "лот" in zero.json()["detail"]  # лот рискует больше бюджета
    assert c.get("/api/plans").json() == []  # ничего не сохранилось


def test_plan_from_signal_inherits_strategy_version_and_checks_instrument(env: Env) -> None:
    env.services.watchlist.add(Instrument("XYZ", "XYZ", "moex"))
    env.adapter.data["XYZ"] = with_next_open(day_candles(BREAKOUT))
    env.now[0] = len(BREAKOUT) * DAY + 1
    env.services.settings.update(
        {"instrument_strategies": {"moex|XYZ": {"strategy": "donchian", "params": {"entry": 5, "exit": 3}}}}
    )
    env.services.engine.scan()
    (sig,) = env.services.signals.list()
    c = client(env)
    body = {"market": "moex", "symbol": "XYZ", "entry": 12.0, "stop": 9.0, "reason": "по сигналу", "signal_id": sig.id}
    p = c.post("/api/plans", json=body).json()
    assert p["strategy"] == "donchian" and p["params"] == {"entry": 5, "exit": 3}
    assert p["strategy_version"] == sig.strategy_version and p["signal_id"] == sig.id
    assert c.post("/api/plans", json={**body, "symbol": "SBER"}).status_code == 422  # чужой инструмент
    # редакция правила сменилась уже после сигнала: план хранит версию сигнала, а не пересчитанную
    with env.services.signals._conn as conn:
        conn.execute("UPDATE signals SET strategy_version = 'donchian/r0/deadbeef' WHERE id = ?", (sig.id,))
    assert c.post("/api/plans", json=body).json()["strategy_version"] == "donchian/r0/deadbeef"
    # сигнал старого образца: параметров нет — план это честно отмечает
    with env.services.signals._conn as conn:
        conn.execute("UPDATE signals SET params = NULL, strategy_version = NULL WHERE id = ?", (sig.id,))
    old = c.post("/api/plans", json=body).json()
    assert old["strategy_version"] is None and any("не сохранены" in w for w in old["warnings"])


def test_journal_entry_links_to_plan_and_review_reports_deviations(env: Env) -> None:
    c = client(env)
    plan = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190,
                                      "target": 230, "reason": "пробой"}).json()
    entry = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 202, "ts": 1_700_000_000_000,
             "planned_stop": 190, "plan_uid": plan["uid"]}
    assert c.get(f"/api/plans/{plan['id']}/review").json()["status"] == "open"
    assert c.post("/api/journal", json={**entry, "plan_uid": "nope"}).status_code == 422
    assert c.post("/api/journal", json={**entry, "symbol": "GAZP", "plan_uid": plan["uid"]}).status_code == 422
    saved = c.post("/api/journal", json=entry)
    assert saved.status_code == 201 and saved.json()["plan_uid"] == plan["uid"]
    r = c.get(f"/api/plans/{plan['id']}/review").json()
    dev = {d["code"]: d for d in r["deviations"]}
    assert r["status"] == "entered" and r["avg_entry"] == 202
    assert dev["entry"]["diff_pct"] == 1.0 and dev["entry"]["worse"] is True
    assert dev["qty"]["actual"] == 10 and dev["stop"]["actual"] == 190
    assert "plan_uid" in c.get("/api/journal.csv").text


# --- сравнение план/факт (чистая функция) ---


def e(side: str, qty: float, price: float, stop: float | None = None, fee: float = 0.0, uid: str = "P") -> Entry:
    return Entry("moex", "SBER", side, qty, price, 1, fee=fee, planned_stop=stop, plan_uid=uid)


def test_review_flags_worse_entry_more_risk_and_computes_r() -> None:
    plan = make_plan(uid="P", id=1)  # вход 100, стоп 90, 10 шт., бюджет риска 100
    r = review(plan, [e("buy", 10, 101, 90, fee=1)])
    dev = {d["code"]: d for d in r["deviations"]}
    assert dev["entry"]["diff_pct"] == 1.0 and dev["entry"]["worse"]
    assert r["actual_risk_at_stop"] == 110 and r["risk_exceeded"] is True and r["status"] == "entered"
    closed = review(plan, [e("buy", 10, 101, 90, fee=1), e("sell", 10, 121, fee=1)])
    assert closed["status"] == "closed" and closed["fees"] == 2
    assert closed["result_pct"] == pytest.approx(19.802, abs=1e-3)
    assert closed["r_multiple"] == pytest.approx(1.82, abs=0.01)  # (121-101)/11


def test_review_partial_fill_missing_stop_and_foreign_entries() -> None:
    plan = make_plan(uid="P", id=1)
    r = review(plan, [e("buy", 5, 100), e("buy", 3, 102, 92), e("buy", 100, 50, uid="OTHER")])  # чужая запись не считается
    dev = {d["code"]: d for d in r["deviations"]}
    assert r["bought_qty"] == 8 and r["avg_entry"] == 100.75
    assert dev["qty"]["diff_pct"] == -20.0 and dev["qty"]["worse"] is False
    assert dev["stop"]["actual"] == 92 and dev["stop"]["worse"] is False  # стоп ближе плана — риск меньше
    assert r["risk_exceeded"] is False
    nostop = review(plan, [e("buy", 10, 100)])
    assert {d["code"]: d for d in nostop["deviations"]}["stop"]["actual"] is None
    assert {d["code"]: d for d in nostop["deviations"]}["stop"]["worse"] is True
    assert review(plan, [])["status"] == "open"
    deleted = Entry("moex", "SBER", "buy", 10, 100, 1, plan_uid="P", deleted_at=5)
    assert review(plan, [deleted])["status"] == "open"  # удалённая запись не считается фактом


# --- резервная копия с планами ---


def test_backup_carries_plans_and_links_and_rejects_dangling_links() -> None:
    src_conn, dst_conn = connect(":memory:"), connect(":memory:")
    src_plans, src_j = PlanStore(src_conn), Journal(src_conn)
    dst_plans, dst_j = PlanStore(dst_conn), Journal(dst_conn)
    plan = src_plans.add(make_plan())
    src_j.add(Entry("moex", "SBER", "buy", 10, 100.0, 1, plan_uid=plan.uid))
    snap = src_j.backup(src_plans)
    assert len(snap["plans"]) == 1 and snap["entries"][0]["plan_uid"] == plan.uid
    assert dst_j.restore_backup(snap, dst_plans) == {"added": 1, "skipped": 0, "plans_added": 1, "plans_skipped": 0}
    assert dst_j.restore_backup(snap, dst_plans) == {"added": 0, "skipped": 1, "plans_added": 0, "plans_skipped": 1}
    assert dst_plans.get_by_uid(plan.uid).reason == "пробой"
    assert dst_j.list()[0].plan_uid == plan.uid

    third_conn = connect(":memory:")
    third_plans, third_j = PlanStore(third_conn), Journal(third_conn)
    dangling = {**snap, "plans": []}  # запись ссылается на план, которого в копии нет
    with pytest.raises(ValueError, match="План"):
        third_j.restore_backup(dangling, third_plans)
    assert third_j.list(mode=None) == []
    broken_plan = {**snap, "plans": [{**snap["plans"][0], "stop": 500.0}]}  # стоп выше входа
    with pytest.raises(ValueError):
        third_j.restore_backup(broken_plan, third_plans)
    assert third_plans.list() == [] and third_j.list(mode=None) == []


def test_backup_version_1_without_plans_is_still_readable() -> None:
    j = Journal(connect(":memory:"))
    rec = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 1, "price": 10.0, "ts": 1, "fee": 0.0,
           "note": "", "signal_id": None, "planned_stop": None, "reason": "", "mode": "real", "uid": "u1",
           "deleted_at": None}
    old = {"format": "compass-journal", "version": 1, "exported_at": 1, "entries": [rec]}
    assert j.restore_backup(old)["added"] == 1


# --- бэктест со стопом, целью и размером по риску через API ---


def _wavy() -> list[float]:
    import math

    return [100 + 12 * math.sin(i / 6) + i * 0.05 for i in range(300)]


def test_backtest_api_with_rules_reports_rules_assumptions_and_exit_reasons(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles(_wavy())
    env.now[0] = 10_000 * DAY
    c = client(env)
    base = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "params": {"fast": 5, "slow": 20}, "limit": 300}
    plain = c.post("/api/backtest", json=base).json()
    assert plain["run_card"]["rules"] is None and plain["run_card"]["engine_version"] == ENGINE_VERSION
    assert any("стоп-лоссов нет" in a for a in plain["run_card"]["assumptions"])

    r = c.post("/api/backtest", json={**base, "stop_atr_mult": 2.0, "target_r": 3.0, "risk_pct": 1.0})
    assert r.status_code == 200
    body = r.json()
    card = body["run_card"]
    assert card["rules"]["stop_atr_mult"] == 2.0 and card["rules"]["lot"] == 10  # лот берётся у инструмента
    assert any("стоп на 2·ATR" in a for a in card["assumptions"]) and any("гэп" in a for a in card["assumptions"])
    assert not any("стоп-лоссов нет" in a for a in card["assumptions"])
    assert {"stops", "targets", "gap_exits", "ambiguous_bars", "skipped_entries"} <= set(body["metrics"])
    reasons = {t["exit_reason"] for t in body["trades"]}
    assert reasons <= {"signal", "stop", "gap_stop", "target", "gap_target", "open"} and reasons - {"signal", "open"}
    assert all(t["qty"] % 10 == 0 for t in body["trades"] if t["qty"])  # лот 10 из инструмента
    risks = [t["risk_amount"] for t in body["trades"] if t["risk_amount"]]
    top = max(x["v"] for x in body["equity"])
    assert risks and max(risks) <= 0.01 * top * 1.0001  # риск на сделку — не больше 1% капитала на тот момент


def test_backtest_api_rejects_inconsistent_rules(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles(_wavy())
    env.now[0] = 10_000 * DAY
    c = client(env)
    base = {"market": "moex", "symbol": "SBER", "strategy": "sma_cross", "limit": 300}
    assert c.post("/api/backtest", json={**base, "risk_pct": 1.0}).status_code == 422  # риск без стопа
    assert c.post("/api/backtest", json={**base, "target_r": 2.0}).status_code == 422  # цель без стопа
    assert c.post("/api/backtest", json={**base, "stop_atr_mult": 0}).status_code == 422
    assert c.post("/api/backtest", json={**base, "stop_atr_mult": 2.0, "risk_pct": 150}).status_code == 422
