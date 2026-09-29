"""Настройки из окружения. Пользовательские настройки из UI появятся позже
в таблице SQLite; здесь только то, что нужно до открытия базы."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def default_data_dir() -> Path:
    override = os.environ.get("COMPASS_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")  # Windows: %APPDATA%\Compass
    return Path(base) / "Compass" if base else Path.home() / ".compass"


@dataclass(frozen=True, slots=True)
class Config:
    data_dir: Path
    crypto_exchange: str  # id биржи ccxt: bybit | okx | binance | ...
    proxy: str | None  # http(s)-прокси для крипто-бирж, если площадка недоступна напрямую
    host: str = "127.0.0.1"  # только локально: API без авторизации
    port: int = 8765

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            data_dir=default_data_dir(),
            crypto_exchange=os.environ.get("COMPASS_CRYPTO_EXCHANGE", "okx"),
            proxy=os.environ.get("COMPASS_PROXY") or None,
            port=int(os.environ.get("COMPASS_PORT", "8765")),
        )
