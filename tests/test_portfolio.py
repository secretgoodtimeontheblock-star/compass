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


# --- Portfolio Truth: оценка по рыночным ценам, свежесть, сверка ---

from compass.portfolio import Mark  # noqa: E402


def mk(price, fetched_s=None, stale=False, max_age=3600) -> Mark:
    return Mark(price, NOW - HOUR, NOW // 1000 - 60 if fetched_s is None else fetched_s, stale, "test", max_age)


def test_marks_turn_unrealized_into_equity_exposure_and_free_stays_cash() -> None:
    entries = [e("buy", 100, 100, NOW - DAY, stop=90)]  # стоимость 10 000
    s = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(120)})
    p = s["positions"][0]
    assert p["mark_price"] == 120 and p["market_value"] == 12_000 and p["unrealized_pnl"] == 2_000
    assert p["unrealized_pct"] == 20 and p["mark_status"] == "fresh"
    assert s["unrealized_total"] == 2_000 and s["equity"] == 102_000  # капитал + нереализованное
    assert s["exposure"] == 12_000 and s["exposure_cost"] == 10_000  # экспозиция по рынку, не по входу
    assert s["free"] == 90_000  # свободные деньги от цен не зависят
    assert s["truth"]["status"] == "ok" and s["equity_complete"] is True


def test_open_risk_is_measured_from_current_price_not_entry() -> None:
    entries = [e("buy", 100, 100, NOW - DAY, stop=90)]
    up = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(120)})["positions"][0]
    assert up["risk_at_stop"] == 1_000 and up["risk"] == 3_000 and up["risk_basis"] == "mark"  # от 120 до стопа 90
    s = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(120)})
    assert s["open_risk"] == 3_000 and s["heat_pct"] == 3.0
    no_mark = portfolio.snapshot(acc(), entries, NOW)
    assert no_mark["open_risk"] == 1_000 and no_mark["positions"][0]["risk_basis"] == "cost"


def test_missing_mark_values_position_at_cost_and_says_so() -> None:
    entries = [e("buy", 100, 100, NOW - DAY, stop=90)]
    s = portfolio.snapshot(acc(), entries, NOW)
    p = s["positions"][0]
    assert p["mark_status"] == "missing" and p["unrealized_pnl"] is None
    assert s["exposure"] == 10_000 and s["equity"] == 100_000 and s["truth"]["status"] == "unmarked"
    assert s["equity_complete"] is False
    assert any("Нет рыночной цены" in w for w in s["warnings"])


def test_stale_mark_is_flagged_not_hidden() -> None:
    entries = [e("buy", 100, 100, NOW - DAY)]
    cached = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(95, stale=True)})
    assert cached["positions"][0]["mark_status"] == "stale" and cached["truth"]["status"] == "partial"
    assert cached["positions"][0]["unrealized_pnl"] == -500  # цена используется, но помечена
    old = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(95, fetched_s=NOW // 1000 - 7200)})
    assert old["positions"][0]["mark_status"] == "stale" and old["positions"][0]["mark_age_s"] == 7200
    assert any("устарели" in w for w in old["warnings"])
    assert old["equity_complete"] is False


def test_price_at_or_below_recorded_stop_is_a_warning_with_zero_remaining_risk() -> None:
    entries = [e("buy", 100, 100, NOW - DAY, stop=90)]
    s = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(85)})
    p = s["positions"][0]
    assert p["stop_breached"] is True and p["risk"] == 0 and p["unrealized_pnl"] == -1_500
    assert any("не выше записанного стопа" in w for w in s["warnings"])


def test_concentration_warning_only_with_several_positions() -> None:
    entries = [e("buy", 100, 100, NOW - DAY), e("buy", 10, 100, NOW - DAY, symbol="GAZP")]
    s = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(100), "GAZP": mk(100)})
    assert next(x for x in s["positions"] if x["symbol"] == "SBER")["weight_pct"] == pytest.approx(90.9, abs=0.1)
    assert any("Концентрация" in w for w in s["warnings"])
    one = portfolio.snapshot(acc(), entries[:1], NOW, {"SBER": mk(100)})
    assert not any("Концентрация" in w for w in one["warnings"])


