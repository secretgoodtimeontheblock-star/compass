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
from compass.markets.base import MarketAdapter, MarketError
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
                        snapshot_last = None
                        try:
                            result = await asyncio.to_thread(
                                self.cache.get, "crypto", symbol, tf, 500, refresh=True,
                            )
                            if not result.stale and result.candles:
                                snapshot_last = result.candles[-1]
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
                                # Сообщения, накопившиеся во время REST, могут быть старее
                                # снимка той же свечи. Её накопленный объём не должен откатиться.
                                if snapshot_last and candle["t"] == snapshot_last.ts and candle["v"] < snapshot_last.volume:
                                    continue
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


_POLL_SYMBOL = re.compile(r"[A-Za-z0-9._/-]{1,30}")
# Интервал опроса: у крипты котировки меняются каждую секунду, у МосБиржи бесплатный
# ISS отдаёт данные с задержкой ~15 минут, чаще спрашивать бессмысленно.
POLL_SECONDS = {"crypto": 5.0, "moex": 30.0}


def poll_subscription(adapters: dict[str, MarketAdapter], market: str, symbol: str, tf: str) -> None:
    adapter = adapters.get(market)
    if adapter is None:
        raise ValueError(f"Неизвестный рынок: {market}")
    if not _POLL_SYMBOL.fullmatch(symbol) or ".." in symbol:
        raise ValueError("Некорректный тикер")
    if tf not in adapter.timeframes:
        raise ValueError(f"Таймфрейм {tf} недоступен для рынка {market}")


class PollingLive:
    """Живой график для рынков без публичного WebSocket (МосБиржа, не-OKX биржи ccxt).

    Тот же протокол SSE, что у OkxLive, но свежие свечи берутся лёгким REST-опросом
    последних баров. Поток не выдаёт себя за тиковый: источник и задержка остаются видны."""

    def __init__(self, cache: CandleCache, adapters: dict[str, MarketAdapter], intervals: dict[str, float] | None = None) -> None:
        self.cache = cache
        self.adapters = adapters
        self.intervals = intervals or POLL_SECONDS
        self.connections = 0
        self.max_connections = 4

    def supports(self, market: str) -> bool:
        return market in self.adapters

    async def events(self, market: str, symbol: str, tf: str) -> AsyncIterator[str]:
        poll_subscription(self.adapters, market, symbol, tf)
        adapter = self.adapters[market]
        source = getattr(adapter, "source_id", market)
        interval = self.intervals.get(market, 10.0)
        tf_ms = TIMEFRAME_MS[tf]
        delay = interval
        seen: dict[int, tuple] = {}
        yield sse("status", {"state": "restoring", "message": "Загружаем историю графика…"})
        try:
            result = await asyncio.to_thread(self.cache.get, market, symbol, tf, 500, refresh=True)
            yield sse("history", history_dto(result))
        except MarketError:
            yield sse("history_error", {"message": "История пока недоступна. Повторим загрузку автоматически."})
        while True:
            try:
                rows = await asyncio.to_thread(adapter.fetch_candles, symbol, tf, None, 3)
                delay = interval
                now_ms = int(time.time() * 1000)
                for c in rows[-2:]:
                    key = (c.open, c.high, c.low, c.close, c.volume)
                    if seen.get(c.ts) == key:
                        continue
                    seen[c.ts] = key
                    yield sse("candle", {
                        "candle": {"t": c.ts, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume},
                        "confirmed": c.ts + tf_ms <= now_ms, "received_at": now_ms, "source": source,
                    })
                if not rows or len(seen) > 50:
                    seen = {c.ts: (c.open, c.high, c.low, c.close, c.volume) for c in rows[-2:]}
                yield sse("status", {
                    "state": "live" if rows else "reconnecting",
                    "message": "Источник отвечает. Проверяем свечи автоматически." if rows else "Источник не вернул свечи. Повторим запрос автоматически.",
                })
                yield ": heartbeat" + chr(10) + chr(10)
            except MarketError:
                yield sse("status", {
                    "state": "reconnecting",
                    "message": f"Источник не отвечает. Повтор через {int(delay)} с. История сохранена.",
                })
                delay = min(delay * 2, 60)
            await asyncio.sleep(delay)
