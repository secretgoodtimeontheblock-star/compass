"""Оповещения о цене: касание между проверками, одно срабатывание, мягкие отказы, API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from compass import alerts
from compass.alerts import Alert
from compass.api.app import create_app
from compass.markets import MarketError
from compass.models import Candle
from tests.conftest import Env

MIN = 60_000
T0 = 1_000_000 * MIN  # условное «сейчас» в мс


def minute(i: int, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(T0 + i * MIN, o, h, low, c, 10.0)


def alert(kind="above", price=105.0, created_s=T0 // 1000) -> Alert:
    return Alert("moex", "SBER", kind, price, id=1, created_at=created_s)


# --- касание ---


def test_touch_uses_the_wick_not_only_the_close() -> None:
    candles = [minute(0, 100, 101, 99, 100), minute(1, 100, 106, 99, 101), minute(2, 101, 102, 100, 101)]
    hit = alerts.touched(alert("above", 105), candles, T0, "1m")
    assert hit == (105.0, T0 + MIN)  # шпилька до 106 на минуту, закрытие 101 — всё равно касание
    assert alerts.touched(alert("above", 107), candles, T0, "1m") is None
    low = alerts.touched(alert("below", 99.5), candles, T0, "1m")
    assert low == (99.5, T0)


def test_gap_through_the_level_reports_the_open_not_an_impossible_price() -> None:
    candles = [minute(0, 110, 112, 109, 111)]  # открылись выше уровня 105
    assert alerts.touched(alert("above", 105), candles, T0, "1m") == (110.0, T0)
    down = [minute(0, 90, 91, 88, 89)]
    assert alerts.touched(alert("below", 95), down, T0, "1m") == (90.0, T0)


def test_candles_that_ended_before_the_alert_was_created_are_ignored() -> None:
    old = [minute(-10, 100, 200, 99, 100)]  # огромный максимум, но до создания оповещения
    assert alerts.touched(alert("above", 150, created_s=T0 // 1000), old, T0, "1m") is None
    running = [minute(0, 100, 150, 99, 100)]  # свеча ещё идёт в момент создания
    assert alerts.touched(alert("above", 150, created_s=(T0 + 30_000) // 1000), running, T0 + 30_000, "1m") is not None


# --- хранилище ---


def test_store_fires_once_validates_and_limits(env: Env) -> None:
    store = env.services.alerts
    a = store.add(Alert("moex", "SBER", "above", 105, note="  цель  "))
    assert a.id and a.note == "цель" and a.status == "active"
    assert store.trigger(a.id, 105.0, 123) is True and store.trigger(a.id, 106.0, 124) is False  # один раз
    got = store.get(a.id)
    assert got.status == "triggered" and got.triggered_price == 105.0 and got.triggered_at == 123
    assert store.cancel(a.id) is False  # сработавшее не отменить и не стереть
    for bad in (Alert("moex", "SBER", "sideways", 1), Alert("moex", "SBER", "above", -1), Alert("moex", "SBER", "above", 1, note="x" * 81)):
        with pytest.raises(ValueError):
            store.add(bad)
    for i in range(alerts.MAX_ACTIVE_PER_INSTRUMENT):
        store.add(Alert("moex", "GAZP", "above", 100 + i))
    with pytest.raises(ValueError, match="На один инструмент"):
        store.add(Alert("moex", "GAZP", "above", 999))


# --- движок ---


def setup_feed(env: Env, candles) -> None:
    env.adapter.timeframes = ("1m", "1h", "1d")
    env.adapter.data["SBER"] = candles
    env.now[0] = T0 + 10 * MIN


def create(env: Env, price: float, kind: str = "above") -> int:
    a = env.services.alerts.add(Alert("moex", "SBER", kind, price))
    # создание «в прошлом»: свечи в сцене начинаются в T0
    env.services.alerts._conn.execute("UPDATE alerts SET created_at=? WHERE id=?", (T0 // 1000, a.id))
    return a.id


def test_engine_fires_on_touch_notifies_once_and_survives_source_failure(env: Env) -> None:
    setup_feed(env, [minute(0, 100, 101, 99, 100), minute(1, 100, 106, 99, 101), minute(2, 101, 102, 100, 101)])
    aid = create(env, 105)
    fired = env.services.alert_engine.check()
    assert [a.id for a in fired] == [aid] and fired[0].triggered_price == 105
    assert len(env.notifier.texts) == 1 and "SBER" in env.notifier.texts[0] and "не сигнал" in env.notifier.texts[0]
    assert env.services.alert_engine.check() == [] and len(env.notifier.texts) == 1  # повторно не шлём
    second = create(env, 150)
    env.adapter.data["SBER"] = MarketError("источник упал")  # кэш есть, источник недоступен — проверка не падает
    env.services.alert_engine.check()
    assert env.services.alerts.get(second).status == "active"


def test_engine_below_and_untouched_stay_active(env: Env) -> None:
    setup_feed(env, [minute(0, 100, 101, 99, 100), minute(1, 100, 101, 98, 99)])
    up, down = create(env, 120, "above"), create(env, 98.5, "below")
    env.services.alert_engine.check()
    assert env.services.alerts.get(up).status == "active" and env.services.alerts.get(down).status == "triggered"


def test_engine_works_with_hourly_candles_only(env: Env) -> None:
    env.adapter.timeframes = ("1h", "1d")  # у источника нет минуток
    env.adapter.data["SBER"] = [Candle(T0, 100, 130, 99, 100, 1.0)]
    env.now[0] = T0 + 10 * MIN
    aid = create(env, 120)
    assert [a.id for a in env.services.alert_engine.check()] == [aid]


# --- API ---


def client(env: Env) -> TestClient:
    return TestClient(create_app(env.services), base_url="http://127.0.0.1")


def test_api_infers_direction_and_refuses_alerts_that_would_fire_immediately(env: Env) -> None:
    setup_feed(env, [minute(0, 100, 101, 99, 100)])
    c = client(env)
    up = c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 110, "note": "цель"}).json()
    assert up["kind"] == "above" and up["last_price"] == 100
    assert c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 90}).json()["kind"] == "below"
    r = c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 95, "kind": "above"})
    assert r.status_code == 422 and "уже выше" in r.json()["detail"]
    r = c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 105, "kind": "below"})
    assert r.status_code == 422 and "уже ниже" in r.json()["detail"]
    assert c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 0}).status_code == 422
    assert c.post("/api/alerts", json={"market": "nope", "symbol": "SBER", "price": 5}).status_code == 404


def test_api_check_list_seen_and_cancel(env: Env) -> None:
    setup_feed(env, [minute(0, 100, 101, 99, 100), minute(1, 100, 112, 99, 101)])
    c = client(env)
    a = c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 110}).json()
    # создание «до свечей»: сдвигаем время создания назад
    env.services.alerts._conn.execute("UPDATE alerts SET created_at=? WHERE id=?", (T0 // 1000, a["id"]))
    other = c.post("/api/alerts", json={"market": "moex", "symbol": "SBER", "price": 50}).json()
    assert [x["id"] for x in c.get("/api/alerts", params={"status": "active"}).json()] == [other["id"], a["id"]]
    fired = c.post("/api/alerts/check").json()["triggered"]
    assert [x["id"] for x in fired] == [a["id"]]
    assert [x["id"] for x in c.get("/api/alerts", params={"status": "triggered", "unseen": True}).json()] == [a["id"]]
    assert c.post("/api/alerts/seen").json() == {"marked": 1}
    assert c.get("/api/alerts", params={"status": "triggered", "unseen": True}).json() == []
    assert c.delete(f"/api/alerts/{other['id']}").status_code == 204
    assert c.delete(f"/api/alerts/{other['id']}").status_code == 404  # повторная отмена
    assert c.delete(f"/api/alerts/{a['id']}").status_code == 404  # сработавшее не отменяется
    assert c.get("/api/alerts", params={"status": "weird"}).status_code == 422
