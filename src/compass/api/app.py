"""Локальный HTTP API. Слушает только 127.0.0.1 — это API одного пользователя на его
машине, не сервис. От чужих веб-страниц защищает api/guard.py."""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Callable
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
from compass.api.guard import install_guard
from compass.backtest import backtest
from compass.cache import CandleCache
from compass.data_quality import check_candles
from compass.journal import MODES, Entry, Journal
from compass.live import OkxLive, history_dto, subscription
from compass.markets.base import MarketAdapter, MarketError
from compass.models import Instrument, InstrumentInfo, closed_candles
from compass.plans import Plan, PlanStore, plan_dto, review
from compass.risk import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT, WORSE_SLIPPAGE_MULT, position_size
from compass.scheduler import BackgroundScanner
from compass.settings import Settings
from compass.signals import Signal, SignalEngine, SignalStore
from compass.strategies import STRATEGIES, candles_to_df, strategy_version
from compass.validation import (
    apply_sample_rules,
    risk_ratios,
    run_card,
    trade_resampling,
    trade_stats,
    walk_forward,
)
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
    plans: PlanStore | None = None
    now_ms: Callable[[], int] = lambda: int(time.time() * 1000)


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
    mode: str = "real"  # real | paper | historical
    plan_uid: str | None = None  # план, по которому совершена сделка


class PlanRequest(BaseModel):
    market: str
    symbol: str
    entry: float
    stop: float
    target: float | None = None
    reason: str
    signal_id: int | None = None
    strategy: str | None = None  # для ручного плана; при signal_id берётся из сигнала
    params: dict[str, int] | None = None
    capital: float | None = None
    risk_pct: float | None = None
    available: float | None = None
    fee_pct: float | None = None
    slippage_pct: float | None = None


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
    available: float | None = None  # свободные средства; по умолчанию — весь капитал
    fee_pct: float | None = None  # по умолчанию — типичная комиссия рынка
    slippage_pct: float | None = None


