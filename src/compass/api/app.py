"""Локальный HTTP API. Слушает только 127.0.0.1, без авторизации — это API
одного пользователя на его машине, не сервис."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from compass import __version__
from compass.cache import CandleCache
from compass.markets.base import MarketAdapter, MarketError
from compass.models import Instrument
from compass.watchlist import Watchlist


class WatchItem(BaseModel):
    market: str
    symbol: str
    name: str = ""


def create_app(
    adapters: dict[str, MarketAdapter], cache: CandleCache, watchlist: Watchlist
) -> FastAPI:
    app = FastAPI(title="Compass", version=__version__)

    def adapter(market: str) -> MarketAdapter:
        a = adapters.get(market)
        if a is None:
            raise HTTPException(404, f"Неизвестный рынок: {market}")
        return a

    @app.exception_handler(MarketError)
    def _market_error(_req, exc: MarketError):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/api/markets")
    def markets() -> list[dict]:
        return [{"id": a.id, "name": a.name, "timeframes": list(a.timeframes)} for a in adapters.values()]

    @app.get("/api/markets/{market}/search")
    def search(market: str, q: str = Query(min_length=1)) -> list[dict]:
        return [vars_(i) for i in adapter(market).search(q)]

    @app.get("/api/candles")
    def candles(
        market: str, symbol: str, tf: str = "1d", limit: int = Query(500, ge=1, le=5000)
    ) -> dict:
        adapter(market)
        res = cache.get(market, symbol, tf, limit)
        return {
            "stale": res.stale,
            "candles": [
                {"t": c.ts, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
                for c in res.candles
            ],
        }

    @app.get("/api/watchlist")
    def watchlist_list() -> list[dict]:
        return [vars_(i) for i in watchlist.list()]

    @app.post("/api/watchlist", status_code=201)
    def watchlist_add(item: WatchItem) -> dict:
        adapter(item.market)
        watchlist.add(Instrument(item.symbol, item.name or item.symbol, item.market))
        return item.model_dump()

    @app.delete("/api/watchlist/{market}/{symbol:path}", status_code=204)
    def watchlist_remove(market: str, symbol: str) -> None:
        # symbol:path — у крипты тикер содержит слэш (BTC/USDT)
        if not watchlist.remove(market, symbol):
            raise HTTPException(404, "Тикера нет в вотчлисте")

    return app


def vars_(i: Instrument) -> dict:
    return {"market": i.market, "symbol": i.symbol, "name": i.name}
