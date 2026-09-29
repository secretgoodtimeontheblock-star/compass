"""Общие модели данных. Время везде — миллисекунды UTC (int)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int  # начало свечи, мс UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    name: str
    market: str  # id адаптера: "moex" | "crypto"
    kind: str = ""  # share | bond | fx | "" для криптопар


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    """Параметры инструмента, нужные для расчёта заявки. None — источник не сообщил;
    приложение не подставляет вместо неизвестного значения «типичное»."""

    symbol: str
    market: str
    source: str
    lot: int = 1  # штук в лоте (акции МосБиржи); у крипты 1
    qty_step: float | None = None  # шаг количества (крипта)
    price_step: float | None = None  # шаг цены
    currency: str | None = None  # валюта цены/счёта инструмента: RUB, USD, USDT...
    min_qty: float | None = None  # минимальное количество (крипта)
    min_cost: float | None = None  # минимальная сумма заявки
    face_value: float | None = None  # номинал облигации
    accrued: float | None = None  # накопленный купонный доход на одну бумагу
    price_unit: str = "money"  # "money" — цена в валюте; "percent_of_face" — облигации: % от номинала
    trading_open: bool | None = None  # идут ли торги сейчас (None — неизвестно)
    complete: bool = True  # False — часть параметров получить не удалось


# Единые названия таймфреймов; адаптер сам переводит их в свои коды.
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "10m": 600_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def closed_candles(candles: Sequence[Candle], tf: str, now_ms: int) -> list[Candle]:
    """Только завершённые свечи: окно свечи целиком в прошлом.

    По незакрытой свече цена и объём ещё меняются, поэтому сигналы, бэктест и AI
    считают только по закрытым. Для МосБиржи это консервативно: дневная свеча
    считается закрытой по окончании календарных суток по Москве."""
    cutoff = now_ms - TIMEFRAME_MS[tf]
    return [c for c in candles if c.ts <= cutoff]
