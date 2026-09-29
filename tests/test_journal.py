from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compass.api import app as app_module
from compass.api.app import create_app
from compass.journal import Entry, summarize
from tests.conftest import Env


def e(side: str, qty: float, price: float, ts: int, fee: float = 0.0, symbol: str = "SBER") -> Entry:
    return Entry("moex", symbol, side, qty, price, ts, fee)


def test_average_cost_and_realized_pnl_with_fees() -> None:
    (p,) = summarize(
        [
            e("buy", 10, 100, 1, fee=1),  # себестоимость 1001
            e("buy", 10, 120, 2, fee=1),  # +1201 → 2202 на 20 шт, средняя 110.1
            e("sell", 5, 130, 3, fee=1),  # прибыль 5·(130−110.1) − 1 = 98.5
        ]
    )
    assert p.qty == 15 and p.avg_price == pytest.approx(110.1)
    assert p.realized_pnl == pytest.approx(98.5) and p.fees == 3 and p.trades == 3


def test_closing_position_resets_average() -> None:
    (p,) = summarize([e("buy", 10, 100, 1), e("sell", 10, 110, 2), e("buy", 4, 200, 3)])
    assert p.qty == 4 and p.avg_price == 200 and p.realized_pnl == 100


def test_symbols_are_summarized_separately() -> None:
    ps = summarize([e("buy", 1, 10, 1, symbol="A"), e("buy", 1, 50, 1, symbol="B")])
    assert [(p.symbol, p.avg_price) for p in ps] == [("A", 10), ("B", 50)]


def test_same_timestamp_buy_counts_before_sell() -> None:
    (p,) = summarize([e("sell", 5, 12, 100), e("buy", 5, 10, 100)])
    assert p.qty == 0 and p.realized_pnl == 10


def test_oversell_is_rejected() -> None:
    with pytest.raises(ValueError, match="больше позиции"):
        summarize([e("buy", 5, 10, 1), e("sell", 6, 10, 2)])


def test_journal_rejects_bad_entries_and_keeps_db_clean(env: Env) -> None:
    j = env.services.journal
    for bad in [e("hold", 1, 1, 1), e("buy", 0, 1, 1), e("buy", 1, -1, 1), e("buy", 1, 1, 1, fee=-1), e("buy", 1, 1, 0)]:
        with pytest.raises(ValueError):
            j.add(bad)
    with pytest.raises(ValueError, match="больше позиции"):
        j.add(e("sell", 1, 10, 5))  # позиции нет
    assert j.list() == []


def test_cannot_delete_buy_that_backs_a_sell(env: Env) -> None:
    j = env.services.journal
    buy = j.add(e("buy", 10, 100, 1))
    j.add(e("sell", 10, 110, 2))
    with pytest.raises(ValueError):
        j.remove(buy.id)
    assert len(j.list()) == 2  # ничего не удалилось
    assert j.remove(9999) is False


def test_journal_keeps_plan_and_exports_csv(env: Env) -> None:
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    body = {
        "market": "moex",
        "symbol": "SBER",
        "side": "buy",
        "qty": 10,
        "price": 250,
        "ts": 1_700_000_000_000,
        "reason": "пробой",
        "planned_stop": 240,
        "signal_id": None,
    }
    saved = client.post("/api/journal", json=body)
    assert saved.status_code == 201
    assert saved.json()["reason"] == "пробой" and saved.json()["planned_stop"] == 240
    csv_body = client.get("/api/journal.csv").text
    assert "пробой" in csv_body and "SBER" in csv_body
    assert client.post("/api/journal", json={**body, "planned_stop": 0, "ts": 2}).status_code == 422


