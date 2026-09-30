"""Счета, совокупный риск, дневной лимит и корреляции. Числа посчитаны вручную."""

from __future__ import annotations

import math
import sqlite3

import numpy as np
import pytest
from fastapi.testclient import TestClient

from compass import db, portfolio
from compass.accounts import Account, AccountStore
from compass.api.app import create_app
from compass.db import connect
from compass.journal import Entry
from tests.conftest import DAY, Env, day_candles

HOUR = 3_600_000
# полночь по Москве 2026-09-29 = 2026-09-28 21:00 UTC
MSK_MIDNIGHT = 1_790_542_800_000
NOW = MSK_MIDNIGHT + 15 * HOUR  # 15:00 МСК того же дня


def acc(**kw) -> Account:
    base = {"market": "moex", "name": "Рубли", "currency": "RUB", "capital": 100_000.0}
    return Account(**{**base, **kw})


def e(side, qty, price, ts, symbol="SBER", fee=0.0, stop=None, mode="real") -> Entry:
    return Entry("moex", symbol, side, qty, price, ts, fee=fee, planned_stop=stop, mode=mode)


# --- состояние счёта ---


def test_snapshot_equity_free_exposure_and_open_risk() -> None:
    entries = [
        e("buy", 100, 100, NOW - 3 * DAY, stop=90),  # стоимость 10 000, риск 1 000
        e("buy", 50, 200, NOW - 2 * DAY, symbol="GAZP", stop=180, fee=10),  # 10 010, риск 50·(200.2−180)=1 010
        e("sell", 40, 110, NOW - DAY),  # продажа 40 SBER: +400 (средняя 100)
    ]
    s = portfolio.snapshot(acc(), entries, NOW)
    assert s["realized_total"] == 400 and s["equity"] == 100_400
    assert s["exposure"] == pytest.approx(6000 + 10_010)  # 60 SBER по 100 + GAZP
    assert s["free"] == pytest.approx(100_400 - 16_010)
    sber = next(p for p in s["positions"] if p["symbol"] == "SBER")
    assert sber["qty"] == 60 and sber["risk_at_stop"] == 600  # 60·(100−90)
    gazp = next(p for p in s["positions"] if p["symbol"] == "GAZP")
    assert gazp["risk_at_stop"] == pytest.approx(50 * (200.2 - 180))
    assert s["open_risk"] == pytest.approx(600 + 1010)
    assert s["heat_pct"] == pytest.approx(1.61, abs=0.01) and s["unprotected"] == []


def test_paper_and_historical_trades_can_be_excluded_by_caller_only_real_counts() -> None:
    from compass.db import connect as c  # noqa: F401  — журнал фильтрует режим до вызова портфеля

    real_only = [x for x in [e("buy", 10, 10, NOW - DAY), e("buy", 999, 10, NOW - DAY, mode="paper")] if x.mode == "real"]
    assert portfolio.snapshot(acc(), real_only, NOW)["exposure"] == 100


def test_daily_pnl_uses_the_market_day_and_flags_limit() -> None:
    entries = [
        e("buy", 100, 100, NOW - 5 * DAY),
        e("sell", 30, 90, MSK_MIDNIGHT - 1),  # вчера 23:59:59.999 МСК — в сегодняшний день не входит: −300
        e("sell", 40, 92, MSK_MIDNIGHT + 1),  # сегодня 00:00 МСК: −320
        e("sell", 10, 94, NOW - HOUR, fee=5),  # сегодня: −60 −5 = −65
    ]
    s = portfolio.snapshot(acc(), entries, NOW)
    assert s["daily_pnl"] == pytest.approx(-385)
    assert s["daily_limit"] == 3000 and s["daily_limit_breached"] is False
    tight = portfolio.snapshot(acc(daily_loss_limit_pct=0.3), entries, NOW)  # лимит 300
    assert tight["daily_limit_breached"] is True and any("Дневной лимит" in w for w in tight["warnings"])
    # для крипты день — по UTC: продажа за миллисекунду до полуночи UTC — вчера, через час после — сегодня
    utc_midnight = MSK_MIDNIGHT + 3 * HOUR
    a = Account("crypto", "USDT", "USDT", 1000.0)
    buy = Entry("crypto", "BTC/USDT", "buy", 3, 100, utc_midnight - 5 * DAY)
    yesterday = Entry("crypto", "BTC/USDT", "sell", 1, 70, utc_midnight - 1)  # −30
    today = Entry("crypto", "BTC/USDT", "sell", 1, 90, utc_midnight + HOUR)  # −10
    s2 = portfolio.snapshot(a, [buy, yesterday, today], utc_midnight + 20 * HOUR)
    assert s2["daily_pnl"] == pytest.approx(-10) and s2["realized_total"] == pytest.approx(-40)