def test_daily_pnl_stays_realized_only_even_with_marks() -> None:
    entries = [e("buy", 100, 100, NOW - DAY), e("sell", 50, 110, NOW - HOUR)]
    s = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(200)})
    assert s["daily_pnl"] == 500 and s["unrealized_total"] == 5_000


def test_reconcile_reports_every_difference_and_merges_nothing() -> None:
    entries = [e("buy", 100, 100, NOW - DAY), e("buy", 10, 50, NOW - DAY, symbol="GAZP")]
    pos = portfolio.snapshot(acc(), entries, NOW)["positions"]
    ok = portfolio.reconcile(pos, [{"symbol": "SBER", "qty": 100, "avg_price": 100.1}, {"symbol": "GAZP", "qty": 10}])
    assert ok["status"] == "match" and ok["differences"] == []
    bad = portfolio.reconcile(
        pos, [{"symbol": "SBER", "qty": 90}, {"symbol": "LKOH", "qty": 3}, {"symbol": "GAZP", "qty": 10, "avg_price": 70}]
    )
    kinds = {d["symbol"]: d["kind"] for d in bad["differences"]}
    assert kinds == {"SBER": "qty_mismatch", "LKOH": "missing_in_journal", "GAZP": "price_mismatch"}
    gone = portfolio.reconcile(pos, [{"symbol": "SBER", "qty": 100}])
    assert {d["kind"] for d in gone["differences"]} == {"missing_at_broker"}
    assert {p["symbol"]: p["qty"] for p in pos} == {"SBER": 100, "GAZP": 10}  # журнал не изменён


def test_snapshot_exposes_provenance_of_each_source() -> None:
    entries = [e("buy", 100, 100, NOW - DAY)]
    t = portfolio.snapshot(acc(), entries, NOW, {"SBER": mk(100)})["truth"]
    assert t["sources"]["journal"]["entries"] == 1 and t["sources"]["market"]["marked"] == 1
    assert t["sources"]["broker"] == {"connected": False} and t["reconciliation"]["status"] == "not_connected"
    with_broker = portfolio.snapshot(acc(), entries, NOW, None, [{"symbol": "SBER", "qty": 99}])["truth"]
    assert with_broker["reconciliation"]["status"] == "mismatch"


def test_day_api_marks_positions_from_cached_candles_and_reconcile_endpoint(env: Env) -> None:
    env.adapter.data["SBER"] = day_candles([100, 101, 105])
    env.services.settings.update({"tf_moex": "1d"})
    env.services.accounts.update("moex", {"capital": 100_000})
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    client.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100,
                                      "ts": env.now[0] - DAY, "planned_stop": 95})
    acc_ = next(a for a in client.get("/api/day").json()["accounts"] if a["market"] == "moex")
    p = acc_["positions"][0]
    assert p["mark_price"] == 105 and p["unrealized_pnl"] == 50 and p["mark_status"] == "fresh"
    assert acc_["equity"] == 100_050 and acc_["truth"]["sources"]["market"]["marked"] == 1
    r = client.post("/api/reconcile", json={"market": "moex", "positions": [{"symbol": "SBER", "qty": 9}]}).json()
    assert r["status"] == "mismatch" and r["differences"][0]["kind"] == "qty_mismatch"
    assert client.post("/api/reconcile", json={"market": "moex", "positions": [{"symbol": "SBER"}]}).status_code == 422
    assert client.post("/api/reconcile", json={"market": "nope", "positions": []}).status_code == 404


def test_day_api_survives_unreachable_source_without_cache(env: Env) -> None:
    from compass.markets import MarketError

    env.adapter.data["SBER"] = MarketError("down")
    env.services.accounts.update("moex", {"capital": 100_000})
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    client.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100,
                                      "ts": env.now[0] - DAY})
    a = next(x for x in client.get("/api/day").json()["accounts"] if x["market"] == "moex")
    assert a["positions"][0]["mark_status"] == "missing" and a["truth"]["status"] == "unmarked"
