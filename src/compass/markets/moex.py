"""Адаптер Мосбиржи через открытый ISS API (без ключей).

Особенности, которые прячет адаптер:
- время в ответе — московское (UTC+3, без перевода часов с 2011 года);
- свечи приходят страницами по 500 строк, параметр `start` — смещение;
- бесплатные котировки идут с задержкой ~15 минут — для дней/недель это
  неважно, для интрадея важно (см. README).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, Candle, Instrument

ISS = "https://iss.moex.com/iss"
MSK = timezone(timedelta(hours=3))
PAGE = 500

# наши таймфреймы -> interval ISS (минуты; 24 = день)
_INTERVAL = {"1m": 1, "10m": 10, "1h": 60, "1d": 24}


class MoexAdapter:
    id = "moex"
    name = "Мосбиржа (акции)"
    timeframes = tuple(_INTERVAL)

    def __init__(self, client: httpx.Client | None = None, board: str = "TQBR") -> None:
        self._client = client or httpx.Client(timeout=15.0)
        self._board = board

    def _get(self, path: str, params: dict) -> dict:
        try:
            r = self._client.get(f"{ISS}{path}", params={"iss.meta": "off", **params})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise MarketError(f"МосБиржа ISS недоступна: {e}") from e

    def fetch_candles(
        self, symbol: str, timeframe: str, since_ms: int | None, limit: int
    ) -> list[Candle]:
        if timeframe not in _INTERVAL:
            raise MarketError(f"Таймфрейм {timeframe} не поддерживается для МосБиржи")
        tf_ms = TIMEFRAME_MS[timeframe]
        if since_ms is None:
            # торговых свечей меньше, чем календарных: выходные и ночь
            span = tf_ms * limit * (1.6 if timeframe == "1d" else 5)
            since_ms = int(time.time() * 1000 - span)
        start_dt = datetime.fromtimestamp(since_ms / 1000, MSK)

        path = f"/engines/stock/markets/shares/boards/{self._board}/securities/{symbol}/candles.json"
        out: list[Candle] = []
        offset = 0
        while True:
            data = self._get(
                path,
                {
                    "interval": _INTERVAL[timeframe],
                    "from": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "start": offset,
                },
            )
            block = data.get("candles") or {}
            cols = block.get("columns") or []
            rows = block.get("data") or []
            idx = {c: i for i, c in enumerate(cols)}
            for row in rows:
                try:
                    # ISS отдаёт московское время без указания пояса
                    begin = datetime.strptime(row[idx["begin"]] + " +0300", "%Y-%m-%d %H:%M:%S %z")
                    out.append(
                        Candle(
                            ts=int(begin.timestamp() * 1000),
                            open=float(row[idx["open"]]),
                            high=float(row[idx["high"]]),
                            low=float(row[idx["low"]]),
                            close=float(row[idx["close"]]),
                            volume=float(row[idx["volume"]]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as e:
                    raise MarketError(f"Неожиданный формат свечи МосБиржи: {e}") from e
            if len(rows) < PAGE:
                break
            offset += len(rows)
        return out[-limit:]

    def search(self, query: str) -> list[Instrument]:
        data = self._get(
            "/securities.json",
            {"q": query, "limit": 20, "securities.columns": "secid,shortname,is_traded,group"},
        )
        block = data.get("securities") or {}
        idx = {c: i for i, c in enumerate(block.get("columns") or [])}
        res: list[Instrument] = []
        for row in block.get("data") or []:
            if row[idx["group"]] == "stock_shares" and row[idx["is_traded"]] == 1:
                res.append(Instrument(row[idx["secid"]], row[idx["shortname"]], self.id))
        return res