def test_heat_limit_unprotected_positions_and_missing_capital_warn() -> None:
    entries = [e("buy", 100, 100, NOW - DAY, stop=90), e("buy", 10, 50, NOW - DAY, symbol="LKOH")]  # у LKOH стопа нет
    s = portfolio.snapshot(acc(max_open_risk_pct=0.5), entries, NOW)  # риск 1000 = 1% > 0.5%
    text = " ".join(s["warnings"])
    assert "выше вашего предела" in text and "Нет записанного стопа: LKOH" in text
    lkoh = next(p for p in s["positions"] if p["symbol"] == "LKOH")
    assert lkoh["risk_at_stop"] is None
    none = portfolio.snapshot(acc(capital=None), entries, NOW)
    assert none["equity"] is None and none["free"] is None and any("Капитал счёта не задан" in w for w in none["warnings"])


def test_stop_above_average_means_zero_risk_and_closed_position_leaves_no_stop() -> None:
    s = portfolio.snapshot(acc(), [e("buy", 10, 100, NOW - DAY, stop=105)], NOW)
    assert s["positions"][0]["risk_at_stop"] == 0
    closed = portfolio.snapshot(acc(), [e("buy", 10, 100, NOW - 2 * DAY, stop=90), e("sell", 10, 100, NOW - DAY),
                                        e("buy", 5, 100, NOW - HOUR)], NOW)
    assert closed["unprotected"] == ["SBER"]  # старый стоп закрытой позиции к новой не относится


def test_assess_new_position_warns_about_heat_free_funds_and_daily_limit() -> None:
    a = acc(max_open_risk_pct=2.0)
    snap = portfolio.snapshot(a, [e("buy", 100, 100, NOW - DAY, stop=90)], NOW)  # риск 1 000 = 1%
    ok = portfolio.assess_new_position(snap, a, risk_amount=500, cost=5000)
    assert ok["warnings"] == [] and ok["heat_after_pct"] == 1.5
    bad = portfolio.assess_new_position(snap, a, risk_amount=1500, cost=95_000)
    text = " ".join(bad["warnings"])
    assert bad["heat_after_pct"] == 2.5 and "станет 2.5% капитала" in text and "больше свободных средств" in text


def test_correlation_detects_moving_together_but_not_independent_or_short_history() -> None:
    rng = np.random.default_rng(3)
    base = np.cumsum(rng.normal(0, 1, 120)) + 200
    ts = [i * DAY for i in range(120)]
    cand = dict(zip(ts, base, strict=True))
    twin = dict(zip(ts, base * 1.5 + rng.normal(0, 0.2, 120), strict=True))
    mirror = dict(zip(ts, -base + 500 + rng.normal(0, 0.2, 120), strict=True))
    indep = dict(zip(ts, np.cumsum(rng.normal(0, 1, 120)) + 200, strict=True))
    short = dict(list(twin.items())[:30])
    found = portfolio.correlated_positions(cand, {"TWIN": twin, "MIRROR": mirror, "INDEP": indep, "SHORT": short})
    by = {x["symbol"]: x["correlation"] for x in found}
    assert by["TWIN"] > 0.9 and by["MIRROR"] < -0.9
    assert "INDEP" not in by and "SHORT" not in by  # независимая и слишком короткая история не порождают вывода


def test_realized_events_average_cost_and_fees() -> None:
    ev = portfolio.realized_events([e("buy", 10, 100, 1, fee=2), e("sell", 4, 120, 2, fee=1)])
    # средняя (1000+2)/10 = 100.2; продажа 4·(120−100.2) − 1 = 78.2
    assert ev == [(2, "SBER", pytest.approx(78.2))]


# --- хранилище счетов и миграция ---


def test_account_store_validates_updates() -> None:
    st = AccountStore(connect(":memory:"))
    assert {a.market: a.currency for a in st.list()} == {"moex": "RUB", "crypto": "USDT"}
    assert st.get("crypto").capital is None  # капитал USDT не придумывается
    assert st.update("moex", {"capital": 250_000, "risk_pct": 0.5, "name": " Основной "}).name == "Основной"
    assert st.get("moex").capital == 250_000 and st.get("moex").risk_pct == 0.5
    assert st.update("crypto", {"capital": None}).capital is None
    for bad in ({"capital": 0}, {"capital": -5}, {"capital": True}, {"capital": float("nan")}, {"risk_pct": 0},
                {"risk_pct": 101}, {"daily_loss_limit_pct": -1}, {"max_open_risk_pct": "x"}, {"currency": "EUR"},
                {"name": ""}):
        with pytest.raises(ValueError):
            st.update("moex", bad)
    with pytest.raises(KeyError):
        st.update("nope", {})
    assert st.get("moex").capital == 250_000  # неудачные правки ничего не изменили


