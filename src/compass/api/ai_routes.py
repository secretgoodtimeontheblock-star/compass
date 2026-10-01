"""Маршруты AI. Здесь из данных приложения собираются факты для промпта; сам сервис AI
про рынки и базу ничего не знает."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from compass.ai import prompts
from compass.ai.providers import AiError, AiNotReady
from compass.ai.service import AiResult
from compass import discipline, execution, glossary
from compass.backtest import backtest
from compass.api.schemas import BacktestRequest
from compass.markets.base import MarketError
from compass.models import closed_candles
from compass.strategies import STRATEGIES, candles_to_df

if TYPE_CHECKING:
    from compass.api.app import Services



class ExplainRequest(BaseModel):
    signal_id: int
    refresh: bool = False


class ReviewRequest(BaseModel):
    market: str | None = None
    symbol: str | None = None
    refresh: bool = False


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    market: str | None = None
    symbol: str | None = None
    tf: str | None = None
    refresh: bool = False


def _dto(r: AiResult) -> dict:
    return asdict(r)


class ChangedRequest(BaseModel):
    hours: int = Field(24, ge=1, le=168)
    refresh: bool = False


class DisciplineRequest(BaseModel):
    days: int = Field(7, ge=1, le=31)
    refresh: bool = False


class CompareRequest(BaseModel):
    plan_id: int
    refresh: bool = False


class ConceptRequest(BaseModel):
    concept_id: str
    context: str | None = Field(None, max_length=200)
    refresh: bool = False


def register_ai_routes(app: FastAPI, svc: Services, hooks: dict) -> None:
    """hooks — вычисления, которыми владеет app.py (бэктест, кокпит, недельные разборы, сравнение): так AI получает
    те же факты, что и экраны, а не их копию."""
    @app.exception_handler(AiNotReady)
    def _not_ready(_req, exc: AiNotReady):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=409)

    @app.exception_handler(AiError)
    def _ai_error(_req, exc: AiError):
        return JSONResponse({"detail": str(exc), "code": "ai_error"}, status_code=502)

    @app.get("/api/ai/status")
    def ai_status() -> dict:
        return svc.ai.status()

    @app.get("/api/ai/providers")
    def ai_providers() -> list[dict]:
        return svc.ai.providers_info()

    @app.get("/api/ai/providers/{provider_id}/models")
    def ai_models(provider_id: str) -> list[dict]:
        return [asdict(m) for m in svc.ai.provider_models(provider_id)]

    @app.post("/api/ai/explain-signal")
    def explain_signal(req: ExplainRequest) -> dict:
        sig = svc.signals.get(req.signal_id)
        if sig is None:
            raise HTTPException(404, "Сигнал не найден")
        strat = STRATEGIES.get(sig.strategy)
        if strat is None:
            raise HTTPException(404, f"Неизвестная стратегия: {sig.strategy}")
        legacy = sig.params is None  # сигнал создан до сохранения параметров
        params = strat.resolve(sig.params)
        acc = svc.accounts.get(sig.market) if svc.accounts else None
        snapshot = metrics = None
        warning = None
        try:  # без свежих данных объяснение остаётся, просто беднее
            result = svc.cache.get(sig.market, sig.symbol, sig.tf, 1000)
            candles = closed_candles(result.candles, sig.tf, svc.now_ms())
            warning = prompts.STALE_WARNING if result.stale else None
            snapshot = prompts.snapshot_facts(candles[-300:], sig.tf, sig.market)
            if len(candles) >= 30:
                df = candles_to_df(candles)
                model = execution.model_for(sig.market)
                metrics = backtest(
                    df, strat.target(df, params), 100_000.0, model.fee_pct, model.slippage_pct,
                    spread_pct=model.spread_pct,
                ).metrics
        except (MarketError, ValueError):
            warning = prompts.UNAVAILABLE_WARNING
        if legacy:
            note = (
                "Параметры этого сигнала не сохранены (создан старой версией): "
                "в объяснении и метриках использованы параметры по умолчанию."
            )
            warning = f"{warning} {note}" if warning else note
        prompt = prompts.explain_signal(sig, strat, params, snapshot, metrics, acc.capital if acc else None, acc.risk_pct if acc else None)
        return _dto(svc.ai.run(prompts.with_data_warning(prompt, warning), req.refresh))

    @app.post("/api/ai/review-journal")
    def review_journal(req: ReviewRequest) -> dict:
        entries = svc.journal.list(req.market, req.symbol)
        # итоги считаются по всему журналу тикера, а не по усечённой выборке промпта
        prompt = prompts.journal_review(entries, svc.journal.positions(), req.symbol)
        return _dto(svc.ai.run(prompt, req.refresh))

    @app.post("/api/ai/ask")
    def ask(req: AskRequest) -> dict:
        snapshot = None
        warning = None
        if req.market and req.symbol:
            adapter = svc.adapters.get(req.market)
            if adapter is None:
                raise HTTPException(404, f"Неизвестный рынок: {req.market}")
            cfg = svc.settings.all()
            tf = req.tf or cfg.get(f"tf_{req.market}", "1d")
            try:
                result = svc.cache.get(req.market, req.symbol, tf, 300)
                snapshot = prompts.snapshot_facts(result.candles, tf, req.market)
                warning = prompts.STALE_WARNING if result.stale else None
            except MarketError:
                snapshot = None  # ответим общим объяснением, без привязки к инструменту
                warning = prompts.UNAVAILABLE_WARNING
        prompt = prompts.teach(req.question.strip(), snapshot, req.symbol)
        return _dto(svc.ai.run(prompts.with_data_warning(prompt, warning), req.refresh))

    @app.post("/api/ai/what-changed")
    def what_changed(req: ChangedRequest) -> dict:
        facts = hooks["changed"](req.hours)
        return _dto(svc.ai.run(prompts.what_changed(discipline.facts_text_changed(facts)), req.refresh))

    @app.post("/api/ai/explain-backtest")
    def explain_backtest(req: BacktestRequest) -> dict:
        result = hooks["backtest"](req)  # расчёт «для объяснения»: в историю экспериментов не пишется
        facts = discipline.facts_text_weaknesses(result, result["weaknesses"])
        return _dto(svc.ai.run(prompts.weak_assumptions(facts), False))

    @app.post("/api/ai/discipline")
    def discipline_review(req: DisciplineRequest) -> dict:
        facts = discipline.facts_text_discipline(hooks["discipline"](req.days))
        return _dto(svc.ai.run(prompts.discipline_review(facts), req.refresh))

    @app.post("/api/ai/compare-trade")
    def compare_trade(req: CompareRequest) -> dict:
        facts = discipline.facts_text_comparison(hooks["compare"](req.plan_id))
        return _dto(svc.ai.run(prompts.compare_trade(facts), req.refresh))

    @app.post("/api/ai/explain-concept")
    def explain_concept(req: ConceptRequest) -> dict:
        t = glossary.term(req.concept_id)
        if t is None:
            raise HTTPException(404, "Такого понятия нет в глоссарии")
        return _dto(svc.ai.run(prompts.explain_concept(t["term"], t["short"], t["why"], req.context), req.refresh))
