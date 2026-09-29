"""Локальный HTTP API. Слушает только 127.0.0.1, без авторизации — это API
одного пользователя на его машине, не сервис."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from compass import __version__
from compass.ai.service import AiService
from compass.api.ai_routes import register_ai_routes
from compass.backtest import backtest
from compass.cache import CandleCache
from compass.journal import Entry, Journal
from compass.live import OkxLive, history_dto, subscription
from compass.markets.base import MarketAdapter, MarketError
from compass.models import Instrument
from compass.risk import position_size
from compass.scheduler import BackgroundScanner
from compass.settings import Settings
from compass.signals import Signal, SignalEngine, SignalStore
from compass.strategies import STRATEGIES, candles_to_df
from compass.validation import risk_ratios, run_card, trade_resampling, trade_stats, walk_forward
from compass.watchlist import Watchlist

CRYPTO_QTY_STEP = 1e-6


def static_dir() -> Path:
    """В установленной сборке интерфейс лежит рядом с распакованным exe, не в исходниках."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "static"
    return Path(__file__).resolve().parent.parent / "static"


STATIC_DIR = static_dir()


@dataclass
class Services:
    adapters: dict[str, MarketAdapter]
    cache: CandleCache
    watchlist: Watchlist
    settings: Settings
    signals: SignalStore
    engine: SignalEngine
    journal: Journal
    ai: AiService
    scanner: BackgroundScanner | None = None  # None — фонового скана нет (тесты)
    live: OkxLive | None = None


class JournalRequest(BaseModel):
    market: str
    symbol: str
    side: str
    qty: float
    price: float
    ts: int  # мс UTC
    fee: float = 0.0
    note: str = ""
    signal_id: int | None = None
    planned_stop: float | None = None
    reason: str = ""


class WatchItem(BaseModel):
    market: str
    symbol: str
    name: str = ""


class BacktestRequest(BaseModel):
    market: str
    symbol: str
    tf: str = "1d"
    strategy: str
    params: dict[str, int] = Field(default_factory=dict)
    limit: int = Field(1000, ge=10, le=5000)
    capital: float = 100_000.0
    fee_pct: float = 0.05
    slippage_pct: float = 0.05


class RiskRequest(BaseModel):
    market: str
    symbol: str
    entry: float
    stop: float
    capital: float | None = None  # по умолчанию — из настроек
    risk_pct: float | None = None


