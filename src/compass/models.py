"""Общие модели данных. Время везде — миллисекунды UTC (int)."""

from __future__ import annotations

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
