"""Запуск: `python -m compass` — поднимает локальный API на 127.0.0.1."""

from __future__ import annotations

import logging

import uvicorn

from compass.accounts import AccountStore
from compass.alerts import AlertEngine, AlertStore, AlertWatcher
from compass.ai.providers import ClaudeProvider, CursorProvider, OllamaProvider
from compass.ai.service import AiService
from compass.api.app import Services, create_app
from compass.cache import CandleCache
from compass.config import Config
from compass.db import connect
from compass.experiments import ExperimentLog
from compass.journal import Journal
from compass.levels import LevelStore
from compass.live import OkxLive, PollingLive
from compass.markets import CryptoAdapter, MoexAdapter
from compass.notify import CompositeNotifier, TelegramNotifier
from compass.plans import PlanStore
from compass.replay import ReplayStore
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
    providers = {
        "cursor": CursorProvider(cfg.data_dir / "ai-workdir"),
        "claude": ClaudeProvider(),
        "ollama": OllamaProvider(),
    }
    ai = AiService(providers, settings, conn)
    scanner = BackgroundScanner(engine, settings) if background_scan else None
    notifier = CompositeNotifier(TelegramNotifier())
    alert_store = AlertStore(conn)
    alert_engine = AlertEngine(cache, alert_store, notifier)
    watcher = AlertWatcher(alert_engine) if background_scan else None
    live = OkxLive(cache, cfg.proxy) if cfg.crypto_exchange == "okx" else None
    return Services(adapters, cache, watchlist, settings, store, engine, Journal(conn), ai, scanner, live, PollingLive(cache, adapters), PlanStore(conn), ExperimentLog(conn), AccountStore(conn), ReplayStore(conn), LevelStore(conn), alert_store, alert_engine, watcher)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = Config.from_env()
    uvicorn.run(create_app(build_services(cfg)), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
