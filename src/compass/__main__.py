"""Запуск: `python -m compass` — поднимает локальный API на 127.0.0.1."""

from __future__ import annotations

import uvicorn

from compass.api.app import create_app
from compass.cache import CandleCache
from compass.config import Config
from compass.db import connect
from compass.markets import CryptoAdapter, MoexAdapter
from compass.watchlist import Watchlist


def build_app(cfg: Config | None = None):
    cfg = cfg or Config.from_env()
    conn = connect(cfg.data_dir / "compass.sqlite3")
    adapters = {
        "moex": MoexAdapter(),
        "crypto": CryptoAdapter(cfg.crypto_exchange, cfg.proxy),
    }
    return create_app(adapters, CandleCache(conn, adapters), Watchlist(conn))


def main() -> None:
    cfg = Config.from_env()
    uvicorn.run(build_app(cfg), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
