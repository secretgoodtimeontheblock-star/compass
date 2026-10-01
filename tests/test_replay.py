"""Учебный счёт: будущие свечи не утекают, заявки исполняются по следующему открытию, стоп — по тем же
консервативным правилам, что в бэктесте; учебные сделки не попадают в реальный учёт."""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from compass import db
from compass.api.app import create_app
from compass.models import Candle
from compass.replay import Bar, Sim, apply_bar, summarize
from tests.conftest import DAY, Env

WARMUP = 100
FLAT = (100.0, 101.0, 99.0, 100.0)  # open, high, low, close


def bars_data(script: list[tuple[float, float, float, float]], pad_to: int = 25) -> list[Candle]:
    rows = [FLAT] * WARMUP + list(script) + [FLAT] * max(0, pad_to - len(script))
    return [Candle(i * DAY, o, h, low, c, 1000.0) for i, (o, h, low, c) in enumerate(rows)]


def start(env: Env, script, *, replay_bars: int | None = None, capital: float = 100_000.0, fee=0.0, slip=0.0,
          symbol: str = "SBER", **kw):
    data = bars_data(script)
    env.adapter.data[symbol] = data
    env.adapter.lot = 1
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    r = c.post("/api/replay", json={"market": "moex", "symbol": symbol, "tf": "1d",
                                    "replay_bars": replay_bars or len(data) - WARMUP, "capital": capital,
                                    "fee_pct": fee, "slippage_pct": slip, "spread_pct": 0, **kw})
    assert r.status_code == 201, r.text
    return c, r.json()


def order(c, sid, side="buy", qty=100, stop=None):
    return c.post(f"/api/replay/{sid}/order", json={"side": side, "qty": qty, "stop": stop})


def step(c, sid, n=1):
    return c.post(f"/api/replay/{sid}/step", json={"bars": n})


# --- будущее не утекает ---


def test_future_candles_never_leave_the_server_until_opened(env: Env) -> None:
    secret = [(100 + i * 0.01 + 0.123456, 101.5, 99.5, 555.0 + i * 0.001 + 0.0007) for i in range(25)]  # приметные цены
    c, s = start(env, secret)
    sid = s["id"]
    seen = json.dumps([s, c.get(f"/api/replay/{sid}").json(), c.get(f"/api/replay/{sid}/candles").json(), c.get("/api/replay").json()])
    for i in range(25):
        assert f"{555.0 + i * 0.001 + 0.0007:.4f}"[:7] not in seen
    cs = c.get(f"/api/replay/{sid}/candles").json()["candles"]
    assert len(cs) == WARMUP and max(x["t"] for x in cs) == (WARMUP - 1) * DAY  # видна только история до начала
    assert s["remaining"] == 25 and s["replayed"] == 0 and s["last_close"] == 100.0
    r = step(c, sid, 3).json()
    cs = c.get(f"/api/replay/{sid}/candles").json()["candles"]
    assert len(cs) == WARMUP + 3 and r["cursor_ts"] == (WARMUP + 2) * DAY and r["replayed"] == 3
    text = json.dumps(c.get(f"/api/replay/{sid}/candles").json())
    assert "555.0007" in text  # открытые свечи видны
    assert "555.0257" not in text and "555.0247" not in text  # а свечи правее курсора — нет


def test_session_data_is_frozen_at_creation(env: Env) -> None:
    c, s = start(env, [(100, 101, 99, 100)] * 25)
    env.adapter.data["SBER"] = bars_data([(300, 301, 299, 300)] * 25)  # источник «исправили» задним числом
    env.services.cache._conn.execute("DELETE FROM candles")
    r = step(c, s["id"]).json()
    assert r["last_close"] == 100.0
    assert all(x["c"] == 100.0 for x in c.get(f"/api/replay/{s['id']}/candles").json()["candles"])


# --- исполнение ---


