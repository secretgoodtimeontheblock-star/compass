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


def test_api_journal_flow(env: Env) -> None:
    client = TestClient(create_app(env.services))
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
    client = TestClient(create_app(env.services))
    got = client.get("/api/signals", params={"market": "moex", "symbol": "SBER"}).json()
    assert [s["symbol"] for s in got] == ["SBER"]


def test_ui_is_served_but_api_is_not_shadowed(env: Env, tmp_path, monkeypatch) -> None:
    (tmp_path / "index.html").write_text("<title>Compass UI</title>", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path)
    client = TestClient(create_app(env.services))
    assert "Compass UI" in client.get("/").text
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/nope").status_code == 404
