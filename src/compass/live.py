"""Публичные свечи OKX → SSE для открытого графика. Никаких ключей или заявок.

REST восстанавливает окно истории после каждого переподключения и сохраняет его
в SQLite. Промежуточные WebSocket-свечи не отмечают весь кэш как свежий и не меняют
курсор докачки: иначе при разрыве можно незаметно пропустить целые интервалы.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections.abc import AsyncIterator

import aiohttp

from compass.cache import CandleCache, CandlesResult
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS

OKX_URL = "wss://ws.okx.com:8443/ws/v5/business"
CHANNELS = {"1m": "candle1m", "5m": "candle5m", "15m": "candle15m", "1h": "candle1H", "4h": "candle4H", "1d": "candle1Dutc"}


def subscription(symbol: str, tf: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Z0-9]{1,20}/[A-Z0-9]{1,20}", symbol):
        raise ValueError("Для потока нужна спотовая пара, например BTC/USDT")
    if tf not in CHANNELS:
        raise ValueError("Таймфрейм не поддерживается потоком OKX")
    return {"channel": CHANNELS[tf], "instId": symbol.replace("/", "-")}


def candle_dto(row: list, tf: str) -> dict:
    if len(row) != 9 or str(row[8]) not in ("0", "1"):
        raise ValueError("Некорректная свеча OKX")
    ts = int(row[0])
    o, h, low, c, v = (float(x) for x in row[1:6])
    if (ts <= 0 or ts % TIMEFRAME_MS[tf] != 0
            or not all(math.isfinite(x) for x in (o, h, low, c, v))
            or min(o, h, low, c) <= 0 or v < 0
            or h < max(o, c, low) or low > min(o, c, h)):
        raise ValueError("Некорректные значения свечи OKX")
    return {"t": ts, "o": o, "h": h, "l": low, "c": c, "v": v}


def history_dto(result: CandlesResult) -> dict:
    return {
        "stale": result.stale, "source": result.source,
        "fetched_at": result.fetched_at,
        "candles": [
            {"t": c.ts, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
            for c in result.candles
        ],
    }


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False)}\n\n"


class OkxLive:
    def __init__(self, cache: CandleCache, proxy: str | None = None) -> None:
        self.cache = cache
        self.proxy = proxy
        self.connections = 0
        self.max_connections = 4

    async def events(self, symbol: str, tf: str) -> AsyncIterator[str]:
        arg = subscription(symbol, tf)
        delay = 1
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                yield sse("status", {"state": "connecting", "message": "Подключаем поток OKX…"})
                try:
                    # Подписка перед REST: во время восстановления истории события буферизуются.
                    async with session.ws_connect(OKX_URL, proxy=self.proxy, max_msg_size=256_000) as ws:
                        await ws.send_json({"op": "subscribe", "args": [arg]})
                        yield sse("status", {"state": "restoring", "message": "Восстанавливаем историю графика…"})
                        try:
                            result = await asyncio.to_thread(
                                self.cache.get, "crypto", symbol, tf, 500, refresh=True,
                            )
                            yield sse("history", history_dto(result))
                        except MarketError:
                            yield sse("history_error", {"message": "История пока недоступна. Повторим загрузку автоматически."})

                        last_candle_ts = 0
                        last_confirmed = False
                        last_data = time.monotonic()
                        while True:
                            try:
                                msg = await ws.receive(timeout=10)
                            except TimeoutError:
                                await ws.send_str("ping")
                                msg = await ws.receive(timeout=10)
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    raise ConnectionError("OKX disconnected")
                                continue
                            if time.monotonic() - last_data > 30:
                                raise ConnectionError("No candle updates")
                            if msg.data == "pong":
                                yield ": heartbeat\n\n"
                                continue
                            payload = json.loads(msg.data)
                            if not isinstance(payload, dict):
                                raise TypeError("Unexpected OKX message")
                            if payload.get("event") == "error":
                                # Не выдаём ошибку подписки за рабочее соединение и не крутим вечный retry.
                                yield sse("status", {"state": "unavailable", "message": "OKX отклонила подписку. Выберите другую спотовую пару или повторите подключение."})
                                return
                            if payload.get("arg") != arg or not payload.get("data"):
                                continue
                            for row in sorted(payload["data"], key=lambda x: int(x[0])):
                                candle = candle_dto(row, tf)
                                confirmed = str(row[8]) == "1"
                                if candle["t"] < last_candle_ts or (candle["t"] == last_candle_ts and last_confirmed):
                                    continue
                                last_candle_ts, last_confirmed = candle["t"], confirmed
                                last_data = time.monotonic()
                                delay = 1
                                yield sse("candle", {
                                    "candle": candle, "confirmed": confirmed,
                                    "received_at": int(time.time() * 1000), "source": "crypto:okx",
                                })
                except (aiohttp.ClientError, TimeoutError, ConnectionError, ValueError, KeyError, TypeError, IndexError):
                    yield sse("status", {
                        "state": "reconnecting",
                        "message": f"Поток прерван. Повторное подключение через {delay} с. История сохранена.",
                    })
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30)
