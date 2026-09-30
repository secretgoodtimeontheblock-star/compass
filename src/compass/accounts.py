"""Счета: у каждого рынка свой счёт со своей валютой и своими лимитами.

Рубли МосБиржи и USDT криптобиржи не складываются: капитал, риск на сделку и лимиты у каждого счёта
свои, а единого итога нет. Лимиты — предупреждения: Compass ничего не исполняет и не блокирует
действия у брокера."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import UTC, timedelta, timezone
from typing import Any

from compass.db import Connection

# «День» для дневного лимита: у МосБиржи московские сутки, у крипты — UTC
MARKET_TZ = {"moex": timezone(timedelta(hours=3)), "crypto": UTC}


@dataclass(frozen=True, slots=True)
class Account:
    market: str
    name: str
    currency: str
    capital: float | None  # None — не задан; тогда размер позиции считать нельзя
    risk_pct: float = 1.0  # риск на одну сделку, % капитала
    daily_loss_limit_pct: float = 3.0  # дневной лимит реализованного убытка, % капитала
    max_open_risk_pct: float = 6.0  # «тепло»: суммарный риск открытых позиций по стопам, % капитала
    max_trades_per_day: int | None = None  # лимит входов в день (для внутридневной торговли); None — без лимита


_COLUMNS = "market, name, currency, capital, risk_pct, daily_loss_limit_pct, max_open_risk_pct, max_trades_per_day"


def _pct(v: Any, label: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or not 0 < v <= 100:
        raise ValueError(f"{label}: число больше 0 и не больше 100")
    return float(v)


class AccountStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def list(self) -> list[Account]:
        with self._lock:
            rows = self._conn.execute(f"SELECT {_COLUMNS} FROM accounts ORDER BY market").fetchall()
        return [Account(*r) for r in rows]

    def get(self, market: str) -> Account | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLUMNS} FROM accounts WHERE market = ?", (market,)).fetchone()
        return Account(*row) if row else None

    def update(self, market: str, changes: dict[str, Any]) -> Account:
        acc = self.get(market)
        if acc is None:
            raise KeyError(market)
        allowed = {"name", "capital", "risk_pct", "daily_loss_limit_pct", "max_open_risk_pct", "max_trades_per_day"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Неизвестные поля счёта: {', '.join(sorted(unknown))}")
        new = acc
        if "name" in changes:
            name = changes["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
                raise ValueError("Название счёта — от 1 до 60 символов")
            new = replace(new, name=name.strip())
        if "capital" in changes:
            v = changes["capital"]
            if v is not None and (
                isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or v <= 0
            ):
                raise ValueError("Капитал: число больше нуля (или пусто, если не задан)")
            new = replace(new, capital=None if v is None else float(v))
        for key, label in (
            ("risk_pct", "Риск на сделку"),
            ("daily_loss_limit_pct", "Дневной лимит убытка"),
            ("max_open_risk_pct", "Суммарный риск открытых позиций"),
        ):
            if key in changes:
                new = replace(new, **{key: _pct(changes[key], label)})
        if "max_trades_per_day" in changes:
            v = changes["max_trades_per_day"]
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 10_000):
                raise ValueError("Лимит сделок в день: целое число от 1 до 10000 (или пусто — без лимита)")
            new = replace(new, max_trades_per_day=v)
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE accounts SET name=?, capital=?, risk_pct=?, daily_loss_limit_pct=?, max_open_risk_pct=?, "
                "max_trades_per_day=? WHERE market=?",
                (new.name, new.capital, new.risk_pct, new.daily_loss_limit_pct, new.max_open_risk_pct,
                 new.max_trades_per_day, market),
            )
        return new
