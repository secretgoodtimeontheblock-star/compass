"""Крипто-адаптер поверх ccxt. Только публичные данные — ключи не нужны и не
принимаются. Биржа и прокси задаются в настройках: доступность площадок для
резидентов РФ меняется, поэтому биржа — параметр, а не константа."""

from __future__ import annotations

import threading
import time
from typing import Any

import ccxt

from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, Candle, Instrument

_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
_PAGE = 500  # безопасный размер страницы для большинства бирж
_MAX_RESULTS = 30
_CATALOG_TTL_S = 6 * 3600


class CryptoAdapter:
    id = "crypto"
    timeframes = _TIMEFRAMES

    def __init__(
        self, exchange_id: str = "okx", proxy: str | None = None, exchange: Any = None
    ) -> None:
        """`exchange` — готовый объект (для тестов); иначе создаётся по exchange_id."""
        self.source_id = f"crypto:{exchange_id}"
        self.exchange_id = exchange_id
        self.proxy = proxy
        self.name = f"Крипта ({exchange_id})"
        self._pairs: list[str] = []
        self._pairs_at = 0.0
        self._pairs_lock = threading.Lock()
        if exchange is not None:
            self._ex = exchange
            return
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            raise MarketError(f"Неизвестная биржа ccxt: {exchange_id}")
        cfg: dict[str, Any] = {"enableRateLimit": True, "timeout": 15000}
        if proxy:
            cfg["proxies"] = {"http": proxy, "https": proxy}
        self._ex = cls(cfg)

    def fetch_candles(
        self, symbol: str, timeframe: str, since_ms: int | None, limit: int
    ) -> list[Candle]:
        if timeframe not in _TIMEFRAMES:
            raise MarketError(f"Таймфрейм {timeframe} не поддерживается для крипты")
        tf_ms = TIMEFRAME_MS[timeframe]
        cursor = since_ms if since_ms is not None else int(time.time() * 1000 - tf_ms * limit)
        out: dict[int, Candle] = {}
        try:
            while len(out) < limit:
                rows = self._ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=_PAGE)
                if not rows:
                    break
                for ts, o, h, l, c, v in rows:
                    out[int(ts)] = Candle(int(ts), float(o), float(h), float(l), float(c), float(v or 0))
                last = int(rows[-1][0])
                if last + tf_ms <= cursor or len(rows) < 2:
                    break  # биржа не двигает курсор — выходим, а не крутимся вечно
                cursor = last + tf_ms
        except ccxt.BaseError as e:
            raise MarketError(f"Биржа недоступна ({self._ex.id}): {e}") from e
        return sorted(out.values(), key=lambda c: c.ts)[-limit:]

    def _load_pairs(self) -> list[str]:
        """Все активные спот-пары биржи; кэш в памяти, при сбое — прежний список."""
        with self._pairs_lock:
            if self._pairs and time.monotonic() - self._pairs_at < _CATALOG_TTL_S:
                return self._pairs
            try:
                markets = self._ex.load_markets()
            except ccxt.BaseError as e:
                if self._pairs:
                    return self._pairs
                raise MarketError(f"Биржа недоступна ({self._ex.id}): {e}") from e
            self._pairs = sorted(
                {m["symbol"] for m in markets.values() if m.get("spot") and m.get("active") is not False}
            )
            self._pairs_at = time.monotonic()
            return self._pairs

    def search(self, query: str) -> list[Instrument]:
        """Поиск по всем парам: сначала по базовой монете, затем по подстроке.
        `btc`, `btc/usdt` и `btcusdt` находят одно и то же."""
        q = query.strip().upper()
        if not q:
            return []
        flat = q.replace("/", "").replace("-", "")
        ranked: list[tuple[int, str]] = []
        for sym in self._load_pairs():
            up = sym.upper()
            base = up.split("/")[0]
            compact = up.replace("/", "")
            if up == q or base == q:
                rank = 0
            elif base.startswith(q):
                rank = 1
            elif q in up or flat in compact:
                rank = 2
            else:
                continue
            ranked.append((rank, sym))
        ranked.sort(key=lambda t: (t[0], "/USDT" not in t[1], t[1]))
        return [Instrument(s, s, self.id) for _, s in ranked[:_MAX_RESULTS]]
