"""Маршруты учебного счёта. Клиент получает только уже открытые свечи: будущее не покидает сервер."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from compass.models import InstrumentInfo, closed_candles
from compass.replay import (
    MAX_STEP,
    MIN_REPLAY,
    MIN_WARMUP,
    Bar,
    ReplayError,
    ReplayStore,
    Session,
    apply_bar,
    summarize,
)
from compass.risk import DEFAULT_FEE_PCT, DEFAULT_SLIPPAGE_PCT
from compass.strategies import candles_to_df
from compass.validation import data_fingerprint

if TYPE_CHECKING:
    from compass.api.app import Services

DEFAULT_PAPER_CAPITAL = {"moex": 100_000.0, "crypto": 10_000.0}
HISTORY_LIMIT = 5000


class ReplayStart(BaseModel):
    market: str
    symbol: str
    tf: str = "1d"
    replay_bars: int = Field(120, ge=MIN_REPLAY, le=1000)  # сколько свечей предстоит открыть
    capital: float | None = Field(None, gt=0)  # учебные деньги; по умолчанию — счёт рынка или условная сумма
    fee_pct: float | None = Field(None, ge=0, le=10)
    slippage_pct: float | None = Field(None, ge=0, le=10)


class ReplayOrder(BaseModel):
    side: Literal["buy", "sell"]
    qty: float = Field(gt=0)
    stop: float | None = Field(None, gt=0)


class ReplayStop(BaseModel):
    stop: float | None = Field(None, gt=0)


class ReplayStep(BaseModel):
    bars: int = Field(1, ge=1, le=MAX_STEP)


def register_replay_routes(
    app: FastAPI,
    svc: Services,
    instrument_info: Callable[[str, str], InstrumentInfo],
    adapter: Callable[[str], object],
    crypto_step: float,
) -> None:
    if svc.replay is None:
        return
    store: ReplayStore = svc.replay

    def session(session_id: int) -> Session:
        s = store.get(session_id)
        if s is None:
            raise HTTPException(404, "Учебная сессия не найдена")
        return s

    def visible(s: Session) -> list[Bar]:
        return s.bars[: s.cursor + 1]  # только открытые свечи; всё, что правее, дальше не используется

    def dto(s: Session) -> dict:
        bars = visible(s)
        sim = s.sim
        assert sim is not None
        last = bars[-1]
        result = summarize(s.capital, s.fills, bars, s.start_index)
        return {
            "id": s.id, "market": s.market, "symbol": s.symbol, "tf": s.tf, "source": s.source,
            "status": s.status, "capital": s.capital, "fee_pct": s.fee_pct, "slippage_pct": s.slippage_pct,
            "cursor_ts": last.ts, "replayed": s.cursor - s.start_index,
            "remaining": len(s.bars) - 1 - s.cursor, "last_close": last.c,
            "cash": round(sim.cash, 2), "qty": sim.qty,
            "avg_price": None if sim.avg_price is None else round(sim.avg_price, 8),
            "stop": sim.stop,
            "pending": None if sim.pending is None else {"side": sim.pending[0], "qty": sim.pending[1], "stop": sim.pending[2]},
            "fills": [
                {
                    **asdict(f),
                    "risk_pct": None if f.side != "buy" or f.stop is None else round(f.qty * (f.price - f.stop) / s.capital * 100, 2),
                }
                for f in s.fills
            ],
            "result": result,
            "notes": [
                "Учебные деньги: это тренировка, реальных заявок нет, в журнал и статистику реальных сделок сделки не попадают.",
                "Вы видите даты: если помните, что было на самом деле, это подсказка, которой в реальной торговле нет.",
                "Заявка исполнится по открытию следующей свечи; стоп проверяется по свечам консервативно (гэп — по открытию).",
            ],
        }

    @app.post("/api/replay", status_code=201)
    def replay_start(req: ReplayStart) -> dict:
        adapter(req.market)
        res = svc.cache.get(req.market, req.symbol, req.tf, HISTORY_LIMIT)
        candles = closed_candles(res.candles, req.tf, svc.now_ms())
        need = req.replay_bars + MIN_WARMUP
        if len(candles) < need:
            raise ReplayError(
                f"Мало истории: нужно хотя бы {need} закрытых свечей (есть {len(candles)}). Уменьшите число свечей воспроизведения."
            )
        acc = svc.accounts.get(req.market) if svc.accounts else None
        capital = req.capital or (acc.capital if acc and acc.capital else DEFAULT_PAPER_CAPITAL.get(req.market, 100_000.0))
        df = candles_to_df(candles)
        bars = [Bar(c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles]
        s = store.create(
            market=req.market, symbol=req.symbol, tf=req.tf, source=res.source or req.market, capital=capital,
            fee_pct=req.fee_pct if req.fee_pct is not None else DEFAULT_FEE_PCT.get(req.market, 0.1),
            slippage_pct=req.slippage_pct if req.slippage_pct is not None else DEFAULT_SLIPPAGE_PCT,
            bars=bars, start_index=len(bars) - 1 - req.replay_bars, data_hash=data_fingerprint(df),
        )
        return dto(s)

    @app.get("/api/replay")
    def replay_list() -> list[dict]:
        return store.list()

    @app.get("/api/replay/{session_id}")
    def replay_get(session_id: int) -> dict:
        return dto(session(session_id))

    @app.get("/api/replay/{session_id}/candles")
    def replay_candles(session_id: int) -> dict:
        s = session(session_id)
        return {
            "source": s.source,
            "candles": [{"t": b.ts, "o": b.o, "h": b.h, "l": b.low, "c": b.c, "v": b.v} for b in visible(s)],
        }

    def check_active(s: Session) -> None:
        if s.status != "active":
            raise ReplayError("Сессия завершена: новые заявки и шаги невозможны")

    @app.post("/api/replay/{session_id}/order")
    def replay_order(session_id: int, req: ReplayOrder) -> dict:
        s = session(session_id)
        check_active(s)
        sim = s.sim
        assert sim is not None
        if sim.pending is not None:
            raise ReplayError("Уже есть заявка на следующую свечу: отмените её или сделайте шаг")
        if not math.isfinite(req.qty):
            raise ReplayError("Количество должно быть конечным числом")
        last = visible(s)[-1]
        info = instrument_info(s.market, s.symbol)
        if s.market == "crypto":
            step = info.qty_step or crypto_step
            if abs(req.qty / step - round(req.qty / step)) > 1e-6:
                raise ReplayError(f"Количество должно быть кратно шагу {step:g}")
        elif abs(req.qty / info.lot - round(req.qty / info.lot)) > 1e-9:
            raise ReplayError(f"Количество должно быть кратно лоту ({info.lot} шт.)")
        if req.side == "buy":
            fee, slip = s.fee_pct / 100, s.slippage_pct / 100
            est = req.qty * last.c * (1 + slip) * (1 + fee)
            if est > sim.cash + 1e-9:
                raise ReplayError(f"Не хватает свободных денег: нужно около {est:,.2f}, есть {sim.cash:,.2f}".replace(",", " "))
            if info.min_qty is not None and req.qty < info.min_qty:
                raise ReplayError(f"Количество меньше минимальной заявки биржи ({info.min_qty:g})")
            if info.min_cost is not None and req.qty * last.c < info.min_cost:
                raise ReplayError(f"Сумма меньше минимальной заявки биржи ({info.min_cost:g})")
            if req.stop is not None and req.stop >= last.c:
                raise ReplayError("Стоп должен быть ниже последней цены закрытия (только лонг)")
        else:
            if req.qty > sim.qty + 1e-9:
                raise ReplayError(f"Продажа {req.qty:g} больше позиции {sim.qty:g}")
            if req.stop is not None:
                raise ReplayError("Стоп задаётся только у покупки")
        store.save_pending(s.id, (req.side, req.qty, req.stop))
        return dto(session(session_id))

    @app.delete("/api/replay/{session_id}/order")
    def replay_cancel_order(session_id: int) -> dict:
        s = session(session_id)
        check_active(s)
        if s.sim is None or s.sim.pending is None:
            raise HTTPException(404, "Активной заявки нет")
        store.save_pending(s.id, None)
        return dto(session(session_id))

    @app.post("/api/replay/{session_id}/stop")
    def replay_stop(session_id: int, req: ReplayStop) -> dict:
        s = session(session_id)
        check_active(s)
        if s.sim is None or s.sim.qty <= 1e-12:
            raise ReplayError("Стоп можно менять только у открытой позиции")
        if req.stop is not None and req.stop >= visible(s)[-1].c:
            raise ReplayError("Стоп должен быть ниже последней цены закрытия")
        store.save_stop(s.id, req.stop)
        return dto(session(session_id))

    @app.post("/api/replay/{session_id}/step")
    def replay_step(session_id: int, req: ReplayStep) -> dict:
        s = session(session_id)
        check_active(s)
        sim = s.sim
        assert sim is not None
        cursor = s.cursor
        new_fills = []
        events: list[str] = []
        for _ in range(req.bars):
            if cursor >= len(s.bars) - 1:
                break
            cursor += 1
            fills, ev = apply_bar(sim, s.bars[cursor], s.fee_pct, s.slippage_pct)
            new_fills += fills
            events += ev
        if cursor == s.cursor:
            raise ReplayError("Данные закончились: сессию можно только завершить")
        done = cursor >= len(s.bars) - 1
        if done and sim.pending is not None:
            sim.pending = None
            events.append("Последняя свеча открыта: заявка, не успевшая исполниться, снята.")
        store.advance(s.id, cursor, new_fills, sim, "finished" if done else "active")
        out = dto(session(session_id))
        out["events"] = events
        return out

    @app.post("/api/replay/{session_id}/finish")
    def replay_finish(session_id: int) -> dict:
        s = session(session_id)
        if s.status == "active":
            store.finish(s.id)
        return dto(session(session_id))
