from fastapi.testclient import TestClient

from compass.__main__ import build_services
from compass.api.app import create_app
from compass.config import Config
from tests.conftest import DAY, ScriptedAdapter, day_candles


def test_workflow_survives_restart(tmp_path, monkeypatch) -> None:
    # Весь сценарий использует отдельную БД и заданные котировки, без сети и Telegram.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    cfg = Config(tmp_path, "okx", None)
    adapter = ScriptedAdapter({"SBER": day_candles([100.0] * 25 + [120.0])})
    svc = build_services(cfg, background_scan=False)
    svc.adapters["moex"]._client.close()
    svc.adapters["moex"] = adapter
    try:
        with TestClient(create_app(svc)) as client:
            assert client.get("/api/health").json()["ok"]
            assert client.post("/api/watchlist", json={
                "market": "moex", "symbol": "SBER", "name": "Сбербанк",
            }).status_code == 201
            assert client.put("/api/settings", json={"capital": 50_000}).status_code == 200
            result = client.post("/api/scan")
            assert result.status_code == 200 and result.json()["new"]
            signal = next(s for s in client.get("/api/signals").json() if s["strategy"] == "donchian")
            risk = client.post("/api/risk", json={
                "market": "moex", "symbol": "SBER", "entry": signal["price"], "stop": signal["stop"],
            })
            assert risk.status_code == 200 and risk.json()["qty"] > 0
            entry = client.post("/api/journal", json={
                "market": "moex", "symbol": "SBER", "side": "buy",
                "qty": risk.json()["qty"], "price": signal["price"],
                "ts": 26 * DAY, "signal_id": signal["id"], "note": "Вход по сигналу",
            })
            assert entry.status_code == 201
            saved_entry = entry.json()
    finally:
        svc.cache._conn.close()

    restored = build_services(cfg, background_scan=False)
    try:
        with TestClient(create_app(restored)) as client:
            assert client.get("/api/watchlist").json()[0]["symbol"] == "SBER"
            assert client.get("/api/settings").json()["capital"] == 50_000
            assert client.get("/api/journal").json() == [saved_entry]
            assert client.get("/api/journal/positions").json()[0]["qty"] == saved_entry["qty"]
            assert any(s["id"] == signal["id"] for s in client.get("/api/signals").json())
            with restored.cache._conn.lock:
                assert restored.cache._conn.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 26
    finally:
        restored.adapters["moex"]._client.close()
        restored.cache._conn.close()
