"""Локальный HTTP API. Слушает только 127.0.0.1 — это API одного пользователя на его
машине, не сервис. От чужих веб-страниц защищает api/guard.py."""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from compass import __version__, oos, portfolio
from compass import review_week as review_week_mod
from compass import screener as screener_mod
from compass.accounts import MARKET_TZ, Account, AccountStore
from compass.ai.service import AiService
from compass.api.ai_routes import register_ai_routes
from compass.api.guard import install_guard
from compass.api.schemas import BacktestRequest, LabRequest, ValidateRequest
from compass.api.replay_routes import register_replay_routes
from compass.backtest import Rules, backtest
from compass.cache import CandleCache
from compass.data_quality import check_candles
from compass.experiments import Experiment, ExperimentLog, multiple_testing_warning
from compass.journal import MODES, Entry, Journal
from compass.levels import Level, LevelStore
from compass.live import OkxLive, PollingLive, history_dto, poll_subscription, subscription
from compass.markets.base import MarketAdapter, MarketError
from compass.models import (
    INTRADAY_TFS,
    TIMEFRAME_MS,
    Instrument,
    InstrumentInfo,
    closed_candles,
    feed_delay_s,
)
from compass.plans import Plan, PlanStore, plan_dto, review
from compass.replay import ReplayStore
from compass import execution
from compass import cockpit as cockpit_mod
from compass import discipline, glossary
from compass import presets as presets_mod
from compass import lab as lab_mod
from compass.integrity import integrity_report
from compass.risk import WORSE_SLIPPAGE_MULT, position_size
from compass.scheduler import BackgroundScanner
from compass.settings import Settings
from compass.signals import (
    Signal,
    SignalEngine,
    SignalStore,
    _chosen_strategies,
    in_quiet_hours,
    signal_expires_at,
    signal_status,
)
from compass.strategies import STRATEGIES, candles_to_df, strategy_version
from compass.validation import (
    apply_sample_rules,
    cost_stress_warning,
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
    polling: PollingLive | None = None  # опрос для рынков без WebSocket
    plans: PlanStore | None = None
    experiments: ExperimentLog | None = None
    accounts: AccountStore | None = None
    replay: ReplayStore | None = None
    levels: LevelStore | None = None
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
    spread_pct: float | None = None  # по умолчанию — спред профиля рынка


class LevelRequest(BaseModel):
    market: str
    symbol: str
    price: float = Field(gt=0)
    label: str = Field("", max_length=60)


class WatchItem(BaseModel):
    market: str
    symbol: str
    name: str = ""


class ReconcileRequest(BaseModel):
    market: str
    positions: list[dict] = Field(max_length=500)  # [{symbol, qty, avg_price?}] из выписки брокера


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
    spread_pct: float | None = None  # по умолчанию — спред профиля рынка


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
                "live_supported": _live_for(a.id) is not None,
                "live_kind": ("stream" if a.id == "crypto" and svc.live is not None else "poll") if _live_for(a.id) is not None else None,
                "instruments": getattr(a, "catalog_size", lambda: 0)(),
                "profile": execution.profile(a.id).dto(),
            }
            for a in svc.adapters.values()
        ]

    @app.get("/api/markets/{market}/search")
    def search(market: str, q: str = Query(min_length=1)) -> list[dict]:
        return [asdict(i) for i in adapter(market).search(q)]

    @app.get("/api/candles")
    def candles(
        market: str, symbol: str, tf: str = "1d", limit: int = Query(500, ge=1, le=20000)
    ) -> dict:
        adapter(market)
        res = svc.cache.get(market, symbol, tf, limit)
        return {**history_dto(res), "quality": check_candles(res.candles, market, tf)}

    def _live_for(market: str):
        if market == "crypto" and svc.live is not None:
            return svc.live
        return svc.polling if svc.polling is not None and svc.polling.supports(market) else None

    @app.get("/api/live")
    async def live_candles(request: Request, symbol: str, tf: str = "1d", market: str = "crypto"):
        feed = _live_for(market)
        if feed is None:
            raise HTTPException(409, "Для этого рынка живое обновление недоступно")
        if feed is svc.live:
            subscription(symbol, tf)
        else:
            poll_subscription(svc.adapters, market, symbol, tf)
        # Локальный endpoint не должен открывать потоки по запросу стороннего сайта.
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Запрос должен идти из Compass")
        if feed.connections >= feed.max_connections:
            raise HTTPException(429, "Слишком много открытых графиков с потоком")

        async def stream():
            feed.connections += 1
            try:
                source = feed.events(symbol, tf) if feed is svc.live else feed.events(market, symbol, tf)
                async with aclosing(source):
                    async for event in source:
                        yield event
            finally:
                feed.connections -= 1

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

    @app.get("/api/presets")
    def presets_list(market: str) -> list[dict]:
        """Готовые наборы по темам (сектора акций, категории монет); в ответ попадают только тикеры из справочника биржи."""
        adapter(market)
        have = {i.symbol for i in svc.watchlist.list() if i.market == market}
        out = presets_mod.groups(market)
        for g in out:
            for i in g["instruments"]:
                i["watched"] = i["symbol"] in have
        return out

    @app.post("/api/presets/{market}/{group_id}/add")
    def presets_add(market: str, group_id: str) -> dict:
        """Добавляет в избранное все тикеры набора, которых там ещё нет."""
        adapter(market)
        group = presets_mod.find(market, group_id)
        if group is None:
            raise HTTPException(404, "Такого набора нет")
        have = {i.symbol for i in svc.watchlist.list() if i.market == market}
        added = 0
        for i in group["instruments"]:
            if i["symbol"] not in have:
                svc.watchlist.add(Instrument(i["symbol"], i["name"], market, i["kind"]))
                added += 1
        return {"added": added, "already": len(group["instruments"]) - added, "title": group["title"]}

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

    def load_series(req: BacktestRequest, min_candles: int = 30):
        """Закрытые свечи для расчёта: незакрытая ещё меняется, расчёт по ней показал бы то, чего не было."""
        res = svc.cache.get(req.market, req.symbol, req.tf, req.limit)
        candles = closed_candles(res.candles, req.tf, svc.now_ms())
        if len(candles) < min_candles:
            raise ValueError(f"Слишком мало истории (нужно хотя бы {min_candles} закрытых свечей)")
        return res, candles_to_df(candles), len(candles) < len(res.candles), check_candles(candles, req.market, req.tf)

    def make_rules(req: BacktestRequest) -> Rules | None:
        if req.stop_atr_mult is None and req.target_r is None and req.risk_pct is None and not req.close_eod:
            return None
        tz_ms = int(MARKET_TZ[req.market].utcoffset(None).total_seconds() * 1000) if req.market in MARKET_TZ else 0
        Rules(stop_atr_mult=req.stop_atr_mult, target_r=req.target_r, risk_pct=req.risk_pct, close_eod=req.close_eod,
              tz_offset_ms=tz_ms).check()
        if req.stop_atr_mult is None and req.risk_pct is None:
            return Rules(close_eod=req.close_eod, tz_offset_ms=tz_ms)
        info = instrument_info(req.market, req.symbol)
        bond = info.price_unit == "percent_of_face"
        if bond and not info.face_value:
            raise ValueError("Для облигации не получен номинал: посчитать размер по риску нельзя")
        return Rules(
            stop_atr_mult=req.stop_atr_mult, target_r=req.target_r, risk_pct=req.risk_pct, lot=info.lot,
            qty_step=(info.qty_step or CRYPTO_QTY_STEP) if req.market == "crypto" else None,
            unit_value=info.face_value / 100 if bond and info.face_value else 1.0,
            close_eod=req.close_eod, tz_offset_ms=tz_ms,
        )

    def rules_dict(rules: Rules | None) -> dict | None:
        if rules is None:
            return None
        return {
            "stop_atr_mult": rules.stop_atr_mult, "target_r": rules.target_r, "risk_pct": rules.risk_pct,
            "atr_period": rules.atr_period, "lot": rules.lot, "qty_step": rules.qty_step, "unit_value": rules.unit_value,
            "close_eod": rules.close_eod,
        }

    def exec_model(req: BacktestRequest) -> execution.ExecutionModel:
        """Допущения исполнения запроса: комиссия и проскальзывание как заданы, спред — из профиля рынка."""
        return execution.model_for(req.market, req.fee_pct, req.slippage_pct, req.spread_pct, req.max_volume_pct)

    def exec_kw(model: execution.ExecutionModel) -> dict:
        return {"spread_pct": model.spread_pct, "max_participation_pct": model.max_participation_pct}

    def cost_stress(df, target, req, rules, bt, model) -> list[dict]:
        """Тот же результат при расходах ×1, ×2, ×3 (комиссия, проскальзывание и спред растут вместе)."""
        stress = []
        for mult in (1, 2, 3):
            m = model.worse(mult) if mult > 1 else model
            r = bt if mult == 1 else backtest(df, target, req.capital, m.fee_pct, m.slippage_pct, rules, **exec_kw(m))
            stress.append({"multiplier": mult, "fee_pct": round(m.fee_pct, 4), "slippage_pct": round(m.slippage_pct, 4),
                           "spread_pct": round(m.spread_pct, 4),
                           "return_pct": r.metrics["total_return_pct"], "trades": r.metrics["trades"]})
        return stress

    def intraday_block(req, strat, params, df, rules, bt, warnings: list[str], model, stress) -> dict:
        """Скальпинг и внутридневная торговля: расходы решают, история коротка, а МосБиржа отдаёт данные с опозданием."""
        tz = MARKET_TZ.get(req.market)
        off = int(tz.utcoffset(None).total_seconds() * 1000) if tz else 0
        ts = df["ts"].to_numpy().astype("int64")
        days = len(set(((ts + off) // 86_400_000).tolist()))
        cost = model.round_trip_cost_pct
        n = bt.metrics["trades"]
        if days < 20:
            warnings.append(
                f"История покрывает {days} торговых дней: для внутридневной стратегии этого мало, результат может быть случайным."
            )
        fragile = cost_stress_warning(stress)
        if fragile:
            warnings.append(fragile)
        if not req.close_eod:
            warnings.append("Позиции удерживаются через ночь: для внутридневной торговли включите закрытие к концу дня.")
        delay = feed_delay_s(req.market, req.tf)
        if delay:
            warnings.append(
                f"Котировки {req.market.upper()} идут с задержкой ~{delay // 60} мин: на таймфрейме {req.tf} реальный "
                "сигнал придёт с опозданием, эту проверку нельзя считать применимой к скальпингу."
            )
        return {
            "tf": req.tf, "trading_days": days, "trades_per_day": round(n / days, 2) if days else None,
            "round_trip_cost_pct": round(cost, 4), "cost_stress": stress, "feed_delay_seconds": delay,
        }

    @app.post("/api/backtest")
    def run_backtest(req: BacktestRequest) -> dict:
        return compute_backtest(req)

    def compute_backtest(req: BacktestRequest, record: bool = True) -> dict:
        """record=False — расчёт «для объяснения»: в историю экспериментов не пишется и число проб не растёт."""
        adapter(req.market)
        strat = STRATEGIES.get(req.strategy)
        if strat is None:
            raise HTTPException(404, f"Неизвестная стратегия: {req.strategy}")
        params = strat.resolve(req.params)
        res, df, open_dropped, quality = load_series(req)
        rules = make_rules(req)
        model = exec_model(req)
        target = strat.target(df, params)
        bt = backtest(df, target, req.capital, model.fee_pct, model.slippage_pct, rules, **exec_kw(model))
        stress = cost_stress(df, target, req, rules, bt, model)
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
        m = bt.metrics
        intraday = None
        if req.tf in INTRADAY_TFS:
            intraday = intraday_block(req, strat, params, df, rules, bt, warnings, model, stress)
        if m.get("ambiguous_bars"):
            warnings.append(
                f"В {m['ambiguous_bars']} свечах достигнуты и стоп, и цель: порядок внутри свечи неизвестен, "
                "принят стоп (консервативно)."
            )
        if m.get("gap_exits"):
            warnings.append(f"Выходов по гэпу: {m['gap_exits']}. Исполнение было хуже уровня стопа.")
        integrity = integrity_report(strat, params, df, target, bt, req.capital, model.effective_slippage_pct, rules, model.fee_pct)
        if not integrity["passed"]:
            warnings.insert(0, integrity["summary"])
        part = m.get("max_participation_pct")
        if part and part > execution.LIQUIDITY_WARN_PCT:
            warnings.append(
                f"Заявки занимали бы до {part:g}% объёма свечи: на такой размер рынок мог не дать цены свечи, "
                "реальное исполнение было бы хуже. Уменьшите размер или включите ограничение по объёму."
            )
        if m.get("liquidity_capped_entries"):
            warnings.append(
                f"Размер входа урезан по объёму свечи {m['liquidity_capped_entries']} раз: "
                f"допущено не больше {model.max_participation_pct:g}% объёма."
            )
        if m.get("illiquid_exits"):
            warnings.append(f"Выход не уместился в допустимую долю объёма {m['illiquid_exits']} раз: заявку пришлось бы дробить.")
        if m.get("zero_volume_fills"):
            warnings.append(
                f"У {m['zero_volume_fills']} исполнений объём свечи неизвестен (ноль): ликвидность по ним не оценена."
            )
        if m.get("skipped_entries"):
            warnings.append(
                f"Вход по сигналу пропущен {m['skipped_entries']} раз: открытие уже ниже стопа, "
                "идёт прогрев ATR или размер по риску округлился до нуля."
            )
        out = {
            "strategy": strat.id,
            "params": params,
            "stale": res.stale,
            "metrics": metrics,
            "data_quality": quality,
            "warnings": warnings,
            "intraday": intraday,
            "execution": {
                "model": {"fee_pct": model.fee_pct, "slippage_pct": model.slippage_pct, "spread_pct": model.spread_pct,
                          "max_participation_pct": model.max_participation_pct,
                          "round_trip_cost_pct": round(model.round_trip_cost_pct, 4)},
                "cost_stress": stress,
                "story": execution.cost_story(stress[0]["return_pct"], stress[-1]["return_pct"], model),
                "max_participation_pct": m.get("max_participation_pct"),
            },
            "coverage": {
                "requested": req.limit,
                "candles": len(df),
                "first_ts": int(df["ts"].iloc[0]),
                "last_ts": int(df["ts"].iloc[-1]),
                # получили меньше запрошенного — у источника больше нет; ровно столько — раньше история могла быть
                "exhausted": len(res.candles) < req.limit,
            },
            "integrity": integrity,
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
                rules=rules_dict(rules),
                spread_pct=model.spread_pct,
                max_participation_pct=model.max_participation_pct,
            ),
            "trades": [asdict(t) for t in bt.trades],
            "equity": [{"t": t, "v": round(v, 2)} for t, v in bt.equity],
        }
        if record:
            prior = record_experiment("backtest", req, strat.id, params, 1, out["run_card"], {
                k: metrics.get(k) for k in ("total_return_pct", "max_drawdown_pct", "trades", "avg_r", "sharpe")
            })
        else:
            prior = svc.experiments.variants_tried(req.market, req.symbol, req.tf, strat.id) if svc.experiments else 0
        out["trials"] = trials_dto(prior, 1 if record else 0)
        out["weaknesses"] = discipline.backtest_weaknesses(out)
        return out

    def record_experiment(kind, req, strategy_id, params, variants, card, result, extra_config=None) -> int:
        """Записывает запуск в неизменяемую историю; возвращает, сколько вариантов пробовали ДО него."""
        if svc.experiments is None:
            return 0
        prior = svc.experiments.variants_tried(req.market, req.symbol, req.tf, strategy_id)
        svc.experiments.add(
            Experiment(
                kind=kind, market=req.market, symbol=req.symbol, tf=req.tf, strategy=strategy_id,
                strategy_version=card.get("strategy_version"), params=params,
                config={
                    "capital": req.capital, "fee_pct": req.fee_pct, "slippage_pct": req.slippage_pct,
                    "spread_pct": card.get("spread_pct"), "max_participation_pct": card.get("max_participation_pct"),
                    "limit": req.limit, "rules": card.get("rules"), **(extra_config or {}),
                },
                data_hash=card["data_hash"], engine_version=card["engine_version"], candles=card["candles"],
                n_variants=variants, result=result,
            )
        )
        return prior

    def trials_dto(prior: int, this_run: int) -> dict:
        return {
            "prior_variants": prior, "this_run_variants": this_run, "total_variants": prior + this_run,
            "warning": multiple_testing_warning(prior + this_run),
        }

    @app.post("/api/validate")
    def validate_out_of_sample(req: ValidateRequest) -> dict:
        adapter(req.market)
        strat = STRATEGIES.get(req.strategy)
        if strat is None:
            raise HTTPException(404, f"Неизвестная стратегия: {req.strategy}")
        base = strat.resolve(req.params)
        res, df, open_dropped, quality = load_series(req, min_candles=oos.MIN_TRAIN_CANDLES + oos.MIN_TEST_CANDLES)
        rules = make_rules(req)
        model = exec_model(req)
        result = oos.run_oos(
            strat, base, req.grid, df, req.train_pct, req.capital, req.fee_pct, req.slippage_pct, rules, exec_kw(model)
        )
        chosen = (result["chosen"] or {}).get("params", base)
        card = run_card(
            df, market=req.market, symbol=req.symbol, tf=req.tf, strategy=strat.id, params=chosen, capital=req.capital,
            fee_pct=req.fee_pct, slippage_pct=req.slippage_pct, stale=res.stale,
            strategy_version=strategy_version(strat, chosen), rules=rules_dict(rules),
            spread_pct=model.spread_pct, max_participation_pct=model.max_participation_pct,
        )
        variants = result["optimization"]["variants"]
        summary = {
            "train_pct": req.train_pct, "chosen": chosen, "verdict": result["verdict"]["status"],
            "train_return_pct": ((result["chosen"] or {}).get("train") or {}).get("total_return_pct"),
            "test_return_pct": ((result["chosen"] or {}).get("test") or {}).get("total_return_pct"),
            "test_trades": ((result["chosen"] or {}).get("test") or {}).get("trades"),
        }
        prior = record_experiment(
            "validate", req, strat.id, chosen, variants, card, summary,
            {"train_pct": req.train_pct, "grid": req.grid},
        )
        warnings = []
        if open_dropped:
            warnings.append("Последняя свеча ещё не закрыта и в расчёт не входит.")
        if res.stale:
            warnings.append("Источник данных недоступен — расчёт по сохранённым данным.")
        return {
            **result, "strategy": strat.id, "strategy_version": card["strategy_version"], "run_card": card,
            "data_quality": quality, "warnings": warnings, "trials": trials_dto(prior, variants),
        }

    @app.post("/api/lab")
    def validation_lab(req: LabRequest) -> dict:
        """Лаборатория проверки: walk-forward в нескольких окнах, карта устойчивости, bootstrap, концентрация прибыли,
        просадки, режимы, стресс расходов, PBO и Deflated Sharpe. Вердикт — простым языком, детали — в слое исследователя."""
        adapter(req.market)
        strat = STRATEGIES.get(req.strategy)
        if strat is None:
            raise HTTPException(404, f"Неизвестная стратегия: {req.strategy}")
        base = strat.resolve(req.params)
        res, df, open_dropped, quality = load_series(req, min_candles=lab_mod.MIN_WINDOW * req.folds)
        rules = make_rules(req)
        model = exec_model(req)
        prior = svc.experiments.variants_tried(req.market, req.symbol, req.tf, strat.id) if svc.experiments else 0
        result = lab_mod.run_lab(
            strat, base, req.grid, df, folds=req.folds, mode=req.mode, capital=req.capital, fee_pct=model.fee_pct,
            slippage_pct=model.slippage_pct, spread_pct=model.spread_pct, rules=rules,
            max_participation_pct=model.max_participation_pct, prior_variants=prior,
        )
        card = run_card(
            df, market=req.market, symbol=req.symbol, tf=req.tf, strategy=strat.id, params=base, capital=req.capital,
            fee_pct=req.fee_pct, slippage_pct=req.slippage_pct, stale=res.stale,
            strategy_version=strategy_version(strat, base), rules=rules_dict(rules),
            spread_pct=model.spread_pct, max_participation_pct=model.max_participation_pct,
        )
        variants = result["multiple_testing"]["variants_this_run"]
        wf = result["walk_forward"]["summary"]
        record_experiment(
            "validate", req, strat.id, base, variants, card,
            {"lab": True, "verdict": result["verdict"]["status"], "folds": wf["folds"],
             "profitable_folds": wf["profitable_folds"], "stitched_return_pct": wf["stitched_return_pct"]},
            {"lab": True, "folds": req.folds, "mode": req.mode, "grid": req.grid},
        )
        warnings = []
        if open_dropped:
            warnings.append("Последняя свеча ещё не закрыта и в расчёт не входит.")
        if res.stale:
            warnings.append("Источник данных недоступен — расчёт по сохранённым данным.")
        return {
            **result, "strategy": strat.id, "strategy_version": card["strategy_version"], "run_card": card,
            "data_quality": quality, "warnings": warnings, "trials": trials_dto(prior, variants),
        }

    @app.get("/api/experiments")
    def experiments_list(
        market: str | None = None, symbol: str | None = None, limit: int = Query(100, ge=1, le=500)
    ) -> list[dict]:
        return svc.experiments.list(market, symbol, limit) if svc.experiments else []

    # --- сигналы ---

    @app.get("/api/signals")
    def signals_list(
        limit: int = Query(100, ge=1, le=500),
        unseen: bool = False,
        market: str | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        if status is not None and status not in ("active", "expired", "acted", "dismissed"):
            raise ValueError("Статус сигнала — active, expired, acted или dismissed")
        ctx = signal_ctx()
        # при фильтре по статусу берём с запасом: срок и «действовали» вычисляются при чтении, а не хранятся
        rows = svc.signals.list(500 if status else limit, unseen, market, symbol)
        out = [signal_dto(s, ctx) for s in rows]
        return [d for d in out if d["status"] == status][:limit] if status else out

    @app.post("/api/signals/{signal_id}/dismiss")
    def signal_dismiss(signal_id: int) -> dict:
        """Отклонить сигнал: он не считается актуальным и не будет рассылаться. Обратимо."""
        if svc.signals.get(signal_id) is None:
            raise HTTPException(404, "Сигнал не найден")
        svc.signals.dismiss(signal_id, svc.now_ms() // 1000)
        return signal_dto(svc.signals.get(signal_id), signal_ctx())

    @app.post("/api/signals/{signal_id}/restore")
    def signal_restore(signal_id: int) -> dict:
        if svc.signals.get(signal_id) is None:
            raise HTTPException(404, "Сигнал не найден")
        svc.signals.undismiss(signal_id)
        return signal_dto(svc.signals.get(signal_id), signal_ctx())

    @app.post("/api/signals/seen")
    def signals_seen() -> dict:
        return {"marked": svc.signals.mark_all_seen()}

    @app.post("/api/scan")
    def scan_now() -> dict:
        r = svc.engine.scan()
        ctx = signal_ctx()
        stored = {(x.market, x.symbol, x.tf, x.strategy, x.candle_ts, x.side): x for x in svc.signals.list(500)}
        new = [stored.get((s.market, s.symbol, s.tf, s.strategy, s.candle_ts, s.side), s) for s in r.new]
        return {"new": [signal_dto(s, ctx) for s in new], "errors": r.errors}

    def signal_ctx() -> dict:
        acted = svc.journal.signal_ids() | (svc.plans.signal_ids() if svc.plans else set())
        return {"now": svc.now_ms(), "valid": svc.settings.get("signal_valid_bars"), "acted": acted}

    def signal_dto(s: Signal, ctx: dict) -> dict:
        d = asdict(s)
        d["status"] = signal_status(s, ctx["now"], ctx["valid"], s.id in ctx["acted"])
        d["expires_at"] = signal_expires_at(s, ctx["valid"])
        delay = feed_delay_s(s.market, s.tf)
        d["delay_seconds"] = delay
        # данные приходят позже, чем живёт сигнал: к моменту получения он уже мог устареть
        d["late"] = bool(delay) and delay * 1000 >= TIMEFRAME_MS[s.tf] * ctx["valid"]
        return d

    @app.get("/api/watch")
    def watch_status() -> dict:
        """Что и как наблюдается. Фонового режима нет: проверка идёт только пока приложение запущено."""
        cfg = svc.settings.all()
        now = svc.now_ms()
        sc = svc.scanner
        states = {(x["market"], x["symbol"]): x for x in svc.signals.states()}
        items = []
        for inst in svc.watchlist.list():
            st = states.get((inst.market, inst.symbol))
            paused = f"{inst.market}|{inst.symbol}" in cfg["paused_instruments"]
            items.append({
                "market": inst.market, "symbol": inst.symbol, "paused": paused,
                "status": "paused" if paused else (st["status"] if st else "unknown"),
                "message": "" if not st else st["message"], "last_scan_at": st and st["last_scan_at"],
                "last_ok_at": st and st["last_ok_at"],
            })
        warns = []
        for mk in ("moex", "crypto"):
            tfm = cfg.get(f"tf_{mk}")
            if tfm is None:
                continue
            window_ms = TIMEFRAME_MS[tfm] * cfg["signal_valid_bars"]
            if cfg["scan_interval_min"] * 60_000 > window_ms:
                warns.append(
                    f"Рынок {mk}: свеча {tfm}, сигнал актуален {cfg['signal_valid_bars']} св. "
                    f"({window_ms // 60_000} мин), а проверка идёт раз в {cfg['scan_interval_min']} мин: часть сигналов устареет до проверки."
                )
            delay = feed_delay_s(mk, tfm)
            if delay:
                warns.append(f"Рынок {mk}: данные с задержкой ~{delay // 60} мин, на таймфрейме {tfm} сигналы запаздывают.")
        return {
            "warnings": warns,
            "background_scanner": bool(sc and sc.alive),
            "interval_min": cfg["scan_interval_min"],
            "last_scan_at": svc.engine.last_scan_at,
            "next_scan_at": sc.next_scan_at if sc and sc.alive else None,
            "quiet_hours": {**cfg["quiet_hours"], "active_now": in_quiet_hours(cfg["quiet_hours"], now)},
            "signal_valid_bars": cfg["signal_valid_bars"],
            "instruments": items,
            "notes": [
                (
                    "Наблюдение работает только при запущенном приложении: если закрыть Compass, сигналы не ищутся "
                    "и уведомления не приходят. Отдельного фонового режима нет."
                ),
                "Сигнал появляется на закрытой свече; по незакрытой и по устаревшим данным сигналы не создаются.",
            ],
        }

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

    def account_for(market: str) -> Account:
        acc = svc.accounts.get(market) if svc.accounts else None
        if acc is None:
            raise ValueError(f"Для рынка {market} нет счёта")
        return acc

    def correlated_open_positions(market: str, symbol: str, positions: list[dict]) -> dict:
        """Корреляция дневных доходностей кандидата с открытыми позициями (по кэшу свечей; сбой источника — без вывода)."""
        others = [p["symbol"] for p in positions if p["symbol"] != symbol]
        if not others:
            return {"items": [], "note": None}
        try:
            def closes(sym: str) -> dict[int, float]:
                res = svc.cache.get(market, sym, "1d", 150)
                return {c.ts: c.close for c in closed_candles(res.candles, "1d", svc.now_ms())}

            items = portfolio.correlated_positions(closes(symbol), {s: closes(s) for s in others})
            return {"items": items, "note": None}
        except (MarketError, KeyError):
            return {"items": [], "note": "Корреляцию с открытыми позициями проверить не удалось: нет данных."}

    def risk_calc(req: RiskRequest) -> dict:
        info = instrument_info(req.market, req.symbol)
        acc = account_for(req.market)
        capital = req.capital if req.capital is not None else acc.capital
        if capital is None:
            raise ValueError(
                f"Капитал счёта «{acc.name}» не задан: укажите его в настройках счёта ({acc.currency}). "
                "Рубли и USDT не пересчитываются друг в друга."
            )
        risk_pct = req.risk_pct if req.risk_pct is not None else acc.risk_pct
        snap = account_snapshot(acc)
        available = req.available
        if available is None and snap["free"] is not None:
            available = max(0.0, snap["free"])  # свободные средства с учётом открытых позиций и результата
        model = execution.model_for(req.market, req.fee_pct, req.slippage_pct, req.spread_pct)
        fee = model.fee_pct
        slip = model.effective_slippage_pct  # проскальзывание + половина спреда: как в бэктесте и на учебном счёте
        bond = info.price_unit == "percent_of_face"
        if bond and not info.face_value:
            raise ValueError("Для облигации не получен номинал: посчитать размер позиции нельзя")
        is_crypto = req.market == "crypto"
        p = position_size(
            capital,
            risk_pct,
            req.entry,
            req.stop,
            lot=info.lot,
            qty_step=(info.qty_step or CRYPTO_QTY_STEP) if is_crypto else None,
            fee_pct=fee,
            slippage_pct=slip,
            available=available,
            unit_value=info.face_value / 100 if bond and info.face_value else 1.0,
            accrued=info.accrued or 0.0,
            min_qty=info.min_qty,
            min_cost=info.min_cost,
        )
        warnings = [w for w in (p.warning,) if w]
        pf = portfolio.assess_new_position(snap, acc, p.risk_amount, p.cost)
        warnings.extend(pf["warnings"])
        corr = correlated_open_positions(req.market, req.symbol, snap["positions"])
        pf["correlated"] = corr["items"]
        pf["correlation_note"] = corr["note"]
        for c in corr["items"]:
            warnings.append(
                f"Доходности {req.symbol} и открытой позиции {c['symbol']} сильно совпадали в прошлом "
                f"(корреляция {c['correlation']:g}): риск двух позиций фактически складывается."
            )
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
            "account": {"market": acc.market, "name": acc.name, "currency": acc.currency},
            "capital": capital,
            "risk_pct": risk_pct,
            "portfolio": pf,
            "unit_value": info.face_value / 100 if bond and info.face_value else 1.0,
            "fee_pct": fee,
            "slippage_pct": slip,
            "slippage_input_pct": model.slippage_pct,
            "spread_pct": model.spread_pct,
            "warnings": warnings,
            "assumptions": [
                "Потеря при стопе — расчётный сценарий, а не гарантированный максимум: цена может пройти стоп гэпом.",
                (
                    f"Считается вход и выход по стопу с комиссией {fee:g}% и проскальзыванием {slip:g}% "
                    f"с каждой стороны (проскальзывание {model.slippage_pct:g}% + половина спреда "
                    f"{model.spread_pct:g}%); ухудшенное исполнение — проскальзывание ×{WORSE_SLIPPAGE_MULT:g}."
                ),
                "Приложение ничего не исполняет и не блокирует: лимит — предупреждение.",
            ],
        }

    @app.post("/api/risk")
    def risk(req: RiskRequest) -> dict:
        return risk_calc(req)

    # --- счета и панель дня ---

    def accounts_store() -> AccountStore:
        if svc.accounts is None:
            raise HTTPException(503, "Хранилище счетов недоступно")
        return svc.accounts

    @app.get("/api/accounts")
    def accounts_list() -> list[dict]:
        return [asdict(a) for a in accounts_store().list()]

    @app.put("/api/accounts/{market}")
    def accounts_update(market: str, changes: dict) -> dict:
        try:
            return asdict(accounts_store().update(market, changes))
        except KeyError:
            raise HTTPException(404, f"Счёта для рынка {market} нет") from None

    def marks_for(market: str, symbols: list[str]) -> dict[str, portfolio.Mark]:
        """Рыночные цены для оценки позиций: последняя цена из свечей рабочего таймфрейма рынка.
        Источник недоступен и кэша нет — метки не будет, позиция останется оценённой по входу."""
        tf = svc.settings.get(f"tf_{market}")
        max_age = int(max(1800, min(3 * TIMEFRAME_MS[tf] / 1000, 3 * 86_400)))
        out: dict[str, portfolio.Mark] = {}
        for symbol in symbols:
            try:
                res = svc.cache.get(market, symbol, tf, 5)
            except (MarketError, KeyError):
                continue
            if res.candles:
                last = res.candles[-1]
                out[symbol] = portfolio.Mark(last.close, last.ts, res.fetched_at, res.stale, res.source, max_age)
        return out

    def account_snapshot(acc: Account, broker: list[dict] | None = None) -> dict:
        entries = svc.journal.list(market=acc.market, mode="real")
        held = sorted({p["symbol"] for p in portfolio._open_positions(entries)})
        return portfolio.snapshot(acc, entries, svc.now_ms(), marks_for(acc.market, held), broker)

    @app.post("/api/reconcile")
    def reconcile_with_broker(req: ReconcileRequest) -> dict:
        """Сверка журнала с выпиской брокера, введённой вручную. Ничего не сохраняет и не исправляет."""
        acc = accounts_store().get(req.market)
        if acc is None:
            raise HTTPException(404, f"Счёта для рынка {req.market} нет")
        for b in req.positions:
            if not isinstance(b.get("symbol"), str) or isinstance(b.get("qty"), bool) or not isinstance(b.get("qty"), int | float):
                raise ValueError("Каждая позиция выписки: тикер (текст) и количество (число)")
        snap = account_snapshot(acc, req.positions)
        return {"market": req.market, **snap["truth"]["reconciliation"],
                "notes": ["Compass ничего не исправляет: выберите, чья запись верна, и поправьте журнал сами."]}

    @app.get("/api/day")
    def day_panel() -> dict:
        """Панель дня по каждому счёту отдельно: без сложения рублей и USDT."""
        snaps = [account_snapshot(a) for a in accounts_store().list()]
        return {
            "accounts": snaps,
            "unseen_signals": len(svc.signals.list(500, True)),
            "problem_sources": [x for x in svc.signals.states() if x["status"] in ("stale", "error")],
            "notes": ["Наблюдение и сигналы работают только при запущенном приложении."],
        }

    @app.get("/api/cockpit")
    def decision_cockpit() -> dict:
        """Единый ответ на вопросы дня: что происходит, позиции, реальный риск, планы, сигналы, правила, данные.
        Всё берётся из тех же источников, что и остальные экраны (Portfolio Truth, планы, сигналы)."""
        accounts = [account_snapshot(a) for a in accounts_store().list()]
        plans = []
        if svc.plans is not None:
            for p in svc.plans.list(None, None, 100):
                rv = review(p, svc.journal.list(p.market, p.symbol, mode=None))
                if rv["status"] != "closed":
                    plans.append({**plan_dto(p), "review": rv})
        ctx = signal_ctx()
        active = [d for d in (signal_dto(s, ctx) for s in svc.signals.list(200)) if d["status"] == "active"]
        problems = [x for x in svc.signals.states() if x["status"] in ("stale", "error")]
        return cockpit_mod.build(accounts, plans, active, watch_status(), problems, svc.now_ms())

    @app.get("/api/review/week")
    def review_week(days: int = Query(7, ge=1, le=31)) -> dict:
        """Недельный разбор по каждому счёту отдельно: только рассчитанные факты по реальным сделкам."""
        now = svc.now_ms()
        out = []
        for acc in accounts_store().list():
            entries = svc.journal.list(market=acc.market, mode="real")
            out.append(review_week_mod.weekly(acc, entries, lambda uid: plan_store().get_by_uid(uid), now, days))
        return {"accounts": out}

    @app.get("/api/screener")
    def screener() -> dict:
        """Факты по избранному на закрытых свечах выбранного таймфрейма рынка. Сбой одного источника не ломает остальные."""
        cfg = svc.settings.all()
        now = svc.now_ms()
        ctx = signal_ctx()
        active = {}
        for s in svc.signals.list(500):
            if signal_status(s, now, ctx["valid"], s.id in ctx["acted"]) == "active":
                active.setdefault((s.market, s.symbol), []).append({"strategy": s.strategy, "side": s.side, "id": s.id})
        paused = set(cfg["paused_instruments"])
        rows = []
        for inst in svc.watchlist.list():
            tf = cfg.get(f"tf_{inst.market}")
            base = {"market": inst.market, "symbol": inst.symbol, "name": inst.name, "tf": tf,
                    "paused": f"{inst.market}|{inst.symbol}" in paused, "signals": active.get((inst.market, inst.symbol), [])}
            try:
                res = svc.cache.get(inst.market, inst.symbol, tf, 300)
            except MarketError as e:
                rows.append({**base, "status": "error", "message": str(e)[:200]})
                continue
            closed = closed_candles(res.candles, tf, now)
            if len(closed) < screener_mod.MIN_BARS:
                rows.append({**base, "status": "short", "message": "Слишком мало закрытых свечей"})
                continue
            df = candles_to_df(closed)
            try:
                chosen = _chosen_strategies(cfg["instrument_strategies"], inst.market, inst.symbol)
            except ValueError:
                chosen = []
            state = {strat.id: int(strat.target(df, params).iloc[-1]) for strat, params in chosen}
            levels = [lv.price for lv in (svc.levels.list(inst.market, inst.symbol) if svc.levels else [])]
            rows.append({**base, "status": "stale" if res.stale else "ok",
                         "message": "источник недоступен, показан кэш" if res.stale else "",
                         **screener_mod.screen_row(df, levels, state)})
        return {
            "rows": rows,
            "notes": [
                "Только факты по закрытым свечам; это фильтр внимания, а не рекомендация.",
                "«Правило в рынке» — что показывает стратегия на последней закрытой свече, а не совет входить.",
            ],
        }

    # --- сохранённые уровни ---

    def level_store() -> LevelStore:
        if svc.levels is None:
            raise HTTPException(503, "Хранилище уровней недоступно")
        return svc.levels

    @app.get("/api/levels")
    def levels_list(market: str, symbol: str, deleted: bool = False) -> list[dict]:
        return [asdict(x) for x in level_store().list(market, symbol, deleted)]

    @app.post("/api/levels", status_code=201)
    def levels_add(req: LevelRequest) -> dict:
        adapter(req.market)
        return asdict(level_store().add(Level(req.market, req.symbol, req.price, req.label)))

    @app.delete("/api/levels/{level_id}", status_code=204)
    def levels_remove(level_id: int) -> None:
        if not level_store().remove(level_id):
            raise HTTPException(404, "Уровня нет")

    @app.post("/api/levels/{level_id}/restore")
    def levels_restore(level_id: int) -> dict:
        if not level_store().restore(level_id):
            raise HTTPException(404, "Удалённого уровня с таким номером нет")
        return {"restored": level_id}

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
        reward_risk = None
        if req.target is not None and req.entry > req.stop:
            reward_risk = round((req.target - req.entry) / (req.entry - req.stop), 2)
        plan = store.add(
            Plan(
                market=req.market, symbol=req.symbol, source=getattr(adapter(req.market), "source_id", req.market),
                entry=req.entry, stop=req.stop, target=req.target, qty=r["qty"], lots=r["lots"],
                capital=r["capital"], risk_pct=r["risk_pct"],
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

    @app.get("/api/plans-active")
    def plans_active(market: str, symbol: str) -> list[dict]:
        """Планы по тикеру, которые ещё не закрыты (вход не выполнен или позиция открыта): их линии рисуются на графике."""
        entries = svc.journal.list(market, symbol, mode=None)
        out = []
        for p in plan_store().list(market, symbol, 50):
            status = review(p, entries)["status"]
            if status != "closed":
                out.append({**plan_dto(p), "status": status})
        return out

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

    # --- слой дисциплины и глоссарий ---

    @app.get("/api/glossary")
    def glossary_list() -> list[dict]:
        """Короткие определения понятий: интерфейс показывает их рядом с находками, где они нужны."""
        return glossary.all_terms()

    def changed_facts(hours: int) -> dict:
        now = svc.now_ms()
        accounts = [account_snapshot(a) for a in accounts_store().list()]
        entries = [{"ts": e.ts, "symbol": e.symbol, "side": e.side, "qty": e.qty, "price": e.price}
                   for e in svc.journal.list(mode="real")]
        ctx = signal_ctx()
        signals = [signal_dto(sg, ctx) for sg in svc.signals.list(200)]
        plans = [plan_dto(p) for p in svc.plans.list(None, None, 100)] if svc.plans is not None else []
        moves = []
        for a in accounts:
            tf = svc.settings.get(f"tf_{a['market']}")
            for p in a["positions"]:
                try:
                    candles_ = svc.cache.get(a["market"], p["symbol"], tf, 5).candles
                except (MarketError, KeyError):
                    continue
                if len(candles_) >= 2 and candles_[-2].close > 0:
                    moves.append({"market": a["market"], "symbol": p["symbol"], "mark_price": candles_[-1].close,
                                  "change_pct": (candles_[-1].close / candles_[-2].close - 1) * 100})
        return discipline.what_changed(now, hours, accounts, entries, signals, plans, moves)

    @app.get("/api/discipline/changed")
    def discipline_changed(hours: int = Query(24, ge=1, le=168)) -> dict:
        """Что изменилось за окно: записи журнала, сигналы, планы, движение позиций. Всё считает код."""
        facts = changed_facts(hours)
        return {**facts, "text": discipline.facts_text_changed(facts)}

    def discipline_data(days: int) -> dict:
        now = svc.now_ms()
        weeklies = [
            review_week_mod.weekly(a, svc.journal.list(market=a.market, mode="real"), lambda uid: plan_store().get_by_uid(uid), now, days)
            for a in accounts_store().list()
        ]
        return discipline.discipline_facts(decision_cockpit(), weeklies)

    @app.get("/api/discipline/violations")
    def discipline_violations(days: int = Query(7, ge=1, le=31)) -> dict:
        """Где вы отступили от собственных правил и планов: текущие нарушения и картина недели."""
        data = discipline_data(days)
        return {**data, "text": discipline.facts_text_discipline(data)}

    def compare_data(plan_id: int) -> dict:
        store = plan_store()
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, "План не найден")
        previous = [
            (p, review(p, svc.journal.list(p.market, p.symbol, mode=None)))
            for p in store.list(None, None, 200) if p.id is not None and plan.id is not None and p.id < plan.id
        ]
        return discipline.trade_comparison(plan, previous)

    @app.get("/api/discipline/compare-trade")
    def discipline_compare(plan_id: int) -> dict:
        """Чем план отличается от ваших прежних планов: размер, риск, стоп, цель. Только отличия, без оценок."""
        return compare_data(plan_id)

    register_ai_routes(app, svc, {
        "backtest": lambda req: compute_backtest(req, record=False),
        "changed": changed_facts, "discipline": discipline_data, "compare": compare_data,
    })
    register_replay_routes(app, svc, instrument_info, adapter, CRYPTO_QTY_STEP)

    # Интерфейс — последним: маршруты /api/* должны матчиться раньше статики.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


