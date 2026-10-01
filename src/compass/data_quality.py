"""Проверка ряда свечей: пропуски, дубли, битые цены, скачки.

Отсутствие ошибки источника не доказывает, что история полная и чистая (см.
docs/COMPETITIVE_ANALYSIS.md), поэтому проверка идёт по самим свечам. Она ничего
не исправляет и не удаляет — только сообщает, чему в ряде нельзя верить вслепую.
Идея — backtest/loader_health.py (Vibe-Trading, MIT).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

from compass.models import TIMEFRAME_MS, Candle

_DAY = TIMEFRAME_MS["1d"]
_MOEX_MAX_HOLIDAY_GAP = 12 * _DAY  # новогодние каникулы МосБиржи — до ~10 дней
_JUMP_1D = 0.35
_JUMP_INTRADAY = 0.15
_ZERO_VOLUME_SHARE = 0.05


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    severity: str  # "error" — ряду верить нельзя; "warning" — учитывать при выводах
    count: int
    message: str


def check_candles(candles: Sequence[Candle], market: str, tf: str) -> dict:
    issues: list[Issue] = []
    n = len(candles)
    tf_ms = TIMEFRAME_MS.get(tf)

    bad_order = dupes = 0
    for a, b in itertools.pairwise(candles):
        if b.ts == a.ts:
            dupes += 1
        elif b.ts < a.ts:
            bad_order += 1
    if dupes:
        issues.append(Issue("duplicates", "error", dupes, f"Повторяющихся свечей: {dupes}."))
    if bad_order:
        issues.append(Issue("unsorted", "error", bad_order, f"Свечи идут не по порядку времени: {bad_order} нарушений."))

    invalid = 0
    for c in candles:
        vals = (c.open, c.high, c.low, c.close)
        if not all(math.isfinite(v) and v > 0 for v in vals) or not math.isfinite(c.volume) or c.volume < 0 or c.high < max(c.open, c.close, c.low) or c.low > min(c.open, c.close, c.high):
            invalid += 1
    if invalid:
        issues.append(Issue("invalid_ohlc", "error", invalid, f"Свечей с невозможными ценами или объёмом: {invalid}."))

    if tf_ms and n > 1 and not (dupes or bad_order):
        gaps, missing = _gaps(candles, market, tf, tf_ms)
        if gaps:
            what = f"около {missing} свечей" if market == "crypto" else "участки без торгов длиннее обычных выходных"
            issues.append(
                Issue("gaps", "warning", gaps, f"Пропусков в истории: {gaps} ({what}). Не проверяйте стратегию через них.")
            )

    if n > 1 and not invalid:
        limit = _JUMP_1D if tf == "1d" else _JUMP_INTRADAY
        jumps = sum(1 for a, b in itertools.pairwise(candles) if abs(b.close / a.close - 1) > limit)
        if jumps:
            hint = " Это может быть реальный обвал или отскок, а у акций ещё и сплит или дивидендный гэп: цены в ряде не скорректированы." if market == "moex" else ""
            issues.append(Issue("jumps", "warning", jumps, f"Резких скачков цены (больше {limit:.0%} за свечу): {jumps}.{hint}"))

    zero = sum(1 for c in candles if c.volume == 0)
    if n and zero / n > _ZERO_VOLUME_SHARE:
        issues.append(Issue("zero_volume", "warning", zero, f"Свечей с нулевым или непереданным объёмом: {zero} из {n}. По ним нельзя оценить активность торгов."))

    status = "error" if any(i.severity == "error" for i in issues) else "warning" if issues else "ok"
    return {
        "status": status,
        "candles": n,
        "issues": [{"code": i.code, "severity": i.severity, "count": i.count, "message": i.message} for i in issues],
    }


def _gaps(candles: Sequence[Candle], market: str, tf: str, tf_ms: int) -> tuple[int, int]:
    gaps = missing = 0
    for a, b in itertools.pairwise(candles):
        d = b.ts - a.ts
        if market == "crypto":  # торгуется круглосуточно: каждая свеча должна быть
            if d > tf_ms * 1.5:
                gaps += 1
                missing += round(d / tf_ms) - 1
        elif tf == "1d" and d > _MOEX_MAX_HOLIDAY_GAP:  # у акций внутри дня перерывы нормальны
            gaps += 1
    return gaps, missing