def test_order_fills_at_next_open_with_slippage_and_fee(env: Env) -> None:
    script = [(100, 106, 99, 104), (102, 105, 101, 104)] + [(104, 105, 103, 104)] * 23  # у свечи открытие ≠ закрытие
    c, s = start(env, script, fee=0.1, slip=0.1)
    assert order(c, s["id"], qty=100).json()["pending"] == {"side": "buy", "qty": 100, "stop": None}
    r = step(c, s["id"]).json()  # первая свеча воспроизведения: open 100 → цена 100.1
    f = r["fills"][0]
    assert f["price"] == pytest.approx(100.1) and f["fee"] == pytest.approx(100 * 100.1 * 0.001) and f["ts"] == WARMUP * DAY
    assert r["cash"] == pytest.approx(100_000 - 100 * 100.1 - 100 * 100.1 * 0.001, abs=0.01)
    assert r["qty"] == 100 and r["pending"] is None and r["avg_price"] == pytest.approx(100.1 + 100.1 * 0.001)
    sell = order(c, s["id"], "sell", 100)
    assert sell.status_code == 200
    r = step(c, s["id"]).json()  # открытие 102 → продажа по 101.898
    assert r["fills"][1]["price"] == pytest.approx(102 * 0.999) and r["qty"] == 0
    assert r["result"]["closed_trades"] == 1


def test_stop_inside_candle_and_gap_use_conservative_prices(env: Env) -> None:
    script = [(100, 101, 99, 100), (100, 101, 94, 97)] + [(97, 98, 96, 97)] * 23  # свеча 2: минимум 94 ниже стопа 96
    c, s = start(env, script)
    order(c, s["id"], qty=100, stop=96)
    step(c, s["id"])  # вход по 100
    r = step(c, s["id"]).json()
    stop_fill = r["fills"][1]
    assert (stop_fill["reason"], stop_fill["price"], stop_fill["qty"]) == ("stop", 96, 100) and r["qty"] == 0
    assert any("Сработал стоп" in e for e in r["events"]) and r["stop"] is None
    gap = [(100, 101, 99, 100), (90, 92, 88, 91)] + [(91, 92, 90, 91)] * 23  # открытие 90 ниже стопа 96
    c2, s2 = start(env, gap, symbol="GAP")
    order(c2, s2["id"], qty=100, stop=96)
    step(c2, s2["id"])
    r2 = step(c2, s2["id"]).json()
    assert (r2["fills"][1]["reason"], r2["fills"][1]["price"]) == ("gap_stop", 90) and any("гэпом" in e for e in r2["events"])


def test_buy_is_cancelled_when_open_is_not_above_stop_or_cash_is_short(env: Env) -> None:
    c, s = start(env, [(95, 96, 94, 95)] + [FLAT] * 24)  # открытие 95 не выше стопа 96
    order(c, s["id"], qty=10, stop=96)
    r = step(c, s["id"]).json()
    assert r["fills"] == [] and r["qty"] == 0 and any("не выше вашего стопа" in e for e in r["events"])
    c2, s2 = start(env, [(300, 301, 299, 300)] * 25, capital=15_000, symbol="X2")
    order(c2, s2["id"], qty=140)  # на последней цене 100 хватает (14 000), но следующая свеча открывается по 300
    r2 = step(c2, s2["id"]).json()
    assert r2["fills"] == [] and r2["qty"] == 0 and r2["cash"] == 15_000 and any("не хватило" in e for e in r2["events"])


def test_order_validation_and_state_rules(env: Env) -> None:
    c, s = start(env, [FLAT] * 25, capital=10_000)
    sid = s["id"]
    assert order(c, sid, qty=101).status_code == 422  # 101 · 100 > 10 000: не хватает денег
    assert order(c, sid, "sell", 1).status_code == 422  # позиции нет
    assert order(c, sid, "buy", 10, stop=100).status_code == 422  # стоп не ниже цены
    assert order(c, sid, "sell", 1, stop=5).status_code == 422
    env.adapter.lot = 10
    assert order(c, sid, qty=15).status_code == 422 and "лот" in order(c, sid, qty=15).json()["detail"]
    env.adapter.lot = 1
    assert order(c, sid, qty=50).status_code == 200
    assert order(c, sid, qty=10).status_code == 422 and "есть заявка" in order(c, sid, qty=10).json()["detail"]
    assert c.delete(f"/api/replay/{sid}/order").json()["pending"] is None
    assert c.delete(f"/api/replay/{sid}/order").status_code == 404
    assert c.post(f"/api/replay/{sid}/stop", json={"stop": 90}).status_code == 422  # позиции нет
    assert c.get("/api/replay/999").status_code == 404
    assert step(c, sid, 0).status_code == 422 and step(c, sid, 51).status_code == 422


