"""Рабочее место: уровни, активные планы для графика, лимит сделок в день."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from compass import db, portfolio
from compass.accounts import Account, AccountStore
from compass.api.app import create_app
from compass.db import connect
from compass.journal import Entry
from compass.levels import MAX_PER_INSTRUMENT, Level, LevelStore
from tests.conftest import Env

HOUR = 3_600_000
MSK_MIDNIGHT = 1_790_542_800_000  # 2026-09-29 00:00 МСК
NOW = MSK_MIDNIGHT + 15 * HOUR


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


# --- уровни ---


def test_level_store_add_list_soft_delete_restore() -> None:
    st = LevelStore(connect(":memory:"))
    a = st.add(Level("moex", "SBER", 270.5, " вчерашний максимум "))
    b = st.add(Level("moex", "SBER", 250.0))
    st.add(Level("moex", "GAZP", 100.0))
    assert [x.price for x in st.list("moex", "SBER")] == [270.5, 250.0]  # сверху вниз
    assert a.label == "вчерашний максимум" and a.uid and a.created_at
    assert st.remove(a.id) is True and st.remove(a.id) is False
    assert [x.id for x in st.list("moex", "SBER")] == [b.id]
    assert [x.id for x in st.list("moex", "SBER", deleted=True)] == [a.id]
    assert st.restore(a.id) is True and st.restore(a.id) is False
    assert len(st.list("moex", "SBER")) == 2 and len(st.list("moex", "GAZP")) == 1


@pytest.mark.parametrize("bad", [Level("moex", "S", 0), Level("moex", "S", -1), Level("moex", "S", float("nan")),
                                 Level("moex", "S", float("inf")), Level("moex", "S", 1, "x" * 61)])
def test_level_rejects_nonsense(bad) -> None:
    with pytest.raises(ValueError):
        LevelStore(connect(":memory:")).add(bad)


def test_level_limit_per_instrument() -> None:
    st = LevelStore(connect(":memory:"))
    for i in range(MAX_PER_INSTRUMENT):
        st.add(Level("moex", "SBER", 1 + i))
    with pytest.raises(ValueError, match="не больше"):
        st.add(Level("moex", "SBER", 999))
    st.add(Level("moex", "GAZP", 5))  # на другом инструменте лимит свой
    st.remove(st.list("moex", "SBER")[0].id)  # освободили место — снова можно
    st.add(Level("moex", "SBER", 1000))


def test_levels_api(env: Env) -> None:
    c = client(env)
    r = c.post("/api/levels", json={"market": "moex", "symbol": "SBER", "price": 270.5, "label": "поддержка"})
    assert r.status_code == 201 and r.json()["label"] == "поддержка"
    lid = r.json()["id"]
    assert c.get("/api/levels", params={"market": "moex", "symbol": "SBER"}).json()[0]["price"] == 270.5
    assert c.post("/api/levels", json={"market": "moex", "symbol": "SBER", "price": 0}).status_code == 422
    assert c.post("/api/levels", json={"market": "nope", "symbol": "X", "price": 1}).status_code == 404
    assert c.delete(f"/api/levels/{lid}").status_code == 204 and c.delete(f"/api/levels/{lid}").status_code == 404
    assert c.get("/api/levels", params={"market": "moex", "symbol": "SBER"}).json() == []
    assert c.get("/api/levels", params={"market": "moex", "symbol": "SBER", "deleted": True}).json()[0]["id"] == lid
    assert c.post(f"/api/levels/{lid}/restore").status_code == 200
    assert c.post(f"/api/levels/{lid}/restore").status_code == 404
    assert c.post("/api/levels", json={"market": "moex", "symbol": "SBER", "price": 1},
                  headers={"Origin": "http://evil.example"}).status_code == 403


# --- активные планы для графика ---


def test_active_plans_hide_closed_ones(env: Env) -> None:
    c = client(env)
    p1 = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190, "target": 230,
                                    "reason": "открытый"}).json()
    p2 = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 210, "stop": 200,
                                    "reason": "закрытый"}).json()
    c.post("/api/plans", json={"market": "moex", "symbol": "GAZP", "entry": 100, "stop": 90, "reason": "другой"})
    ts = 1_700_000_000_000
    for side, qty, price, off in (("buy", 10, 210, 0), ("sell", 10, 220, 1)):
        c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": side, "qty": qty, "price": price,
                                     "ts": ts + off, "plan_uid": p2["uid"]})
    act = c.get("/api/plans-active", params={"market": "moex", "symbol": "SBER"}).json()
    assert [(x["id"], x["status"]) for x in act] == [(p1["id"], "open")]
    assert act[0]["entry"] == 200 and act[0]["stop"] == 190 and act[0]["target"] == 230
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 5, "price": 201,
                                 "ts": ts + 5, "plan_uid": p1["uid"]})
    assert c.get("/api/plans-active", params={"market": "moex", "symbol": "SBER"}).json()[0]["status"] == "entered"


# --- лимит сделок в день ---


def e(side, ts, symbol="SBER", qty=1):
    return Entry("moex", symbol, side, qty, 100, ts)


def test_entries_today_limit_counts_only_todays_buys_and_warns_at_limit() -> None:
    acc = Account("moex", "Рубли", "RUB", 100_000.0, max_trades_per_day=3)
    entries = [e("buy", MSK_MIDNIGHT - 1), e("sell", NOW - 3 * HOUR), e("buy", NOW - 2 * HOUR), e("buy", NOW - HOUR, "GAZP")]
    s = portfolio.snapshot(acc, entries, NOW)
    assert s["entries_today"] == 2 and s["trades_limit_reached"] is False  # вчерашняя покупка и продажа не в счёт
    s = portfolio.snapshot(acc, [*entries, e("buy", NOW - 1, "LKOH")], NOW)
    assert s["entries_today"] == 3 and s["trades_limit_reached"] is True
    assert any("входов при вашем лимите 3" in w for w in s["warnings"])
    assert any("Лимит входов" in w for w in portfolio.assess_new_position(s, acc, 10, 1000)["warnings"])
    off = portfolio.snapshot(Account("moex", "Р", "RUB", 100_000.0), entries, NOW)
    assert off["trades_limit_reached"] is False and off["max_trades_per_day"] is None


def test_account_max_trades_validation(env: Env) -> None:
    st = AccountStore(connect(":memory:"))
    assert st.update("moex", {"max_trades_per_day": 20}).max_trades_per_day == 20
    assert st.update("moex", {"max_trades_per_day": None}).max_trades_per_day is None
    for bad in (0, -1, 1.5, True, "5", 10_001):
        with pytest.raises(ValueError):
            st.update("moex", {"max_trades_per_day": bad})
    c = client(env)
    assert c.put("/api/accounts/moex", json={"max_trades_per_day": 30}).json()["max_trades_per_day"] == 30
    assert c.put("/api/accounts/moex", json={"max_trades_per_day": 0}).status_code == 422


def test_v13_upgrade_adds_levels_and_trades_limit_keeping_accounts(tmp_path) -> None:
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("".join(getattr(db, f"_V{i}") for i in range(1, 14)) + "PRAGMA user_version=13;")
    old.execute("UPDATE accounts SET capital = 777 WHERE market = 'moex'")
    old.commit()
    old.close()
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT capital, max_trades_per_day FROM accounts WHERE market='moex'").fetchone() == (777.0, None)
        assert conn.execute("SELECT COUNT(*) FROM levels").fetchone() == (0,)
    finally:
        conn.close()
    assert sqlite3.connect(str(path) + ".v13.bak").execute("PRAGMA user_version").fetchone()[0] == 13


def test_experience_level_defaults_to_beginner_and_validates(env) -> None:
    s = env.services.settings
    assert s.get("experience_level") == "beginner"
    assert s.update({"experience_level": "researcher"})["experience_level"] == "researcher"
    import pytest

    for bad in ("pro", 1, None, ""):
        with pytest.raises(ValueError):
            s.update({"experience_level": bad})
