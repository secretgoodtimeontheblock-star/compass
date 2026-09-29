"""Фоновый скан по расписанию. Обычный поток: приложение локальное, одна
машина, внешний планировщик (cron/Celery) здесь был бы лишним."""

from __future__ import annotations

import logging
import threading

from compass.settings import Settings
from compass.signals import SignalEngine

log = logging.getLogger("compass.scheduler")


class BackgroundScanner:
    def __init__(self, engine: SignalEngine, settings: Settings, first_delay_s: float = 5.0) -> None:
        self._engine, self._settings, self._first_delay = engine, settings, first_delay_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="compass-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        wait = self._first_delay
        while not self._stop.wait(wait):
            try:
                res = self._engine.scan()
                if res.new or res.errors:
                    log.info("скан: новых сигналов %d, ошибок %d", len(res.new), len(res.errors))
            except Exception:
                log.exception("Сбой скана")
            # интервал читаем каждый раз: пользователь мог поменять его в настройках
            wait = float(self._settings.get("scan_interval_min")) * 60
