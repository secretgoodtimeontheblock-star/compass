"""SQLite: один файл в папке данных пользователя, без сервера и миграционных
инструментов. Схема версионируется через PRAGMA user_version."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

_V1 = """
CREATE TABLE candles (
  market TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL, ts INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume REAL NOT NULL,
  PRIMARY KEY (market, symbol, tf, ts)
) WITHOUT ROWID;

CREATE TABLE fetch_log (
  market TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL, fetched_at INTEGER NOT NULL,
  PRIMARY KEY (market, symbol, tf)
);

CREATE TABLE watchlist (
  market TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
  added_at INTEGER NOT NULL,
  PRIMARY KEY (market, symbol)
);
"""


_V2 = """
CREATE TABLE signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
  strategy TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('buy','exit')),
  candle_ts INTEGER NOT NULL,   -- закрытая свеча, на которой сработал сигнал
  price REAL NOT NULL, stop REAL,
  created_at INTEGER NOT NULL, seen INTEGER NOT NULL DEFAULT 0,
  -- один и тот же сигнал на одной свече не дублируется, сколько бы раз ни шёл скан
  UNIQUE (market, symbol, tf, strategy, candle_ts, side)
);
CREATE INDEX signals_created ON signals (created_at DESC);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # FastAPI гоняет sync-эндпоинты по пулу потоков — одно соединение на всех
    # безопасно только вместе с блокировкой в Cache (см. cache.py).
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"База создана более новой версией Compass (схема {version}, "
            f"эта версия знает {SCHEMA_VERSION}). Обновите приложение."
        )
    if version < 1:
        conn.executescript(_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    if version < 2:
        conn.executescript(_V2)
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
