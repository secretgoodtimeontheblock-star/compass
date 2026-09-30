"""Скринер по избранному: факты о каждом инструменте по закрытым свечам, без оценок «покупать/продавать».

Показывает, где что-то происходит (движение, объём, волатильность, близость к вашим уровням, состояние
правила стратегии, есть ли актуальный сигнал), чтобы быстрее выбрать, на что смотреть. Это фильтр
внимания, а не рекомендация."""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from compass.indicators import atr, rsi, sma

MIN_BARS = 30


def _num(v: Any, nd: int = 2) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(f) else round(f, nd)


def screen_row(df: pd.DataFrame, levels: list[float], rule_state: dict[str, int]) -> dict[str, Any]:
    """df — только ЗАКРЫТЫЕ свечи. rule_state — «стратегия → 1/0» на последней закрытой свече."""
    n = len(df)
    close = df["close"]
    last = float(close.iloc[-1])
    ch1 = (last / float(close.iloc[-2]) - 1) * 100 if n >= 2 else None
    ch20 = (last / float(close.iloc[-21]) - 1) * 100 if n >= 21 else None
    s20 = sma(close, 20).iloc[-1] if n >= 20 else float("nan")
    s50 = sma(close, 50).iloc[-1] if n >= 50 else float("nan")
    a = atr(df, 14).iloc[-1] if n >= 15 else float("nan")
    vol = df["volume"]
    avg_vol = float(vol.iloc[-21:-1].mean()) if n >= 21 else float("nan")
    vol_ratio = float(vol.iloc[-1]) / avg_vol if avg_vol and avg_vol > 0 else None
    nearest = None
    if levels:
        lv = min(levels, key=lambda x: abs(x - last))
        nearest = {"price": lv, "distance_pct": round((last / lv - 1) * 100, 2)}
    return {
        "last": last,
        "change_bar_pct": _num(ch1),
        "change_20_pct": _num(ch20),
        "above_sma20": None if math.isnan(s20) else last > float(s20),
        "above_sma50": None if math.isnan(s50) else last > float(s50),
        "rsi14": _num(rsi(close, 14).iloc[-1], 1) if n >= 15 else None,
        "atr_pct": _num(float(a) / last * 100) if not math.isnan(a) else None,
        "volume_ratio": _num(vol_ratio),
        "nearest_level": nearest,
        "rule_state": rule_state,
        "bars": n,
    }
