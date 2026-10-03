"""Крипто-адаптер поверх ccxt. Только публичные данные — ключи не нужны и не
принимаются. Биржа и прокси задаются в настройках: доступность площадок для
резидентов РФ меняется, поэтому биржа — параметр, а не константа."""

from __future__ import annotations

import time
from typing import Any

import ccxt

from compass.catalog import match_instruments, okx_catalog
from compass.crypto_tools import spread_from_book
from compass.markets.base import MarketError
from compass.models import TIMEFRAME_MS, Candle, Instrument, InstrumentInfo

CRYPTO_EXCHANGES = ("okx", "bybit", "binance", "kucoin", "gate")
_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
_PAGE = 500  # безопасный размер страницы для большинства бирж


class CryptoAdapter:
    id = "crypto"
    timeframes = _TIMEFRAMES

    def __init__(
        self,
        exchange_id: str = "okx",
        proxy: str | None = None,
        exchange: Any = None,
        fallback_id: str | None = None,
    ) -> None:
        """`exchange` — готовый объект (для тестов); иначе создаётся по exchange_id."""
        self.boot_exchange = exchange_id
        self.proxy = proxy
        self._injected = exchange is not None
        self._fallback: Any = None
        self.fallback_id: str | None = None
        self._ex: Any = exchange
        self.exchange_id = exchange_id
        self.source_id = f"crypto:{exchange_id}"
        self.name = f"Крипта ({exchange_id})"
        # Готовый объект биржи — в тестах. Справочник OKX только у живого адаптера,
        # иначе поиск теста смешается с полным списком пар.
        self._catalog = list(okx_catalog()) if exchange is None and exchange_id == "okx" else []
        if exchange is None:
            self._ex = self._make(exchange_id)
            if fallback_id and fallback_id != exchange_id:
                self.fallback_id = fallback_id
                self._fallback = self._make(fallback_id)

    def configure(self, exchange_id: str, fallback_id: str | None, proxy: str | None = None) -> None:
        """Смена биржи без перезапуска. В тестах с подставленной биржей ничего не делает."""
        if self._injected:
            return
        if exchange_id not in CRYPTO_EXCHANGES:
            raise MarketError(f"Биржа {exchange_id} не из списка: {', '.join(CRYPTO_EXCHANGES)}")
        if proxy is not None:
            self.proxy = proxy
        self.exchange_id = exchange_id
        self.source_id = f"crypto:{exchange_id}"
        self.name = f"Крипта ({exchange_id})"
        self._ex = self._make(exchange_id)
        self._catalog = list(okx_catalog()) if exchange_id == "okx" else []
        fb = fallback_id if fallback_id and fallback_id != exchange_id else None
        if fb and fb not in CRYPTO_EXCHANGES:
            raise MarketError(f"Запасная биржа {fb} не из списка: {', '.join(CRYPTO_EXCHANGES)}")
        self.fallback_id = fb
        self._fallback = self._make(fb) if fb else None

    def _make(self, exchange_id: str) -> Any:
        cls = getattr(ccxt, exchange_id, None)
        if cls is None:
            raise MarketError(f"Неизвестная биржа ccxt: {exchange_id}")
        cfg: dict[str, Any] = {"enableRateLimit": True, "timeout": 15000}
        if self.proxy:
            cfg["proxies"] = {"http": self.proxy, "https": self.proxy}
        return cls(cfg)

    def _promote_fallback(self) -> None:
        self._ex, self._fallback = self._fallback, self._ex
        self.exchange_id, self.fallback_id = self.fallback_id or self.exchange_id, self.exchange_id
        self.source_id = f"crypto:{self.exchange_id}"
        self.name = f"Крипта ({self.exchange_id})"
        self._catalog = list(okx_catalog()) if self.exchange_id == "okx" else []

    def fetch_candles(
        self, symbol: str, timeframe: str, since_ms: int | None, limit: int
    ) -> list[Candle]:
        if timeframe not in _TIMEFRAMES:
            raise MarketError(f"Таймфрейм {timeframe} не поддерживается для крипты")
        try:
            return self._fetch(self._ex, symbol, timeframe, since_ms, limit)
        except MarketError:
            if self._fallback is None:
                raise
            # полный последний кусок, а не «с места, где остановилась другая биржа»
            candles = self._fetch(self._fallback, symbol, timeframe, None, limit)
            self._promote_fallback()
            return candles

    def _fetch(self, ex: Any, symbol: str, timeframe: str, since_ms: int | None, limit: int) -> list[Candle]:
        tf_ms = TIMEFRAME_MS[timeframe]
        cursor = since_ms if since_ms is not None else int(time.time() * 1000 - tf_ms * limit)
        out: dict[int, Candle] = {}
        try:
            while len(out) < limit:
                rows = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=_PAGE)
                if not rows:
                    break
                for ts, o, h, l, c, v in rows:
                    out[int(ts)] = Candle(int(ts), float(o), float(h), float(l), float(c), float(v or 0))
                last = int(rows[-1][0])
                if last + tf_ms <= cursor or len(rows) < 2:
                    break  # биржа не двигает курсор — выходим, а не крутимся вечно
                cursor = last + tf_ms
        except ccxt.BaseError as e:
            raise MarketError(f"Биржа недоступна ({getattr(ex, 'id', '?')}): {e}") from e
        return sorted(out.values(), key=lambda c: c.ts)[-limit:]

    def order_book_spread(self, symbol: str) -> dict[str, float] | None:
        """Спред лучшего bid/ask в процентах от середины. Нет стакана — None, не выдуманные 0,05%."""
        book = self._book(symbol)
        return spread_from_book(book) if book else None

    def _book(self, symbol: str) -> dict | None:
        try:
            return self._ex.fetch_order_book(symbol, limit=5)
        except (ccxt.BaseError, AttributeError):
            if self._fallback is None:
                return None
            try:
                book = self._fallback.fetch_order_book(symbol, limit=5)
            except (ccxt.BaseError, AttributeError):
                return None
            self._promote_fallback()
            return book

    def instrument_info(self, symbol: str) -> InstrumentInfo:
        """Шаги и минимумы — из данных биржи (ccxt load_markets), а не из общей константы."""
        try:
            m = self._use("load_markets").get(symbol)
        except ccxt.BaseError as e:
            raise MarketError(f"Биржа недоступна ({self.exchange_id}): {e}") from e
        if not m or not m.get("spot"):
            raise MarketError(f"Спотовой пары {symbol} нет на бирже {self._ex.id}")
        mode = getattr(self._ex, "precisionMode", None)
        prec, limits = m.get("precision") or {}, m.get("limits") or {}
        return InstrumentInfo(
            symbol, self.id, self.source_id,
            qty_step=_step(prec.get("amount"), mode),
            price_step=_step(prec.get("price"), mode),
            currency=m.get("quote"),
            min_qty=_positive((limits.get("amount") or {}).get("min")),
            min_cost=_positive((limits.get("cost") or {}).get("min")),
            trading_open=m.get("active"),
        )

    def catalog_size(self) -> int:
        return len(self._catalog)

    def search(self, query: str) -> list[Instrument]:
        if self._catalog:
            return match_instruments(self._catalog, query, self.id)
        try:
            markets = self._use("load_markets")
        except ccxt.BaseError as e:
            raise MarketError(f"Биржа недоступна ({self.exchange_id}): {e}") from e
        q = query.strip().upper()
        res = [
            Instrument(m["symbol"], m["symbol"], self.id)
            for m in markets.values()
            if m.get("spot") and m.get("active", True) and q in m["symbol"].upper()
        ]
        return sorted(res, key=lambda i: i.symbol)[:20]

    def _use(self, method: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self._ex, method)
        try:
            return fn(*args, **kwargs)
        except ccxt.BaseError as e:
            if self._fallback is None:
                raise
            try:
                result = getattr(self._fallback, method)(*args, **kwargs)
            except ccxt.BaseError:
                raise e
            self._promote_fallback()
            return result


def _positive(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and v > 0 else None


def _step(v: Any, mode: Any) -> float | None:
    """ccxt отдаёт точность тремя способами; неизвестный способ — лучше None, чем неверный шаг."""
    if not isinstance(v, int | float) or v <= 0:
        return None
    if mode == ccxt.TICK_SIZE:
        return float(v)
    if mode == ccxt.DECIMAL_PLACES:
        return 10.0 ** -int(v)
    return None
