"""SQLite: один файл в папке данных пользователя, без сервера и миграционных
инструментов. Схема версионируется через PRAGMA user_version."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_VERSION = 8

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


_V3 = """
CREATE TABLE journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL, symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty REAL NOT NULL CHECK (qty > 0), price REAL NOT NULL CHECK (price > 0),
  fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
  ts INTEGER NOT NULL,          -- когда сделка совершена, мс UTC
  note TEXT NOT NULL DEFAULT '',
  signal_id INTEGER,            -- из какого сигнала родилась сделка (необязательно)
  created_at INTEGER NOT NULL
);
CREATE INDEX journal_symbol ON journal (market, symbol, ts);
"""


_V4 = """
CREATE TABLE ai_cache (
  key TEXT PRIMARY KEY,         -- sha256(провайдер, модель, задача, запрос): те же данные — тот же ответ
  provider TEXT NOT NULL, model TEXT NOT NULL,
  response TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX ai_cache_created ON ai_cache (created_at DESC);
"""

# Старую криптоисторию нельзя приписать текущей бирже: настройка могла меняться.
# Сохраняем её отдельно; при первом обращении новый источник загрузит свой ряд.
_V5 = """
ALTER TABLE candles RENAME TO candles_v4;
CREATE TABLE candles (
  source TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
  ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
  close REAL NOT NULL, volume REAL NOT NULL,
  PRIMARY KEY (source, market, symbol, tf, ts)
) WITHOUT ROWID;
INSERT INTO candles
SELECT CASE WHEN market='moex' THEN 'moex:iss' ELSE 'legacy:' || market END,
       market, symbol, tf, ts, open, high, low, close, volume FROM candles_v4;
DROP TABLE candles_v4;
ALTER TABLE fetch_log RENAME TO fetch_log_v4;
CREATE TABLE fetch_log (
  source TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
  fetched_at INTEGER NOT NULL,
  PRIMARY KEY (source, market, symbol, tf)
);
INSERT INTO fetch_log
SELECT CASE WHEN market='moex' THEN 'moex:iss' ELSE 'legacy:' || market END,
       market, symbol, tf, fetched_at FROM fetch_log_v4;
DROP TABLE fetch_log_v4;
"""

# Причина входа и плановый стоп — чтобы сравнить замысел со сделкой, которую записал пользователь.
_V6 = """
ALTER TABLE journal ADD COLUMN planned_stop REAL;
ALTER TABLE journal ADD COLUMN reason TEXT NOT NULL DEFAULT '';
"""

# Параметры стратегии, породившие сигнал (JSON). У старых сигналов NULL: их параметры не сохранялись,
# и приложение не выдумывает их задним числом.
_V7 = """
ALTER TABLE signals ADD COLUMN params TEXT;
"""

# Режим сделки (реальная/учебная/историческая), стабильный uid для восстановления без дублей и
# мягкое удаление. Старые записи считаются реальными: других раньше не было.
_V8 = """
ALTER TABLE journal ADD COLUMN mode TEXT NOT NULL DEFAULT 'real' CHECK (mode IN ('real','paper','historical'));
ALTER TABLE journal ADD COLUMN uid TEXT;
ALTER TABLE journal ADD COLUMN deleted_at INTEGER;
UPDATE journal SET uid = lower(hex(randomblob(16))) WHERE uid IS NULL;
CREATE UNIQUE INDEX journal_uid ON journal (uid);
"""


class Connection(sqlite3.Connection):
    """Все сервисы делят блокировку соединения, включая чтение и commit/rollback.

    Держать lock нужно на протяжении всей операции с БД, а не отдельного execute.
    Сетевые вызовы выполняются за пределами блокировки.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()


def connect(path: Path | str) -> Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # FastAPI и сканер работают в разных потоках; сервисы используют conn.lock.
    conn = sqlite3.connect(str(path), check_same_thread=False, factory=Connection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if 0 < version < SCHEMA_VERSION and str(path) != ":memory:":
            backup = Path(str(path) + f".v{version}.bak")
            if not backup.exists():
                saved = sqlite3.connect(str(backup))
                try:
                    conn.backup(saved)
                finally:
                    saved.close()
        _migrate(conn)
    except Exception:
        conn.close()
        raise
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
    if version < 3:
        conn.executescript(_V3)
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    if version < 4:
        conn.executescript(_V4)
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    if version < 5:
        conn.executescript("BEGIN IMMEDIATE;\n" + _V5 + "\nPRAGMA user_version = 5;\nCOMMIT;")
    if version < 6:
        conn.executescript(_V6)
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
    if version < 7:
        conn.executescript(_V7)
        conn.execute("PRAGMA user_version = 7")
        conn.commit()
    if version < 8:
        conn.executescript(_V8)
        conn.execute("PRAGMA user_version = 8")
        conn.commit()
