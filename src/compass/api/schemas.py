"""Модели запросов, общие для нескольких групп маршрутов (бэктест нужен и экранам, и AI-объяснению)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    market: str
    symbol: str
    tf: str = "1d"
    strategy: str
    params: dict[str, int] = Field(default_factory=dict)
    limit: int = Field(1000, ge=10, le=20000)  # для внутридневных таймфреймов нужна история глубже 5000 свечей
    capital: float = 100_000.0
    fee_pct: float = 0.05
    slippage_pct: float = 0.05
    spread_pct: float | None = Field(None, ge=0, lt=100)  # None — спред профиля рынка
    # предельная доля объёма свечи в заявке: None — размер не ограничивается (доля всё равно считается)
    max_volume_pct: float | None = Field(None, gt=0, le=100)
    # стопы, цели и размер по риску; всё пусто — прежний режим «весь капитал, выход по сигналу»
    stop_atr_mult: float | None = Field(None, gt=0, le=20)
    target_r: float | None = Field(None, gt=0, le=50)
    risk_pct: float | None = Field(None, gt=0, le=100)
    close_eod: bool = False  # внутридневная торговля: закрывать позицию к концу торгового дня


class ValidateRequest(BacktestRequest):
    train_pct: float = Field(70, ge=50, le=90)  # доля истории на подбор; остальное — проверка
    grid: dict[str, list[int]] = Field(default_factory=dict)  # параметр -> значения для перебора; пусто — без подбора


class LabRequest(BacktestRequest):
    grid: dict[str, list[int]] = Field(default_factory=dict)  # не больше двух параметров; пусто — правило фиксировано
    folds: int = Field(4, ge=3, le=8)  # число независимых проверочных окон
    mode: Literal["rolling", "anchored"] = "rolling"  # скользящее или растущее обучение