def test_api_journal_flow(env: Env) -> None:
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    body = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 250, "ts": 1_700_000_000_000}
    r = client.post("/api/journal", json=body)
    assert r.status_code == 201 and r.json()["id"] > 0
    client.post("/api/journal", json={**body, "side": "sell", "qty": 4, "price": 260, "ts": 1_700_000_100_000})

    (pos,) = client.get("/api/journal/positions").json()
    assert pos["qty"] == 6 and pos["realized_pnl"] == 40
    assert len(client.get("/api/journal", params={"symbol": "SBER"}).json()) == 2
    assert client.get("/api/journal", params={"symbol": "GAZP"}).json() == []

    assert client.post("/api/journal", json={**body, "qty": -1}).status_code == 422
    assert client.post("/api/journal", json={**body, "market": "nope"}).status_code == 404
    oversell = {**body, "side": "sell", "qty": 100, "ts": 1_700_000_200_000}
    r = client.post("/api/journal", json=oversell)
    assert r.status_code == 422 and "больше позиции" in r.json()["detail"]

    by_side = {x["side"]: x["id"] for x in client.get("/api/journal").json()}
    assert client.delete(f"/api/journal/{by_side['buy']}").status_code == 422  # продажа опирается на покупку
    assert client.delete(f"/api/journal/{by_side['sell']}").status_code == 204
    assert client.delete(f"/api/journal/{by_side['buy']}").status_code == 204  # теперь можно
    assert client.delete("/api/journal/999999").status_code == 404


def test_signals_filter_by_symbol(env: Env) -> None:
    from compass.signals import Signal

    st = env.services.signals
    st.insert(Signal("moex", "SBER", "1d", "donchian", "buy", 1, 10.0))
    st.insert(Signal("moex", "GAZP", "1d", "donchian", "buy", 1, 10.0))
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    got = client.get("/api/signals", params={"market": "moex", "symbol": "SBER"}).json()
    assert [s["symbol"] for s in got] == ["SBER"]


