"""Недельный разбор: числа посчитаны вручную; каждый счёт отдельно."""

from __future__ import annotations

from fastapi.testclient import TestClient

from compass.accounts import Account
from compass.api.app import create_app
from compass.journal import Entry
from compass.plans import Plan
from compass.review_week import weekly
from tests.conftest import Env

HOUR = 3_600_000
DAY = 86_400_000
MSK_MIDNIGHT = 1_790_542_800_000  # 2026-09-29 00:00 МСК
NOW = MSK_MIDNIGHT + 15 * HOUR


def e(side, qty, price, ts, symbol="SBER", fee=0.0, stop=None, plan=None) -> Entry:
    return Entry("moex", symbol, side, qty, price, ts, fee=fee, planned_stop=stop, plan_uid=plan)


ACC = Account("moex", "Рубли", "RUB", 100_000.0, daily_loss_limit_pct=1.0, max_trades_per_day=2)


def test_weekly_numbers_by_hand() -> None:
    old = NOW - 20 * DAY
    entries = [
        e("buy", 100, 100, old),  # давняя покупка — в неделю не входит, но даёт среднюю цену
        e("sell", 40, 110, NOW - 6 * DAY, fee=4),  # +400 − 4 = +396
        e("sell", 30, 95, NOW - 2 * DAY - HOUR, fee=3),  # −150 − 3 = −153
        e("sell", 30, 80, NOW - HOUR),  # −600  (день с убытком выше лимита 1 000? нет, 600 < 1 000)
        e("buy", 10, 90, NOW - 3 * HOUR, symbol="GAZP", stop=85),
        e("buy", 10, 91, NOW - 2 * HOUR, symbol="LKOH"),  # без стопа
        e("buy", 10, 92, NOW - HOUR, symbol="YDEX", stop=88),
    ]
    w = weekly(ACC, entries, lambda uid: None, NOW)
    assert w["closed_trades"] == 3 and w["realized"] == 396 - 153 - 600 and w["wins"] == 1 and w["losses"] == 2
    assert w["win_rate_pct"] is None and any("меньше 10" in n for n in w["notes"])  # мало сделок — доли нет
    assert w["avg_win"] == 396 and w["avg_loss"] == -376.5
    assert w["best"] == {"symbol": "SBER", "pnl": 396} and w["worst"] == {"symbol": "SBER", "pnl": -600}
    assert w["entries"] == 3 and w["entries_without_stop"] == 1 and w["fees"] == 7
    assert w["busiest_day"]["entries"] == 3 and w["days_over_trades_limit"] == [w["busiest_day"]["date"]]  # лимит 2, было 3
    assert w["days_over_daily_limit"] == []


def test_days_over_daily_limit_and_window_edges() -> None:
    entries = [e("buy", 100, 100, NOW - 30 * DAY), e("sell", 100, 88, NOW - 2 * HOUR)]  # −1 200 при лимите 1 000
    w = weekly(ACC, entries, lambda uid: None, NOW)
    assert len(w["days_over_daily_limit"]) == 1
    edge = weekly(ACC, [e("buy", 10, 100, NOW - 8 * DAY), e("sell", 10, 90, NOW - 8 * DAY + HOUR)], lambda uid: None, NOW)
    assert edge["closed_trades"] == 0 and edge["realized"] == 0  # за пределами окна недели
    wide = weekly(ACC, [e("buy", 10, 100, NOW - 8 * DAY), e("sell", 10, 90, NOW - 8 * DAY + HOUR)], lambda uid: None, NOW, days=10)
    assert wide["closed_trades"] == 1 and wide["realized"] == -100


def test_win_rate_appears_with_ten_trades() -> None:
    entries = [e("buy", 100, 100, NOW - 6 * DAY)]
    for i in range(10):
        entries.append(e("sell", 1, 101 if i < 7 else 99, NOW - 5 * DAY + i * HOUR))
    w = weekly(ACC, entries, lambda uid: None, NOW)
    assert w["closed_trades"] == 10 and w["win_rate_pct"] == 70.0


def test_plan_deviations_are_counted() -> None:
    plan = Plan(market="moex", symbol="SBER", source="s", entry=100.0, stop=90.0, qty=10, lots=10, capital=100_000,
                risk_pct=1.0, fee_pct=0.05, slippage_pct=0.05, cost=1000.0, risk_amount=100.0, risk_amount_worse=110.0,
                budget=100.0, reason="р", uid="P1", id=1)
    entries = [e("buy", 10, 103, NOW - 2 * HOUR, stop=90, plan="P1"), e("buy", 1, 50, NOW - HOUR, symbol="GAZP")]
    w = weekly(ACC, entries, lambda uid: plan if uid == "P1" else None, NOW)
    d = w["plan_deviations"]
    assert w["entries_with_plan"] == 1 and w["entries_without_plan"] == 1
    assert d["plans_reviewed"] == 1 and d["worse_entry"] == 1 and d["risk_exceeded"] == 1  # риск 10·(103−90)=130 > 100
    assert d["bigger_qty"] == 0 and d["no_stop"] == 0


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


def test_api_is_per_account_only_real_trades_and_validates_days(env: Env) -> None:
    c = client(env)
    env.now[0] = NOW
    ts = NOW - 2 * HOUR
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 100, "ts": ts - HOUR})
    c.post("/api/journal", json={"market": "moex", "symbol": "SBER", "side": "sell", "qty": 10, "price": 110, "ts": ts})
    c.post("/api/journal", json={"market": "moex", "symbol": "GAZP", "side": "buy", "qty": 999, "price": 1, "ts": ts,
                                 "mode": "paper"})
    body = c.get("/api/review/week").json()
    by = {a["market"]: a for a in body["accounts"]}
    assert set(by) == {"moex", "crypto"} and "total" not in body
    assert by["moex"]["realized"] == 100 and by["moex"]["entries"] == 1 and by["moex"]["currency"] == "RUB"
    assert by["crypto"]["closed_trades"] == 0 and by["crypto"]["currency"] == "USDT"
    assert c.get("/api/review/week", params={"days": 0}).status_code == 422
    assert c.get("/api/review/week", params={"days": 90}).status_code == 422
    assert c.get("/api/review/week", headers={"Host": "evil.example"}).status_code == 403
