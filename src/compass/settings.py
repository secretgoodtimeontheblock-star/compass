"""Пользовательские настройки в SQLite (ключ → JSON-значение) с валидацией.
Секреты (токены бирж/Telegram) здесь НЕ хранятся."""

from __future__ import annotations

import copy
import json
import math
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from compass.ai.providers import MODEL_ID_RE

AI_PROVIDERS = ("off", "cursor", "claude", "ollama")

DEFAULTS: dict[str, Any] = {
    "capital": 100_000.0,  # капитал для расчёта размера позиции
    "risk_pct": 1.0,  # максимум потерь на сделку, % капитала
    "scan_interval_min": 15,  # как часто искать сигналы
    "tf_moex": "1d",
    "tf_crypto": "4h",
    "ai_provider": "off",  # off | cursor | claude | ollama
    "ai_models": {},  # провайдер → выбранная модель; пусто — модель по умолчанию у провайдера
    # провайдер, которому пользователь разрешил отправку данных (для облачных обязательно)
    "ai_consent": "",
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
        return {**copy.deepcopy(DEFAULTS), **stored}  # копия: вызывающий не должен менять общие дефолты

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
            "ai_provider": lambda v: _choice(v, "Провайдер AI", AI_PROVIDERS),
            "ai_models": _ai_models,
            "ai_consent": lambda v: v if v == "" else _choice(v, "Согласие AI", AI_PROVIDERS[1:]),
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


def _choice(v: Any, label: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(v, str) or v not in allowed:
        raise ValueError(f"{label}: один из {', '.join(allowed)}")
    return v


def _ai_models(v: Any) -> dict[str, str]:
    """Модель идёт в командную строку Cursor CLI, поэтому проверяем её здесь, а не только при вызове."""
    if not isinstance(v, dict):
        # ValueError, не TypeError: API отдаёт его как 422 с понятным текстом
        raise ValueError("Модели AI: нужен объект «провайдер → модель»")  # noqa: TRY004
    out: dict[str, str] = {}
    for provider, model in v.items():
        _choice(provider, "Провайдер AI", AI_PROVIDERS[1:])
        if model != "" and not (isinstance(model, str) and MODEL_ID_RE.match(model)):
            raise ValueError(f"Недопустимое имя модели для {provider}")
        if model:
            out[provider] = model
    return out


def _int(v: Any, label: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{label}: целое число от {lo} до {hi}")
    return v