def test_manual_stop_change_persists_and_applies(env: Env) -> None:
    script = [(100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 91, 92)] + [(92, 93, 91, 92)] * 22
    c, s = start(env, script)
    order(c, s["id"], qty=100, stop=90)
    step(c, s["id"])
    r = c.post(f"/api/replay/{s['id']}/stop", json={"stop": 95}).json()
    assert r["stop"] == 95 and c.get(f"/api/replay/{s['id']}").json()["stop"] == 95
    assert c.post(f"/api/replay/{s['id']}/stop", json={"stop": 100}).status_code == 422  # не ниже последней цены
    step(c, s["id"])
    r = step(c, s["id"]).json()  # минимум 91 ниже подтянутого стопа 95 (но не ниже прежнего 90)
    assert r["qty"] == 0 and r["fills"][-1]["reason"] == "stop" and r["fills"][-1]["price"] == 95
    c.post(f"/api/replay/{s['id']}/stop", json={"stop": None})  # позиции нет — ошибка не должна ломать состояние
    assert c.get(f"/api/replay/{s['id']}").json()["qty"] == 0


def test_end_of_data_finishes_session_and_blocks_actions(env: Env) -> None:
    c, s = start(env, [FLAT] * 20, replay_bars=20)
    sid = s["id"]
    assert order(c, sid, qty=10).status_code == 200
    r = step(c, sid, 20).json()
    assert r["status"] == "finished" and r["remaining"] == 0 and r["replayed"] == 20
    assert order(c, sid, qty=1).status_code == 422 and step(c, sid).status_code == 422
    assert c.post(f"/api/replay/{sid}/finish").json()["status"] == "finished"
    # заявка на последней свече не остаётся висеть
    c2, s2 = start(env, [FLAT] * 20, replay_bars=20, symbol="LAST")
    step(c2, s2["id"], 19)
    order(c2, s2["id"], qty=5)
    r2 = step(c2, s2["id"]).json()
    assert r2["status"] == "finished" and r2["qty"] == 5  # исполнилась на открытии последней свечи


def test_finish_early_blocks_further_steps(env: Env) -> None:
    c, s = start(env, [FLAT] * 25)
    step(c, s["id"], 2)
    assert c.post(f"/api/replay/{s['id']}/finish").json()["status"] == "finished"
    assert step(c, s["id"]).status_code == 422


# --- результат и дисциплина ---


def test_result_numbers_and_discipline(env: Env) -> None:
    script = [(100, 101, 99, 100), (100, 111, 99, 110)] + [(110, 111, 109, 110)] * 23
    c, s = start(env, script, capital=100_000)
    order(c, s["id"], qty=100)  # без стопа
    step(c, s["id"], 2)
    r = c.get(f"/api/replay/{s['id']}").json()["result"]
    assert r["equity"] == pytest.approx(101_000) and r["return_pct"] == 1.0  # 100 шт. · (110 − 100)
    assert r["buy_hold_pct"] == 10.0  # цена закрытия 100 → 110
    assert r["entries_without_stop"] == 1 and r["closed_trades"] == 0
    assert r["win_rate_pct"] is None and "меньше 10" in r["note"]  # мало сделок — доля прибыльных не показывается
    order(c, s["id"], "sell", 100)
    step(c, s["id"])
    r = c.get(f"/api/replay/{s['id']}").json()["result"]
    assert r["closed_trades"] == 1 and r["realized"] == pytest.approx(1000)
    c2, s2 = start(env, script, capital=100_000, symbol="RSK")
    order(c2, s2["id"], qty=100, stop=90)
    step(c2, s2["id"])
    f = c2.get(f"/api/replay/{s2['id']}").json()
    assert f["fills"][0]["risk_pct"] == pytest.approx(1.0, abs=0.001)  # 100 · (100 − 90) / 100 000 = 1%
    assert f["result"]["max_entry_risk_pct"] == pytest.approx(1.0, abs=0.001)


def test_win_rate_appears_only_with_enough_trades() -> None:
    bars = [Bar(i * DAY, 100, 101, 99, 100, 1) for i in range(40)]
    fills = []
    from compass.replay import Fill

    for k in range(10):
        fills.append(Fill((2 * k) * DAY, "buy", 1, 100, 0, "order", None))
        fills.append(Fill((2 * k + 1) * DAY, "sell", 1, 101 if k < 6 else 99, 0, "order"))
    r = summarize(1000, fills, bars[:25], 0)
    assert r["closed_trades"] == 10 and r["win_rate_pct"] == 60.0 and r["note"] is None


