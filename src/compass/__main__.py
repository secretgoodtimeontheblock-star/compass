"""Запуск: `python -m compass` — поднимает локальный API на 127.0.0.1."""

from __future__ import annotations

import logging

import uvicorn

from compass.api.app import Services, create_app
from compass.cache import CandleCache
from compass.config import Config
from compass.db import connect
from compass.journal import Journal
from compass.markets import CryptoAdapter, MoexAdapter
from compass.notify import CompositeNotifier, TelegramNotifier
from compass.scheduler import BackgroundScanner
from compass.settings import Settings
from compass.signals import SignalEngine, SignalStore
from compass.watchlist import Watchlist


def build_services(cfg: Config, background_scan: bool = True) -> Services:
    conn = connect(cfg.data_dir / "compass.sqlite3")
    adapters = {
        "moex": MoexAdapter(),
        "crypto": CryptoAdapter(cfg.crypto_exchange, cfg.proxy),
    }
    cache = CandleCache(conn, adapters)
    watchlist = Watchlist(conn)
    settings = Settings(conn, {k: a.timeframes for k, a in adapters.items()})
    store = SignalStore(conn)
    engine = SignalEngine(cache, watchlist, settings, store, CompositeNotifier(TelegramNotifier()))
    scanner = BackgroundScanner(engine, settings) if background_scan else None
    return Services(adapters, cache, watchlist, settings, store, engine, Journal(conn), scanner)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = Config.from_env()
    uvicorn.run(create_app(build_services(cfg)), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
