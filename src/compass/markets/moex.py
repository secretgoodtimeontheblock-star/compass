"""Адаптер Мосбиржи через открытый ISS API (без ключей).

Особенности, которые прячет адаптер:
- время в ответе — московское (UTC+3, без перевода часов с 2011 года);
- свечи приходят страницами по 500 строк, параметр `start` — смещение;
- бесплатные котировки идут с задержкой ~15 минут — для дней/недель это
  неважно, для интрадея важно (см. README).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, Candle, Instrument

ISS = "https://iss.moex.com/iss"
MSK = timezone(timedelta(hours=3))
PAGE = 500

# Площадки каталога: основной режим акций, ETF и БПИФ. Главная (TQBR) обязательна,
# остальные — по возможности: пустой или недоступный список не ломает поиск.
CATALOG_BOARDS = ("TQBR", "TQTF", "TQIF")
CATALOG_TTL_S = 6 * 3600
MAX_RESULTS = 30

# наши таймфреймы -> interval ISS (минуты; 24 = день)
_INTERVAL = {"1m": 1, "10m": 10, "1h": 60, "1d": 24}


class MoexAdapter:
    source_id = "moex:iss"
    id = "moex"
    name = "Мосбиржа (акции)"
    timeframes = tuple(_INTERVAL)

    def __init__(self, client: httpx.Client | None = None, board: str = "TQBR") -> None:
        self._client = client or httpx.Client(timeout=15.0)
        self._board = board
        self._catalog: dict[str, _Entry] = {}
        self._catalog_at = 0.0
        self._catalog_lock = threading.Lock()

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

        board = self._board_of(symbol)
        path = f"/engines/stock/markets/shares/boards/{board}/securities/{symbol}/candles.json"
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

    def _board_of(self, symbol: str) -> str:
        """Площадка тикера из каталога (если он уже загружен), иначе основная."""
        entry = self._catalog.get(symbol)
        return entry.board if entry else self._board

    def _load_catalog(self) -> dict[str, _Entry]:
        """Полный список бумаг одним запросом на площадку; кэш в памяти на несколько часов.
        При сбое источника отдаём прежний каталог, если он есть."""
        with self._catalog_lock:
            fresh = self._catalog and time.monotonic() - self._catalog_at < CATALOG_TTL_S
            if fresh:
                return self._catalog
            found: dict[str, _Entry] = {}
            try:
                for board in dict.fromkeys((self._board, *CATALOG_BOARDS)):
                    try:
                        found.update(self._fetch_board(board))
                    except MarketError:
                        if board == self._board:
                            raise
            except MarketError:
                if self._catalog:
                    return self._catalog
                raise
            self._catalog, self._catalog_at = found, time.monotonic()
            return found

    def _fetch_board(self, board: str) -> dict[str, _Entry]:
        data = self._get(
            f"/engines/stock/markets/shares/boards/{board}/securities.json",
            {"securities.columns": "SECID,SHORTNAME,SECNAME,LOTSIZE,STATUS"},
        )
        block = data.get("securities") or {}
        idx = {c: i for i, c in enumerate(block.get("columns") or [])}
        out: dict[str, _Entry] = {}
        for row in block.get("data") or []:
            try:
                if row[idx["STATUS"]] != "A":  # только торгуемые
                    continue
                out[row[idx["SECID"]]] = _Entry(
                    row[idx["SECID"]],
                    row[idx["SHORTNAME"]] or row[idx["SECID"]],
                    row[idx["SECNAME"]] or "",
                    int(row[idx["LOTSIZE"]] or 1),
                    board,
                )
            except (KeyError, IndexError, TypeError, ValueError) as e:
                raise MarketError(f"Неожиданный формат списка бумаг МосБиржи: {e}") from e
        return out

    def lot_size(self, symbol: str) -> int:
        """Размер лота: акции на МосБирже торгуются лотами (10, 100, 1000 штук)."""
        entry = self._load_catalog().get(symbol)
        if entry is None:
            raise MarketError(f"Тикер {symbol} не найден на МосБирже")
        return entry.lot

    def search(self, query: str) -> list[Instrument]:
        """Поиск по локальному каталогу: тикер (точно, затем по началу) и название."""
        q = query.strip().casefold()
        if not q:
            return []
        ranked: list[tuple[int, str, _Entry]] = []
        for e in self._load_catalog().values():
            sym, name, full = e.secid.casefold(), e.shortname.casefold(), e.secname.casefold()
            if sym == q:
                rank = 0
            elif sym.startswith(q):
                rank = 1
            elif name.startswith(q):
                rank = 2
            elif q in sym or q in name or q in full:
                rank = 3
            else:
                continue
            ranked.append((rank, e.secid, e))
        ranked.sort(key=lambda t: (t[0], t[1]))
        return [Instrument(e.secid, e.shortname, self.id) for _, _, e in ranked[:MAX_RESULTS]]


@dataclass(frozen=True, slots=True)
class _Entry:
    secid: str
    shortname: str
    secname: str
    lot: int
    board: str