def create_app(svc: Services, session_token: str | None = None) -> FastAPI:
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
    install_guard(app, session_token)

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
        return {**history_dto(res), "quality": check_candles(res.candles, market, tf)}

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
        # незакрытая свеча ещё меняется: бэктест по ней показал бы результат, которого не было
        candles = closed_candles(res.candles, req.tf, svc.now_ms())
        open_dropped = len(candles) < len(res.candles)
        if len(candles) < 30:
            raise ValueError("Слишком мало истории для бэктеста (нужно хотя бы 30 закрытых свечей)")
        quality = check_candles(candles, req.market, req.tf)
        df = candles_to_df(candles)
        bt = backtest(df, strat.target(df, params), req.capital, req.fee_pct, req.slippage_pct)
        ts = df["ts"].to_numpy()
        eq = np.array([v for _, v in bt.equity])
        metrics, warnings = apply_sample_rules(
            {
                **bt.metrics,
                **risk_ratios(eq, ts, req.capital, bt.metrics["max_drawdown_pct"]),
                **trade_stats(bt.trades),
            }
        )
        if open_dropped:
            warnings.append("Последняя свеча ещё не закрыта и в расчёт не входит.")
        return {
            "strategy": strat.id,
            "params": params,
            "stale": res.stale,
            "metrics": metrics,
            "data_quality": quality,
            "warnings": warnings,
            "coverage": {
                "requested": req.limit,
                "candles": len(candles),
                "first_ts": int(df["ts"].iloc[0]),
                "last_ts": int(df["ts"].iloc[-1]),
                # получили меньше запрошенного — у источника больше нет; ровно столько — раньше история могла быть
                "exhausted": len(res.candles) < req.limit,
            },
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
                strategy_version=strategy_version(strat, params),
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

    def instrument_info(market: str, symbol: str) -> InstrumentInfo:
        a = adapter(market)
        fn = getattr(a, "instrument_info", None)
        source = getattr(a, "source_id", market)
        if fn is None:
            return InstrumentInfo(symbol, market, source, complete=False)
        try:
            return fn(symbol)
        except MarketError:
            if market == "moex":
                raise  # неизвестный тикер или ISS недоступен без справочника — гадать лот нельзя
            return InstrumentInfo(symbol, market, source, qty_step=CRYPTO_QTY_STEP, complete=False)

    @app.get("/api/instrument")
    def instrument(market: str, symbol: str) -> dict:
        return asdict(instrument_info(market, symbol))

    def risk_calc(req: RiskRequest) -> dict:
        info = instrument_info(req.market, req.symbol)
        cfg = svc.settings.all()
        fee = req.fee_pct if req.fee_pct is not None else DEFAULT_FEE_PCT.get(req.market, 0.1)
        slip = req.slippage_pct if req.slippage_pct is not None else DEFAULT_SLIPPAGE_PCT
        bond = info.price_unit == "percent_of_face"
        if bond and not info.face_value:
            raise ValueError("Для облигации не получен номинал: посчитать размер позиции нельзя")
        is_crypto = req.market == "crypto"
        p = position_size(
            req.capital if req.capital is not None else cfg["capital"],
            req.risk_pct if req.risk_pct is not None else cfg["risk_pct"],
            req.entry,
            req.stop,
            lot=info.lot,
            qty_step=(info.qty_step or CRYPTO_QTY_STEP) if is_crypto else None,
            fee_pct=fee,
            slippage_pct=slip,
            available=req.available,
            unit_value=info.face_value / 100 if bond and info.face_value else 1.0,
            accrued=info.accrued or 0.0,
            min_qty=info.min_qty,
            min_cost=info.min_cost,
        )
        warnings = [w for w in (p.warning,) if w]
        if not info.complete:
            warnings.append(
                "Параметры инструмента получены не полностью"
                + (f": шаг количества взят {CRYPTO_QTY_STEP:g} по умолчанию" if is_crypto else "")
                + ". Проверьте лот, шаг цены и минимальную заявку у брокера."
            )
        if info.trading_open is False:
            warnings.append("По данным биржи торги по инструменту сейчас не идут: цена может быть вчерашней.")
        for label, price in (("входа", req.entry), ("стопа", req.stop)):
            if info.price_step and abs(price / info.price_step - round(price / info.price_step)) > 1e-6:
                warnings.append(f"Цена {label} не кратна шагу цены {info.price_step:g}: биржа её не примет.")
        if bond:
            warnings.append(
                "Облигация: цена в % от номинала; накопленный купонный доход входит в стоимость покупки, "
                "но не в расчёт потери при стопе."
            )
        return {
            **asdict(p),
            "lot_size": info.lot,
            "currency": info.currency,
            "unit_value": info.face_value / 100 if bond and info.face_value else 1.0,
            "fee_pct": fee,
            "slippage_pct": slip,
            "warnings": warnings,
            "assumptions": [
                "Потеря при стопе — расчётный сценарий, а не гарантированный максимум: цена может пройти стоп гэпом.",
                (
                    f"Считается вход и выход по стопу с комиссией {fee:g}% и проскальзыванием {slip:g}% "
                    f"с каждой стороны; ухудшенное исполнение — проскальзывание ×{WORSE_SLIPPAGE_MULT:g}."
                ),
                "Приложение ничего не исполняет и не блокирует: лимит — предупреждение.",
            ],
        }

    @app.post("/api/risk")
    def risk(req: RiskRequest) -> dict:
        return risk_calc(req)

    # --- планы сделок ---

    def plan_store() -> PlanStore:
        if svc.plans is None:
            raise HTTPException(503, "Хранилище планов недоступно")
        return svc.plans

    @app.post("/api/plans", status_code=201)
    def plan_create(req: PlanRequest) -> dict:
        store = plan_store()
        strategy_id, params, version, notes = req.strategy, req.params, None, []
        if req.signal_id is not None:
            sig = svc.signals.get(req.signal_id)
            if sig is None:
                raise HTTPException(404, "Сигнал не найден")
            if (sig.market, sig.symbol) != (req.market, req.symbol):
                raise ValueError("Сигнал относится к другому инструменту")
            strategy_id, params, version = sig.strategy, sig.params, sig.strategy_version
            if params is None:
                notes.append("Параметры сигнала не сохранены (сигнал создан старой версией): версия стратегии неизвестна.")
        if strategy_id is not None:
            strat = STRATEGIES.get(strategy_id)
            if strat is None:
                raise HTTPException(404, f"Неизвестная стратегия: {strategy_id}")
            if params is not None:
                params = strat.resolve(params)
                if req.signal_id is None:  # у сигнала версия зафиксирована в момент его рождения: пересчитывать её нельзя
                    version = strategy_version(strat, params)
        elif params is not None:
            raise ValueError("Параметры без стратегии не имеют смысла")
        r = risk_calc(
            RiskRequest(
                market=req.market, symbol=req.symbol, entry=req.entry, stop=req.stop, capital=req.capital,
                risk_pct=req.risk_pct, available=req.available, fee_pct=req.fee_pct, slippage_pct=req.slippage_pct,
            )
        )
        if r["qty"] <= 0:
            raise ValueError(r["warnings"][0] if r["warnings"] else "Покупать нечего: количество нулевое")
        cfg = svc.settings.all()
        reward_risk = None
        if req.target is not None and req.entry > req.stop:
            reward_risk = round((req.target - req.entry) / (req.entry - req.stop), 2)
        plan = store.add(
            Plan(
                market=req.market, symbol=req.symbol, source=getattr(adapter(req.market), "source_id", req.market),
                entry=req.entry, stop=req.stop, target=req.target, qty=r["qty"], lots=r["lots"],
                capital=req.capital if req.capital is not None else cfg["capital"],
                risk_pct=req.risk_pct if req.risk_pct is not None else cfg["risk_pct"],
                fee_pct=r["fee_pct"], slippage_pct=r["slippage_pct"], cost=r["cost"], risk_amount=r["risk_amount"],
                risk_amount_worse=r["risk_amount_worse"], budget=r["budget"], strategy=strategy_id,
                strategy_version=version, params=params, signal_id=req.signal_id, available=req.available,
                currency=r["currency"], unit_value=r["unit_value"], reward_risk=reward_risk,
                reason=req.reason.strip(), warnings=(*r["warnings"], *notes),
            )
        )
        return {**plan_dto(plan), "assumptions": r["assumptions"]}

    @app.get("/api/plans")
    def plan_list(market: str | None = None, symbol: str | None = None, limit: int = Query(100, ge=1, le=500)) -> list[dict]:
        return [plan_dto(p) for p in plan_store().list(market, symbol, limit)]

    @app.get("/api/plans/{plan_id}")
    def plan_get(plan_id: int) -> dict:
        p = plan_store().get(plan_id)
        if p is None:
            raise HTTPException(404, "План не найден")
        return plan_dto(p)

    @app.get("/api/plans/{plan_id}/review")
    def plan_review(plan_id: int) -> dict:
        p = plan_store().get(plan_id)
        if p is None:
            raise HTTPException(404, "План не найден")
        return review(p, svc.journal.list(p.market, p.symbol, mode=None))

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

    @app.get("/api/journal/backup.json")
    def journal_backup() -> Response:
        return JSONResponse(
            svc.journal.backup(svc.plans),
            headers={"Content-Disposition": 'attachment; filename="compass-journal-backup.json"'},
        )

    @app.post("/api/journal/restore")
    def journal_restore(payload: dict) -> dict:
        return svc.journal.restore_backup(payload, svc.plans)

    @app.get("/api/journal/deleted")
    def journal_deleted() -> list[dict]:
        return [asdict(e) for e in svc.journal.deleted()]

    @app.post("/api/journal/{entry_id}/restore")
    def journal_undelete(entry_id: int) -> dict:
        if not svc.journal.restore(entry_id):
            raise HTTPException(404, "Удалённой записи с таким номером нет")
        return {"restored": entry_id}

    def _mode(mode: str) -> str | None:
        if mode == "all":
            return None
        if mode not in MODES:
            raise ValueError("Режим — real, paper, historical или all")
        return mode

    @app.get("/api/journal")
    def journal_list(market: str | None = None, symbol: str | None = None, mode: str = "real") -> list[dict]:
        return [asdict(e) for e in svc.journal.list(market, symbol, _mode(mode))]

    @app.get("/api/journal/positions")
    def journal_positions(mode: str = "real") -> list[dict]:
        return [asdict(p) for p in svc.journal.positions(_mode(mode))]

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