def test_v10_upgrade_creates_accounts_inheriting_settings_without_inventing_usdt(tmp_path) -> None:
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    scripts = db._V1 + db._V2 + db._V3 + db._V4 + db._V5 + db._V6 + db._V7 + db._V8 + db._V9 + db._V10
    old.executescript(scripts + "PRAGMA user_version=10;")
    old.executemany("INSERT INTO settings VALUES (?, ?)", [("capital", "250000.0"), ("risk_pct", "0.5")])
    old.commit()
    old.close()
    conn = db.connect(path)
    try:
        rows = conn.execute("SELECT market, currency, capital, risk_pct FROM accounts ORDER BY market").fetchall()
        assert rows == [("crypto", "USDT", None, 0.5), ("moex", "RUB", 250_000.0, 0.5)]
    finally:
        conn.close()
    assert sqlite3.connect(str(path) + ".v10.bak").execute("PRAGMA user_version").fetchone()[0] == 10


def test_v10_upgrade_without_settings_uses_defaults_and_survives_garbage(tmp_path) -> None:
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    scripts = db._V1 + db._V2 + db._V3 + db._V4 + db._V5 + db._V6 + db._V7 + db._V8 + db._V9 + db._V10
    old.executescript(scripts + "PRAGMA user_version=10;")
    old.executemany("INSERT INTO settings VALUES (?, ?)", [("capital", "0"), ("risk_pct", "\"junk\"")])
    old.commit()
    old.close()
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT capital, risk_pct FROM accounts WHERE market='moex'").fetchone() == (100_000.0, 1.0)
    finally:
        conn.close()


# --- API ---


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


def test_risk_uses_the_markets_own_account_not_a_shared_capital(env: Env) -> None:
    c = client(env)
    c.put("/api/accounts/moex", json={"capital": 500_000, "risk_pct": 1})
    r = c.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190}).json()
    assert r["capital"] == 500_000 and r["account"]["currency"] == "RUB" and r["budget"] == 5000
    # общий «капитал» из настроек больше не подменяет счёт
    c.put("/api/settings", json={"capital": 1_000})
    assert c.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190}).json()["capital"] == 500_000


def test_unset_usdt_capital_is_an_error_not_the_ruble_number(env: Env) -> None:
    class Crypto:
        id, name, timeframes = "crypto", "c", ("1d",)

        def fetch_candles(self, *a):
            return []

        def search(self, q):
            return []

    env.services.adapters["crypto"] = Crypto()
    c = client(env)
    r = c.post("/api/risk", json={"market": "crypto", "symbol": "BTC/USDT", "entry": 100, "stop": 90})
    assert r.status_code == 422 and "USDT" in r.json()["detail"] and "не задан" in r.json()["detail"]
    ok = c.post("/api/risk", json={"market": "crypto", "symbol": "BTC/USDT", "entry": 100, "stop": 90, "capital": 1000})
    assert ok.status_code == 200 and ok.json()["capital"] == 1000


def test_risk_uses_free_funds_and_reports_portfolio_effects(env: Env) -> None:
    c = client(env)
    c.put("/api/accounts/moex", json={"capital": 100_000, "risk_pct": 5, "max_open_risk_pct": 1.5})
    buy = {"market": "moex", "symbol": "GAZP", "side": "buy", "qty": 300, "price": 200, "ts": 1_700_000_000_000,
           "planned_stop": 190}  # стоимость 60 000, риск 3 000 = 3%
    assert c.post("/api/journal", json=buy).status_code == 201
    r = c.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190}).json()
    assert r["portfolio"]["heat_before_pct"] == 3.0 and r["portfolio"]["heat_after_pct"] > 3.0
    assert any("станет" in w for w in r["warnings"])
    assert r["cost"] <= 40_000 + 1  # свободно 100 000 − 60 000: больше на счёте купить нельзя
    assert r["capped"] is True