def test_apply_bar_pure_rules() -> None:
    sim = Sim(cash=10_000, pending=("buy", 10, 95.0))
    fills, ev = apply_bar(sim, Bar(1, 95, 96, 90, 92, 1), 0, 0)  # открытие 95 не выше стопа 95
    assert fills == [] and sim.qty == 0 and ev
    sim = Sim(cash=10_000, pending=("buy", 10, 90.0))
    apply_bar(sim, Bar(1, 100, 101, 99, 100, 1), 0, 0)
    assert sim.qty == 10 and sim.stop == 90 and sim.cash == 9_000
    fills, _ = apply_bar(sim, Bar(2, 100, 100, 85, 86, 1), 0, 0)  # стоп внутри свечи
    assert fills[0].price == 90 and sim.qty == 0 and sim.stop is None


# --- изоляция от реального учёта ---


def test_paper_trades_never_touch_real_journal_day_panel_or_signals(env: Env) -> None:
    c, s = start(env, [(100, 101, 99, 100), (100, 105, 99, 104)] + [FLAT] * 23)
    order(c, s["id"], qty=100, stop=95)
    step(c, s["id"], 3)
    assert c.get("/api/journal", params={"mode": "all"}).json() == []
    assert c.get("/api/journal/positions", params={"mode": "all"}).json() == []
    day = {a["market"]: a for a in c.get("/api/day").json()["accounts"]}
    assert day["moex"]["exposure"] == 0 and day["moex"]["daily_pnl"] == 0 and day["moex"]["positions"] == []
    assert c.get("/api/signals").json() == [] and c.get("/api/plans").json() == []


def test_creation_limits_and_listing(env: Env) -> None:
    env.adapter.data["SHORT"] = bars_data([FLAT] * 5)[:80]
    env.adapter.lot = 1
    env.now[0] = 10_000 * DAY
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert c.post("/api/replay", json={"market": "moex", "symbol": "SHORT", "replay_bars": 30}).status_code == 422
    assert c.post("/api/replay", json={"market": "moex", "symbol": "SHORT", "replay_bars": 5}).status_code == 422
    assert c.post("/api/replay", json={"market": "nope", "symbol": "X"}).status_code == 404
    c2, s = start(env, [FLAT] * 25)
    listed = c2.get("/api/replay").json()
    assert [x["id"] for x in listed] == [s["id"]] and listed[0]["remaining"] == 25 and "candles" not in listed[0]


def test_foreign_origin_cannot_drive_a_session(env: Env) -> None:
    c, s = start(env, [FLAT] * 25)
    r = c.post(f"/api/replay/{s['id']}/step", json={"bars": 1}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert c.get(f"/api/replay/{s['id']}").json()["replayed"] == 0


# --- хранение ---


def test_fills_are_append_only(env: Env) -> None:
    c, s = start(env, [FLAT] * 25)
    order(c, s["id"], qty=10)
    step(c, s["id"])
    conn = env.services.cache._conn
    with pytest.raises(sqlite3.DatabaseError, match="неизменяема"):
        conn.execute("UPDATE replay_fills SET price = 1")
    with pytest.raises(sqlite3.DatabaseError, match="нельзя удалить"):
        conn.execute("DELETE FROM replay_fills")


def test_v12_upgrade_adds_replay_tables_keeping_data_and_backup(tmp_path) -> None:
    path = tmp_path / "compass.sqlite3"
    old = sqlite3.connect(path)
    scripts = "".join(getattr(db, f"_V{i}") for i in range(1, 13))
    old.executescript(scripts + "PRAGMA user_version=12;")
    old.execute("INSERT INTO journal (market, symbol, side, qty, price, fee, ts, created_at, uid) "
                "VALUES ('moex','SBER','buy',10,270.5,0,1000,5,'u1')")
    old.commit()
    old.close()
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT qty FROM journal").fetchone() == (10,)
        assert conn.execute("SELECT COUNT(*) FROM replay_sessions").fetchone() == (0,)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert {"replay_fills_no_update", "replay_fills_no_delete"} <= names
    finally:
        conn.close()
    assert sqlite3.connect(str(path) + ".v12.bak").execute("PRAGMA user_version").fetchone()[0] == 12
