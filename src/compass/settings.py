"""Пользовательские настройки в SQLite (ключ → JSON-значение) с валидацией.
Секреты (токены бирж/Telegram) здесь НЕ хранятся."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

DEFAULTS: dict[str, Any] = {
    "capital": 100_000.0,  # капитал для расчёта размера позиции
    "risk_pct": 1.0,  # максимум потерь на сделку, % капитала
    "scan_interval_min": 15,  # как часто искать сигналы
    "tf_moex": "1d",
    "tf_crypto": "4h",
}


class Settings:
    def __init__(self, conn: sqlite3.Connection, timeframes: dict[str, tuple[str, ...]]) -> None:
        """timeframes: рынок → допустимые таймфреймы (для валидации tf_*)."""
        self._conn = conn
        self._tfs = timeframes
        self._lock = threading.Lock()

    def all(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        stored = {k: json.loads(v) for k, v in rows if k in DEFAULTS}
        return {**DEFAULTS, **stored}

    def get(self, key: str) -> Any:
        return self.all()[key]

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"Неизвестные настройки: {', '.join(sorted(unknown))}")
        clean = {k: self._validate(k, v) for k, v in changes.items()}
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO settings VALUES (?, ?)",
                [(k, json.dumps(v)) for k, v in clean.items()],
            )
        return self.all()

    def _validate(self, key: str, value: Any) -> Any:
        checks: dict[str, Callable[[Any], Any]] = {
            "capital": lambda v: _num(v, "Капитал", lo=0, lo_open=True),
            "risk_pct": lambda v: _num(v, "Риск на сделку", lo=0, hi=100, lo_open=True),
            "scan_interval_min": lambda v: _int(v, "Интервал скана", 1, 1440),
            "tf_moex": lambda v: self._tf(v, "moex"),
            "tf_crypto": lambda v: self._tf(v, "crypto"),
        }
        return checks[key](value)

    def _tf(self, v: Any, market: str) -> str:
        allowed = self._tfs.get(market, ())
        if v not in allowed:
            raise ValueError(f"Таймфрейм для рынка {market} — один из: {', '.join(allowed)}")
        return v


def _num(v: Any, label: str, lo: float, hi: float | None = None, lo_open: bool = False) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or math.isnan(v):
        raise ValueError(f"{label}: нужно число")
    if v < lo or (lo_open and v == lo) or (hi is not None and v > hi):
        raise ValueError(f"{label}: значение вне допустимых границ")
    return float(v)


def _int(v: Any, label: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{label}: целое число от {lo} до {hi}")
    return v
