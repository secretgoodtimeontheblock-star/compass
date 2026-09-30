"""История всех проверок стратегий.

Если сохранять только удачные результаты, лучший из сотни вариантов почти всегда выглядит хорошо
случайно. Поэтому каждый запуск бэктеста и каждый перебор параметров записывается сразу, и журнал
нельзя ни изменить, ни удалить (триггеры в БД). По нему считается, сколько вариантов правила уже
пробовали на этом инструменте, и пользователю показывается предупреждение о множественных проверках.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from compass.db import Connection

MULTIPLE_TESTING_WARN_AT = 20  # столько вариантов на одном инструменте — уже повод усомниться в лучшем из них


@dataclass(frozen=True, slots=True)
class Experiment:
    kind: str  # "backtest" | "validate"
    market: str
    symbol: str
    tf: str
    strategy: str
    strategy_version: str | None
    params: dict[str, int]
    config: dict[str, Any]
    data_hash: str
    engine_version: str
    candles: int
    n_variants: int  # сколько вариантов параметров реально считалось в этом запуске
    result: dict[str, Any]
    id: int | None = None
    created_at: int | None = None


class ExperimentLog:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def add(self, e: Experiment) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO experiments (created_at, kind, market, symbol, tf, strategy, strategy_version, params, "
                "config, data_hash, engine_version, candles, n_variants, result) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    int(time.time()), e.kind, e.market, e.symbol, e.tf, e.strategy, e.strategy_version,
                    json.dumps(e.params, sort_keys=True), json.dumps(e.config, sort_keys=True, default=str),
                    e.data_hash, e.engine_version, e.candles, e.n_variants,
                    json.dumps(e.result, sort_keys=True, default=str),
                ),
            )
        return int(cur.lastrowid)

    def variants_tried(self, market: str, symbol: str, tf: str, strategy: str) -> int:
        """Сколько вариантов этого правила на этом инструменте и таймфрейме уже считалось (по всем запускам)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(n_variants), 0) FROM experiments WHERE market=? AND symbol=? AND tf=? AND strategy=?",
                (market, symbol, tf, strategy),
            ).fetchone()
        return int(row[0])

    def list(self, market: str | None = None, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where, args = [], []
        if market:
            where.append("market = ?")
            args.append(market)
        if symbol:
            where.append("symbol = ?")
            args.append(symbol)
        sql = (
            "SELECT id, created_at, kind, market, symbol, tf, strategy, strategy_version, params, config, data_hash, "
            "engine_version, candles, n_variants, result FROM experiments"
            + (f" WHERE {' AND '.join(where)}" if where else "")
            + " ORDER BY id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        keys = ["id", "created_at", "kind", "market", "symbol", "tf", "strategy", "strategy_version", "params",
                "config", "data_hash", "engine_version", "candles", "n_variants", "result"]
        out = []
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            for k in ("params", "config", "result"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out


def multiple_testing_warning(variants: int) -> str | None:
    if variants < MULTIPLE_TESTING_WARN_AT:
        return None
    return (
        f"Это правило на этом инструменте уже пробовали в {variants} вариантах. Чем больше вариантов "
        "перепробовано, тем выше шанс, что лучший из них хорош случайно, а не потому что правило работает. "
        "Доверяйте результату на отдельном проверочном периоде, а не лучшему из перебора."
    )
