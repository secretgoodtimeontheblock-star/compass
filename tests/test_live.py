import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.live import OkxLive, candle_dto, sse, subscription
from compass.models import Candle
from tests.conftest import Env, day_candles


def row(ts=1_800_000, close="12", confirm="0", volume="7"):
    return [str(ts), "10", "13", "9", close, volume, "7", "70", confirm]


def test_daily_channel_uses_utc_and_rejects_non_spot():
    assert subscription("BTC/USDT", "1d") == {"channel": "candle1Dutc", "instId": "BTC-USDT"}
    assert subscription("BTC/USDT", "4h")["channel"] == "candle4H"
    for symbol, tf in [("BTC/USDT:USDT", "1d"), ("../bad", "1h"), ("BTC/USDT", "1w")]:
        with pytest.raises(ValueError):
            subscription(symbol, tf)


def test_candle_validation_and_volume():
    assert candle_dto(row(), "1m") == {"t": 1_800_000, "o": 10, "h": 13, "l": 9, "c": 12, "v": 7}
    for bad in [row(close="NaN"), row(close="Infinity"), row(close="0"), row(close="14"), row(ts=1), row(confirm="bad"), [1]]:
        with pytest.raises(ValueError):
            candle_dto(bad, "1m")


def test_sse_is_single_json_record():
    event = sse("status", {"message": "a\nb"})
    assert len(event.splitlines()) == 3
    assert json.loads(event.splitlines()[1][6:])["message"] == "a\nb"


def test_stream_restores_history_and_ignores_old_or_reopened_candles(env: Env, monkeypatch):
    from compass import live

    env.services.adapters["crypto"] = env.adapter
    env.adapter.timeframes = ("1m",)
    env.adapter.data["BTC/USDT"] = [Candle(1_800_000, 10, 13, 9, 12, 8)]
    arg = subscription("BTC/USDT", "1m")
    sent = []
    closed = []
    payloads = [
        {"event": "subscribe", "arg": arg},
        {"arg": arg, "data": [row()]},  # событие до REST: объём 7 старее снимка с объёмом 8
        {"arg": arg, "data": [row(confirm="1", volume="9")]},
        {"arg": arg, "data": [row(ts=1_740_000)]},
        {"arg": arg, "data": [row(close="11")]},
        {"arg": arg, "data": [row(ts=1_860_000)]},
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def send_json(self, data):
            sent.append(data)

        async def receive(self, **kwargs):
            return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payloads.pop(0)))

    class Session:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def ws_connect(self, *args, **kwargs):
            return Socket()

    monkeypatch.setattr(live.aiohttp, "ClientSession", Session)

    async def run():
        events = []
        stream = OkxLive(env.services.cache).events("BTC/USDT", "1m")
        try:
            async for event in stream:
                events.append(event)
                if sum(e.startswith("event: candle") for e in events) == 2:
                    break
        finally:
            await stream.aclose()
        return events

    events = asyncio.run(run())
    assert events[2].startswith("event: history")
    data = [json.loads(e.splitlines()[1][6:]) for e in events if e.startswith("event: candle")]
    assert [d["candle"]["t"] for d in data] == [1_800_000, 1_860_000]
    assert sent == [{"op": "subscribe", "args": [arg]}] and closed


def test_api_stream_metadata_validation_and_cleanup(env: Env):
    class FiniteLive(OkxLive):
        async def events(self, symbol, tf):
            yield sse("status", {"state": "unavailable", "message": "test"})

    feed = FiniteLive(env.services.cache)
    env.services.live = feed
    with TestClient(create_app(env.services), base_url="http://127.0.0.1") as client:
        assert client.get("/api/markets").json()[0]["delay_seconds"] == 900
        assert client.get("/api/live", params={"symbol": "../bad"}).status_code == 422
        assert client.get("/api/live", params={"symbol": "BTC/USDT"}, headers={"Origin": "https://example.com"}).status_code == 403
        response = client.get("/api/live", params={"symbol": "BTC/USDT"})
        assert response.status_code == 200 and "event: status" in response.text
        assert feed.connections == 0
        feed.connections = feed.max_connections
        assert client.get("/api/live", params={"symbol": "BTC/USDT"}).status_code == 429


def test_polling_live_streams_moex_and_dedups(env: Env):
    from compass.live import PollingLive, poll_subscription

    env.adapter.data["SBER"] = day_candles([10, 11, 12])
    adapters = {"crypto": env.adapter}
    with pytest.raises(ValueError):
        poll_subscription(adapters, "nope", "SBER", "1d")
    with pytest.raises(ValueError):
        poll_subscription(adapters, "crypto", "../x", "1d")
    feed = PollingLive(env.services.cache, adapters, {"crypto": 0.01})

    async def run():
        out = []
        stream = feed.events("crypto", "SBER", "1d")
        try:
            async for event in stream:
                out.append(event)
                if len(out) >= 8:
                    break
        finally:
            await stream.aclose()
        return out

    events = asyncio.run(run())
    assert events[1].startswith("event: history")
    candles = [e for e in events if e.startswith("event: candle")]
    assert len(candles) == 2  # повторные опросы без изменений не дублируют свечи
    statuses = [json.loads(e.splitlines()[1][6:]) for e in events if e.startswith("event: status")]
    assert sum(s["state"] == "live" for s in statuses) >= 2  # тихий рынок — не обрыв связи
