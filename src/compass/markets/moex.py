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

from compass.catalog import match_instruments, moex_catalog
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, Candle, Instrument, InstrumentInfo

ISS = "https://iss.moex.com/iss"
MSK = timezone(timedelta(hours=3))
PAGE = 500

# наши таймфреймы -> interval ISS (минуты; 24 = день)
_INTERVAL = {"1m": 1, "10m": 10, "1h": 60, "1d": 24}


class MoexAdapter:
    source_id = "moex:iss"
    id = "moex"
    name = "Мосбиржа (акции)"
    timeframes = tuple(_INTERVAL)

    def __init__(
        self,
        client: httpx.Client | None = None,
        board: str = "TQBR",
        catalog: list[dict] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=15.0)
        self._board = board
        rows = moex_catalog() if catalog is None else catalog
        self._catalog = {str(row["symbol"]): row for row in rows}

    def catalog_size(self) -> int:
        return len(self._catalog)

    def _locate(self, symbol: str) -> tuple[str, str, str]:
        row = self._catalog.get(symbol)
        if row:
            return row["engine"], row["market"], row["board"]
        return "stock", "shares", self._board

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

        engine, market, board = self._locate(symbol)
        path = f"/engines/{engine}/markets/{market}/boards/{board}/securities/{symbol}/candles.json"
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
                            # ISS может отдавать цены без объёма (например, валюты).
                            # В числовом ряде это 0; check_candles явно предупреждает,
                            # что нулевой/непереданный объём не доказывает редкие торги.
                            volume=0.0 if row[idx["volume"]] is None else float(row[idx["volume"]]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as e:
                    raise MarketError("МосБиржа вернула неполные или некорректные данные свечей. Попробуйте другой период или повторите запрос позже.") from e
            if len(rows) < PAGE:
                break
            offset += len(rows)
        return out[-limit:]

    def lot_size(self, symbol: str) -> int:
        """Размер лота: акции на МосБирже торгуются лотами (10, 100, 1000 штук)."""
        row = self._catalog.get(symbol)
        if row and row.get("lot"):
            return int(row["lot"])
        engine, market, board = self._locate(symbol)
        data = self._get(
            f"/engines/{engine}/markets/{market}/boards/{board}/securities/{symbol}.json",
            {"securities.columns": "SECID,LOTSIZE"},
        )
        block = data.get("securities") or {}
        rows = block.get("data") or []
        if not rows:
            raise MarketError(f"Тикер {symbol} не найден на МосБирже")
        return int(rows[0][block["columns"].index("LOTSIZE")])

    def instrument_info(self, symbol: str) -> InstrumentInfo:
        """Лот, шаг цены, валюта, номинал/НКД облигаций и режим торгов — из ISS.
        Если ISS недоступен, а лот есть в справочнике, возвращаем только лот с complete=False."""
        row = self._catalog.get(symbol)
        engine, market, board = self._locate(symbol)
        try:
            data = self._get(f"/engines/{engine}/markets/{market}/boards/{board}/securities/{symbol}.json", {})
        except MarketError:
            if row and row.get("lot"):
                return InstrumentInfo(symbol, self.id, self.source_id, lot=int(row["lot"]), complete=False)
            raise
        sec = _first_row(data.get("securities"))
        if sec is None:
            raise MarketError(f"Тикер {symbol} не найден на МосБирже")
        md = _first_row(data.get("marketdata")) or {}
        status = md.get("TRADINGSTATUS")
        is_bond = market == "bonds" or (row or {}).get("kind") == "bond"
        currency = sec.get("CURRENCYID") or sec.get("FACEUNIT")
        return InstrumentInfo(
            symbol, self.id, self.source_id,
            lot=int(sec.get("LOTSIZE") or (row or {}).get("lot") or 1),
            price_step=_num(sec.get("MINSTEP")),
            currency={"SUR": "RUB", "SUR ": "RUB"}.get(currency, currency),
            face_value=_num(sec.get("FACEVALUE")) if is_bond else None,
            accrued=_num(sec.get("ACCRUEDINT")) if is_bond else None,
            price_unit="percent_of_face" if is_bond else "money",
            trading_open=None if status is None else status == "T",
        )

    def search(self, query: str) -> list[Instrument]:
        if self._catalog:
            return match_instruments(self._catalog.values(), query, self.id)
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


def _first_row(block: dict | None) -> dict | None:
    if not block or not block.get("data"):
        return None
    return dict(zip(block["columns"], block["data"][0], strict=False))


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
