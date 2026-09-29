"""Стратегии. Только long/flat — и на акциях МосБиржи, и на крипто-споте
обычный частный трейдер не шортит, поэтому «короткой» позиции в модели нет.

Стратегия отвечает на один вопрос: «на закрытии свечи i я хочу быть в позиции
(1) или вне (0)?». Исполнение (по открытию следующей свечи), комиссии и
проскальзывание — дело бэктестера, не стратегии.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from compass.indicators import rsi, sma


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    label: str
    default: int
    min: int
    max: int


@dataclass(frozen=True, slots=True)
class Strategy:
    id: str
    name: str
    description: str  # для новичка, по-русски
    params: tuple[Param, ...]
    target: Callable[[pd.DataFrame, dict[str, int]], pd.Series]
    check: Callable[[dict[str, int]], str | None] = lambda p: None  # ошибка сочетания параметров

    def resolve(self, overrides: dict[str, int] | None = None) -> dict[str, int]:
        """Дефолты + значения пользователя с проверкой границ. ValueError — по-русски."""
        overrides = overrides or {}
        known = {p.name for p in self.params}
        extra = set(overrides) - known
        if extra:
            raise ValueError(f"Неизвестные параметры: {', '.join(sorted(extra))}")
        out: dict[str, int] = {}
        for p in self.params:
            v = overrides.get(p.name, p.default)
            if isinstance(v, bool) or not isinstance(v, int):
                # ValueError, не TypeError: API отдаёт его как 422 с понятным текстом
                raise ValueError(f"Параметр «{p.label}» должен быть целым числом")  # noqa: TRY004
            if not p.min <= v <= p.max:
                raise ValueError(f"Параметр «{p.label}» должен быть от {p.min} до {p.max}")
            out[p.name] = v
        problem = self.check(out)
        if problem:
            raise ValueError(problem)
        return out


def _sma_cross(df: pd.DataFrame, p: dict[str, int]) -> pd.Series:
    fast, slow = sma(df["close"], p["fast"]), sma(df["close"], p["slow"])
    return (fast > slow).astype(int)  # NaN в прогреве даёт False → 0


def _rsi_reversion(df: pd.DataFrame, p: dict[str, int]) -> pd.Series:
    r = rsi(df["close"], p["period"]).to_numpy()
    out = np.zeros(len(r), dtype=int)
    held = 0
    for i, v in enumerate(r):
        if np.isnan(v):
            held = 0
        elif held == 0 and v < p["buy_below"]:
            held = 1
        elif held == 1 and v > p["exit_above"]:
            held = 0
        out[i] = held
    return pd.Series(out, index=df.index)


def _donchian(df: pd.DataFrame, p: dict[str, int]) -> pd.Series:
    # уровни считаются по ПРЕДЫДУЩИМ свечам (shift) — иначе свеча сравнивается сама с собой
    upper = df["high"].rolling(p["entry"]).max().shift(1)
    lower = df["low"].rolling(p["exit"]).min().shift(1)
    close = df["close"].to_numpy()
    up, lo = upper.to_numpy(), lower.to_numpy()
    out = np.zeros(len(df), dtype=int)
    held = 0
    for i in range(len(df)):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            held = 0
        elif held == 0 and close[i] > up[i]:
            held = 1
        elif held == 1 and close[i] < lo[i]:
            held = 0
        out[i] = held
    return pd.Series(out, index=df.index)


STRATEGIES: dict[str, Strategy] = {
    s.id: s
    for s in (
        Strategy(
            id="sma_cross",
            name="Пересечение скользящих средних",
            description=(
                "Покупаем, когда быстрая средняя цена поднялась выше медленной (тренд вверх), "
                "выходим, когда упала ниже. Работает на трендах, в боковике даёт ложные входы."
            ),
            params=(
                Param("fast", "Быстрая средняя (свечей)", 20, 2, 200),
                Param("slow", "Медленная средняя (свечей)", 50, 3, 400),
            ),
            target=_sma_cross,
            check=lambda p: None if p["fast"] < p["slow"] else "Быстрая средняя должна быть короче медленной",
        ),
        Strategy(
            id="rsi_reversion",
            name="RSI: покупка после перепроданности",
            description=(
                "RSI показывает, насколько резко цена падала или росла. Покупаем, когда RSI "
                "опустился ниже порога (цену слишком сильно продали), выходим, когда RSI "
                "вернулся выше порога выхода. Хорошо в боковике, плохо в сильном падении."
            ),
            params=(
                Param("period", "Период RSI", 14, 2, 100),
                Param("buy_below", "Покупать, если RSI ниже", 30, 5, 50),
                Param("exit_above", "Выходить, если RSI выше", 50, 30, 90),
            ),
            target=_rsi_reversion,
            check=lambda p: None if p["buy_below"] < p["exit_above"] else "Порог входа должен быть ниже порога выхода",
        ),
        Strategy(
            id="donchian",
            name="Пробой канала Дончиана",
            description=(
                "Покупаем, когда цена закрылась выше максимума за последние N свечей "
                "(пробой вверх), выходим, когда закрылась ниже минимума за M свечей. "
                "Ловит начало сильных движений, но часто входит на ложных пробоях."
            ),
            params=(
                Param("entry", "Канал входа (свечей)", 20, 2, 300),
                Param("exit", "Канал выхода (свечей)", 10, 2, 300),
            ),
            target=_donchian,
        ),
    )
}


def candles_to_df(candles) -> pd.DataFrame:
    df = pd.DataFrame(
        [(c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    return df.reset_index(drop=True)