def create_app(svc: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if svc.scanner:
            svc.scanner.start()
        try:
            yield
        finally:
            if svc.scanner:
                svc.scanner.stop()

    app = FastAPI(title="Compass", version=__version__, lifespan=lifespan)

    def adapter(market: str) -> MarketAdapter:
        a = svc.adapters.get(market)
        if a is None:
            raise HTTPException(404, f"Неизвестный рынок: {market}")
        return a

    @app.exception_handler(MarketError)
    def _market_error(_req, exc: MarketError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(ValueError)
    def _value_error(_req, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    # --- рынки, свечи, вотчлист ---

    @app.get("/api/markets")
    def markets() -> list[dict]:
        return [
            {
                "id": a.id, "name": a.name, "timeframes": list(a.timeframes),
                "source": getattr(a, "source_id", a.id),
                "delay_seconds": 900 if a.id == "moex" else None,
                "live_supported": a.id == "crypto" and svc.live is not None,
                "instruments": getattr(a, "catalog_size", lambda: 0)(),
            }
            for a in svc.adapters.values()
        ]

    @app.get("/api/markets/{market}/search")
    def search(market: str, q: str = Query(min_length=1)) -> list[dict]:
        return [asdict(i) for i in adapter(market).search(q)]

    @app.get("/api/candles")
    def candles(
        market: str, symbol: str, tf: str = "1d", limit: int = Query(500, ge=1, le=5000)
    ) -> dict:
        adapter(market)
        res = svc.cache.get(market, symbol, tf, limit)
        return history_dto(res)

    @app.get("/api/live")
    async def live_candles(request: Request, symbol: str, tf: str = "1d"):
        if svc.live is None:
            raise HTTPException(409, "Публичный поток доступен только для OKX")
        subscription(symbol, tf)
        # Локальный endpoint не должен открывать потоки по запросу стороннего сайта.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Запрос должен идти из Compass")
        if svc.live.connections >= svc.live.max_connections:
            raise HTTPException(429, "Слишком много открытых графиков с потоком")

        async def stream():
            svc.live.connections += 1
            try:
                async for event in svc.live.events(symbol, tf):
                    yield event
            finally:
                svc.live.connections -= 1

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
        })

    @app.get("/api/watchlist")
    def watchlist_list() -> list[dict]:
        return [asdict(i) for i in svc.watchlist.list()]

    @app.post("/api/watchlist", status_code=201)
    def watchlist_add(item: WatchItem) -> dict:
        adapter(item.market)
        svc.watchlist.add(Instrument(item.symbol, item.name or item.symbol, item.market))
        return item.model_dump()

    @app.delete("/api/watchlist/{market}/{symbol:path}", status_code=204)
    def watchlist_remove(market: str, symbol: str) -> None:
        # symbol:path — у крипты тикер содержит слэш (BTC/USDT)
        if not svc.watchlist.remove(market, symbol):
            raise HTTPException(404, "Тикера нет в вотчлисте")

    # --- стратегии и бэктест ---

    @app.get("/api/strategies")
    def strategies() -> list[dict]:
        return [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "params": [asdict(p) for p in s.params],
            }
            for s in STRATEGIES.values()
        ]

    @app.post("/api/backtest")
    def run_backtest(req: BacktestRequest) -> dict:
        adapter(req.market)
        strat = STRATEGIES.get(req.strategy)
        if strat is None:
            raise HTTPException(404, f"Неизвестная стратегия: {req.strategy}")
        params = strat.resolve(req.params)
        res = svc.cache.get(req.market, req.symbol, req.tf, req.limit)
        if len(res.candles) < 30:
            raise ValueError("Слишком мало истории для бэктеста (нужно хотя бы 30 свечей)")
        df = candles_to_df(res.candles)
        bt = backtest(df, strat.target(df, params), req.capital, req.fee_pct, req.slippage_pct)
        ts = df["ts"].to_numpy()
        eq = np.array([v for _, v in bt.equity])
        metrics = {
            **bt.metrics,
            **risk_ratios(eq, ts, req.capital, bt.metrics["max_drawdown_pct"]),
            **trade_stats(bt.trades),
        }
        return {
            "strategy": strat.id,
            "params": params,
            "stale": res.stale,
            "metrics": metrics,
            "validation": {
                "walk_forward": walk_forward(df, bt, req.capital),
                "resampling": trade_resampling(bt.trades),
            },
            "run_card": run_card(
                df,
                market=req.market,
                symbol=req.symbol,
                tf=req.tf,
                strategy=strat.id,
                params=params,
                capital=req.capital,
                fee_pct=req.fee_pct,
                slippage_pct=req.slippage_pct,
                stale=res.stale,
            ),
            "trades": [asdict(t) for t in bt.trades],
            "equity": [{"t": t, "v": round(v, 2)} for t, v in bt.equity],
        }

    # --- сигналы ---

    @app.get("/api/signals")
    def signals_list(
        limit: int = Query(100, ge=1, le=500),
        unseen: bool = False,
        market: str | None = None,
        symbol: str | None = None,
    ) -> list[dict]:
        return [_signal_dto(s) for s in svc.signals.list(limit, unseen, market, symbol)]

    @app.post("/api/signals/seen")
    def signals_seen() -> dict:
        return {"marked": svc.signals.mark_all_seen()}

    @app.post("/api/scan")
    def scan_now() -> dict:
        r = svc.engine.scan()
        return {"new": [_signal_dto(s) for s in r.new], "errors": r.errors}

    # --- риск и настройки ---

    @app.post("/api/risk")
    def risk(req: RiskRequest) -> dict:
        a = adapter(req.market)
        cfg = svc.settings.all()
        lot_fn = getattr(a, "lot_size", None)
        lot = lot_fn(req.symbol) if lot_fn else 1
        p = position_size(
            req.capital if req.capital is not None else cfg["capital"],
            req.risk_pct if req.risk_pct is not None else cfg["risk_pct"],
            req.entry,
            req.stop,
            lot=lot,
            qty_step=None if lot_fn else CRYPTO_QTY_STEP,
        )
        return {**asdict(p), "lot_size": lot}

    @app.get("/api/settings")
    def settings_get() -> dict:
        return svc.settings.all()

    @app.put("/api/settings")
    def settings_put(changes: dict) -> dict:
        return svc.settings.update(changes)

    # --- журнал сделок ---

    @app.get("/api/journal.csv")
    def journal_csv() -> Response:
        return Response(
            content=svc.journal.to_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="compass-journal.csv"'},
        )

    @app.get("/api/journal")
    def journal_list(market: str | None = None, symbol: str | None = None) -> list[dict]:
        return [asdict(e) for e in svc.journal.list(market, symbol)]

    @app.get("/api/journal/positions")
    def journal_positions() -> list[dict]:
        return [asdict(p) for p in svc.journal.positions()]

    @app.post("/api/journal", status_code=201)
    def journal_add(req: JournalRequest) -> dict:
        adapter(req.market)
        return asdict(svc.journal.add(Entry(**req.model_dump())))

    @app.delete("/api/journal/{entry_id}", status_code=204)
    def journal_remove(entry_id: int) -> None:
        if not svc.journal.remove(entry_id):
            raise HTTPException(404, "Записи нет в журнале")

    register_ai_routes(app, svc)

    # Интерфейс — последним: маршруты /api/* должны матчиться раньше статики.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


def _signal_dto(s: Signal) -> dict:
    return asdict(s)
