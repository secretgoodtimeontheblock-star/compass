"""Пользовательские настройки в SQLite (ключ → JSON-значение) с валидацией.
Секреты (токены бирж/Telegram) здесь НЕ хранятся."""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable
from typing import Any

from compass.ai.providers import MODEL_ID_RE
from compass.db import Connection
from compass.markets.crypto import CRYPTO_EXCHANGES
from compass.strategies import STRATEGIES

AI_PROVIDERS = ("off", "cursor", "claude", "ollama")
# уровни показа: три представления одного движка — «Начинаю», «Торгую», «Исследую»
EXPERIENCE_LEVELS = ("beginner", "trader", "researcher")

DEFAULTS: dict[str, Any] = {
    "capital": 100_000.0,  # капитал для расчёта размера позиции
    "risk_pct": 1.0,  # максимум потерь на сделку, % капитала
    "scan_interval_min": 15,  # как часто искать сигналы
    "tf_moex": "1d",
    "tf_crypto": "1d",
    "ai_provider": "off",  # off | cursor | claude | ollama
    "ai_models": {},  # провайдер → выбранная модель; пусто — модель по умолчанию у провайдера
    # провайдер, которому пользователь разрешил отправку данных (для облачных обязательно)
    "ai_consent": "",
    # «рынок|тикер» → стратегия и параметры, которыми и сканер, и бэктест пользуются для этого инструмента.
    # Пусто — сканер проверяет все стратегии с параметрами по умолчанию.
    "instrument_strategies": {},
    # сколько свечей после закрытия сигнал считается актуальным; потом получает статус «устарел»
    "signal_valid_bars": 3,
    # тихие часы: сигналы сохраняются и видны в ленте, но внешние уведомления откладываются до их конца
    "quiet_hours": {"enabled": False, "from": "22:00", "to": "08:00"},
    # «рынок|тикер», по которым наблюдение приостановлено
    "paused_instruments": [],
    # сколько возможностей показывать в интерфейсе; движок и расчёты у всех уровней одни и те же
    "experience_level": "beginner",
    # пустая основная биржа — та, с которой процесс запущен (COMPASS_CRYPTO_EXCHANGE)
    "crypto_exchange": "",
    "crypto_fallback": "bybit",
}


class Settings:
    def __init__(self, conn: Connection, timeframes: dict[str, tuple[str, ...]]) -> None:
        """timeframes: рынок → допустимые таймфреймы (для валидации tf_*)."""
        self._conn = conn
        self._tfs = timeframes
        self._lock = conn.lock

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
        current = self.all()
        ex = clean.get("crypto_exchange", current["crypto_exchange"])
        fb = clean.get("crypto_fallback", current["crypto_fallback"])
        if ex and fb and ex == fb:
            raise ValueError("Запасная биржа должна отличаться от основной")
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
            "instrument_strategies": _instrument_strategies,
            "signal_valid_bars": lambda v: _int(v, "Срок сигнала, свечей", 1, 50),
            "quiet_hours": _quiet_hours,
            "paused_instruments": _paused,
            "experience_level": lambda v: _choice(v, "Уровень", EXPERIENCE_LEVELS),
            "crypto_exchange": _crypto_exchange,
            "crypto_fallback": _crypto_exchange,
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


def profile_key(market: str, symbol: str) -> str:
    return f"{market}|{symbol}"


def _instrument_strategies(v: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict):
        raise ValueError("Настройки стратегий: нужен объект «рынок|тикер» → стратегия")  # noqa: TRY004
    out: dict[str, dict[str, Any]] = {}
    for key, item in v.items():
        if not isinstance(key, str) or key.count("|") != 1:
            raise ValueError("Ключ стратегии — «рынок|тикер»")
        market, symbol = key.split("|", 1)
        if not market or not symbol:
            raise ValueError("Ключ стратегии — «рынок|тикер»")
        if not isinstance(item, dict) or set(item) - {"strategy", "params"}:
            raise ValueError(f"{symbol}: укажите стратегию и параметры")
        sid = item.get("strategy")
        strat = STRATEGIES.get(sid) if isinstance(sid, str) else None
        if strat is None:
            raise ValueError(f"Неизвестная стратегия: {sid}")
        params = item.get("params", {})
        if not isinstance(params, dict):
            raise ValueError(f"{symbol}: параметры стратегии — объект")  # noqa: TRY004
        out[key] = {"strategy": strat.id, "params": strat.resolve(params)}
    return out


def _int(v: Any, label: str, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{label}: целое число от {lo} до {hi}")
    return v


_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _quiet_hours(v: Any) -> dict[str, Any]:
    if not isinstance(v, dict) or set(v) - {"enabled", "from", "to"}:
        raise ValueError("Тихие часы: объект с полями enabled, from, to")
    cur = DEFAULTS["quiet_hours"]
    out = {**cur, **v}
    if not isinstance(out["enabled"], bool):
        raise ValueError("Тихие часы: enabled — да или нет")  # noqa: TRY004
    for key in ("from", "to"):
        if not isinstance(out[key], str) or not _HHMM.match(out[key]):
            raise ValueError("Тихие часы: время в формате ЧЧ:ММ")
    if out["enabled"] and out["from"] == out["to"]:
        raise ValueError("Тихие часы: начало и конец не должны совпадать")
    return out


def _crypto_exchange(v: Any) -> str:
    if v == "":
        return ""
    if not isinstance(v, str) or v not in CRYPTO_EXCHANGES:
        raise ValueError(f"Биржа крипты — пусто или одна из: {', '.join(CRYPTO_EXCHANGES)}")
    return v


def _paused(v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(k, str) and k.count("|") == 1 and all(k.split("|")) for k in v):
        raise ValueError("Пауза наблюдения: список «рынок|тикер»")
    return sorted(set(v))