def test_plan_snapshot_keeps_portfolio_warnings_and_account_capital(env: Env) -> None:
    c = client(env)
    c.put("/api/accounts/moex", json={"capital": 100_000, "max_open_risk_pct": 0.5})
    c.post("/api/journal", json={"market": "moex", "symbol": "GAZP", "side": "buy", "qty": 100, "price": 200,
                                 "ts": 1_700_000_000_000, "planned_stop": 190})
    p = c.post("/api/plans", json={"market": "moex", "symbol": "SBER", "entry": 200, "stop": 190,
                                   "reason": "тест"}).json()
    assert p["capital"] == 100_000 and any("станет" in w for w in p["warnings"])
    c.put("/api/accounts/moex", json={"capital": 1_000_000, "max_open_risk_pct": 50})
    again = c.get(f"/api/plans/{p['id']}").json()
    assert again["capital"] == 100_000 and any("станет" in w for w in again["warnings"])  # план не переписывается


def test_day_panel_is_per_account_and_only_real_trades(env: Env) -> None:
    c = client(env)
    body = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100, "ts": 1_700_000_000_000,
            "planned_stop": 95}
    c.post("/api/journal", json=body)
    c.post("/api/journal", json={**body, "mode": "paper", "qty": 5000, "symbol": "GAZP"})
    day = c.get("/api/day").json()
    by = {a["market"]: a for a in day["accounts"]}
    assert set(by) == {"moex", "crypto"} and by["moex"]["currency"] == "RUB" and by["crypto"]["currency"] == "USDT"
    assert by["moex"]["exposure"] == 1000 and [p["symbol"] for p in by["moex"]["positions"]] == ["SBER"]
    assert by["crypto"]["capital"] is None and any("не задан" in w for w in by["crypto"]["warnings"])
    assert "unseen_signals" in day and "equity" not in day  # общего итога рублей и USDT нет


def test_accounts_api_validation(env: Env) -> None:
    c = client(env)
    assert {a["market"] for a in c.get("/api/accounts").json()} == {"moex", "crypto"}
    assert c.put("/api/accounts/moex", json={"capital": -1}).status_code == 422
    assert c.put("/api/accounts/moex", json={"currency": "EUR"}).status_code == 422
    assert c.put("/api/accounts/nope", json={"capital": 1}).status_code == 404


def test_correlated_open_position_warns_in_risk(env: Env) -> None:
    rng = np.random.default_rng(5)
    base = list(np.cumsum(rng.normal(0, 1, 150)) + 300)
    env.adapter.data["GAZP"] = day_candles(base)
    env.adapter.data["SBER"] = day_candles([v * 1.2 + float(rng.normal(0, 0.1)) for v in base])
    env.now[0] = 10_000 * DAY
    c = client(env)
    c.post("/api/journal", json={"market": "moex", "symbol": "GAZP", "side": "buy", "qty": 10, "price": 300,
                                 "ts": 1_700_000_000_000, "planned_stop": 280})
    r = c.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 300, "stop": 285}).json()
    assert r["portfolio"]["correlated"] and r["portfolio"]["correlated"][0]["symbol"] == "GAZP"
    assert any("сильно совпадали" in w for w in r["warnings"])
    env.adapter.data["GAZP"] = day_candles(list(np.cumsum(rng.normal(0, 1, 150)) + 300))  # независимая бумага
    from compass.cache import CandleCache  # noqa: F401

    env.services.cache._conn.execute("DELETE FROM candles")
    env.services.cache._conn.execute("DELETE FROM fetch_log")
    r2 = c.post("/api/risk", json={"market": "moex", "symbol": "SBER", "entry": 300, "stop": 285}).json()
    assert r2["portfolio"]["correlated"] == []


def test_guard_still_protects_new_endpoints(env: Env) -> None:
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert c.put("/api/accounts/moex", json={"capital": 1}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.get("/api/day", headers={"Host": "evil.example"}).status_code == 403
    assert math.isfinite(1.0)


def test_position_stop_is_the_latest_recorded_one() -> None:
    entries = [e("buy", 10, 100, NOW - 2 * DAY, stop=90), e("buy", 10, 100, NOW - DAY, stop=95)]
    (p,) = portfolio.snapshot(acc(), entries, NOW)["positions"]
    assert p["stop"] == 95 and p["risk_at_stop"] == 100  # 20 шт. · (100 − 95): стоп подтянули
    later_without = entries + [e("buy", 5, 100, NOW - HOUR)]  # докупка без стопа не отменяет записанный
    assert portfolio.snapshot(acc(), later_without, NOW)["positions"][0]["stop"] == 95