def test_ui_is_served_but_api_is_not_shadowed(env: Env, tmp_path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text("<title>Compass UI</title>", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path)
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    assert "Compass UI" in client.get("/").text
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/nope").status_code == 404


# --- режимы, мягкое удаление, резервная копия ---


def m(mode: str, side: str, qty: float, price: float, ts: int, symbol: str = "SBER") -> Entry:
    return Entry("moex", symbol, side, qty, price, ts, mode=mode)


def test_modes_are_never_mixed_in_positions_or_lists(env: Env) -> None:
    j = env.services.journal
    j.add(m("real", "buy", 10, 100, 1))
    j.add(m("paper", "buy", 500, 100, 2))
    j.add(m("historical", "buy", 7, 100, 3))
    (real,) = j.positions()
    assert (real.mode, real.qty) == ("real", 10)
    assert {p.mode: p.qty for p in j.positions(None)} == {"real": 10, "paper": 500, "historical": 7}
    assert [x.mode for x in j.list()] == ["real"]
    assert len(j.list(mode=None)) == 3
    with pytest.raises(ValueError, match="больше позиции"):
        j.add(m("real", "sell", 11, 110, 4))  # учебные 500 шт. не прикрывают реальную продажу
    j.add(m("paper", "sell", 400, 110, 4))
    with pytest.raises(ValueError, match="Режим"):
        j.add(m("demo", "buy", 1, 1, 5))


def test_delete_is_soft_reversible_and_validated(env: Env) -> None:
    j = env.services.journal
    buy = j.add(m("real", "buy", 10, 100, 1))
    sell = j.add(m("real", "sell", 5, 110, 2))
    assert j.remove(sell.id) and j.remove(buy.id)
    assert j.list() == [] and {x.id for x in j.deleted()} == {buy.id, sell.id}
    assert j.remove(buy.id) is False  # уже удалена
    with pytest.raises(ValueError, match="больше позиции"):
        j.restore(sell.id)  # продажа без покупки
    assert j.restore(buy.id) and j.restore(sell.id)
    assert j.restore(buy.id) is False
    assert j.positions()[0].qty == 5 and j.deleted() == []


def _fill(j) -> None:
    j.add(m("real", "buy", 10, 100, 1))
    j.add(Entry("moex", "SBER", "sell", 4, 120, 2, fee=1.0, note="фикс", reason="цель", planned_stop=90.0))
    j.add(m("paper", "buy", 3, 50, 3, symbol="GAZP"))
    j.add(m("historical", "buy", 2, 60, 4, symbol="LKOH"))
    gone = j.add(m("real", "buy", 1, 10, 5, symbol="YNDX"))
    j.remove(gone.id)


def _key(e: Entry) -> tuple:
    return (e.uid, e.mode, e.symbol, e.side, e.qty, e.price, e.fee, e.note, e.reason, e.planned_stop)


def test_backup_roundtrip_is_idempotent_and_keeps_modes_and_deleted() -> None:
    from compass.db import connect
    from compass.journal import Journal

    src, dst = Journal(connect(":memory:")), Journal(connect(":memory:"))
    _fill(src)
    snap = src.backup()
    assert snap["format"] == "compass-journal" and len(snap["entries"]) == 5
    assert dst.restore_backup(snap) == {"added": 5, "skipped": 0, "plans_added": 0, "plans_skipped": 0}
    assert dst.restore_backup(snap) == {"added": 0, "skipped": 5, "plans_added": 0, "plans_skipped": 0}  # повтор ничего не дублирует
    assert sorted(map(_key, dst.list(mode=None))) == sorted(map(_key, src.list(mode=None)))
    assert [e.symbol for e in dst.deleted()] == ["YNDX"]
    assert [p.qty for p in dst.positions(None) if p.symbol == "SBER"] == [6]


def _rec(uid: str, side: str, qty: float, ts: int, **kw) -> dict:
    return {
        "market": "moex", "symbol": "SBER", "side": side, "qty": qty, "price": 10.0, "ts": ts, "fee": 0.0,
        "note": "", "signal_id": None, "planned_stop": None, "reason": "", "mode": "real", "uid": uid,
        "deleted_at": None, **kw,
    }


def _wrap(entries) -> dict:
    return {"format": "compass-journal", "version": 1, "exported_at": 1, "entries": entries}


def test_restore_is_all_or_nothing_and_rejects_garbage() -> None:
    from compass.db import connect
    from compass.journal import Journal

    j = Journal(connect(":memory:"))
    bad_sets = [
        _wrap([_rec("a", "buy", 5, 1), _rec("b", "sell", 6, 2)]),  # продажа больше позиции
        _wrap([_rec("a", "buy", 5, 1), _rec("a", "buy", 5, 2)]),  # повторный uid
        _wrap([_rec("a", "buy", 5, 1), _rec("b", "buy", -1, 2)]),  # плохая запись
        _wrap([_rec("a", "buy", 5, 1), {**_rec("b", "buy", 1, 2), "evil": 1}]),  # лишнее поле
        _wrap([{**_rec("a", "buy", 5, 1), "uid": ""}]),
        {"format": "other", "version": 1, "entries": []},
        {"format": "compass-journal", "version": 99, "entries": []},
        {"format": "compass-journal", "version": 1, "entries": "x"},
    ]
    for payload in bad_sets:
        with pytest.raises(ValueError):
            j.restore_backup(payload)
        assert j.list(mode=None) == [] and j.deleted() == []  # ни одной записи не просочилось
    assert j.restore_backup(_wrap([_rec("a", "buy", 5, 1), _rec("b", "sell", 5, 2)])) == {"added": 2, "skipped": 0, "plans_added": 0, "plans_skipped": 0}


def test_api_modes_backup_restore_and_undelete(env: Env) -> None:
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    body = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 10, "price": 250, "ts": 1_700_000_000_000}
    real = client.post("/api/journal", json=body).json()
    client.post("/api/journal", json={**body, "mode": "paper", "qty": 99})
    assert real["mode"] == "real" and real["uid"]
    assert client.post("/api/journal", json={**body, "mode": "demo"}).status_code == 422
    assert [x["qty"] for x in client.get("/api/journal").json()] == [10]
    assert len(client.get("/api/journal", params={"mode": "all"}).json()) == 2
    assert client.get("/api/journal", params={"mode": "zzz"}).status_code == 422
    assert [p["qty"] for p in client.get("/api/journal/positions", params={"mode": "paper"}).json()] == [99]
    assert "paper" in client.get("/api/journal.csv").text

    dump = client.get("/api/journal/backup.json")
    assert "attachment" in dump.headers["content-disposition"] and len(dump.json()["entries"]) == 2
    assert client.delete(f"/api/journal/{real['id']}").status_code == 204
    assert [x["id"] for x in client.get("/api/journal/deleted").json()] == [real["id"]]
    assert client.post(f"/api/journal/{real['id']}/restore").status_code == 200
    assert client.post(f"/api/journal/{real['id']}/restore").status_code == 404
    assert client.post("/api/journal/restore", json=dump.json()).json() == {"added": 0, "skipped": 2, "plans_added": 0, "plans_skipped": 0}
    assert client.post("/api/journal/restore", json={"format": "x"}).status_code == 422
