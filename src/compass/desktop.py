"""Десктопное окно: движок в фоновом потоке + нативное окно WebView2 (pywebview).

Запуск: `python -m compass.desktop`. Один процесс, один порт (случайный свободный,
если не задан COMPASS_PORT) — интерфейс раздаётся тем же сервером, что и API.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time

import uvicorn

from compass.__main__ import build_services
from compass.api.app import create_app
from compass.config import Config

log = logging.getLogger("compass.desktop")
START_TIMEOUT_S = 20.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_started(server: uvicorn.Server, thread: threading.Thread, timeout: float = START_TIMEOUT_S) -> None:
    """Ждём готовности сервера; если поток умер (порт занят, сбой БД) — не ждём вхолостую."""
    deadline = time.monotonic() + timeout
    while not server.started:
        if not thread.is_alive():
            raise RuntimeError("Движок Compass не запустился (см. сообщения выше)")
        if time.monotonic() > deadline:
            raise RuntimeError(f"Движок Compass не ответил за {timeout:.0f} с")
        time.sleep(0.05)


def main() -> None:
    import webview  # импорт здесь: без pywebview остальное приложение (`python -m compass`) работает

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = Config.from_env()
    port = cfg.port if "COMPASS_PORT" in os.environ else free_port()

    app = create_app(build_services(cfg))
    server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="compass-engine", daemon=True)
    thread.start()
    wait_started(server, thread)

    try:
        webview.create_window("Compass", f"http://{cfg.host}:{port}/", width=1360, height=860, min_size=(900, 600))
        webview.start()
    finally:
        server.should_exit = True  # lifespan остановит фоновый сканер
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
