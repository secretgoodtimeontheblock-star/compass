"""Десктопное окно: движок в фоновом потоке + нативное окно WebView2 (pywebview).

Запуск: `python -m compass.desktop`. Один процесс, один порт (случайный свободный,
если не задан COMPASS_PORT) — интерфейс раздаётся тем же сервером, что и API.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
import threading
import time
import traceback

import uvicorn

from compass.__main__ import build_services
from compass.api.app import create_app, static_dir
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


def _message(text: str) -> None:
    log.error(text)
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "Compass", 0x10)


def webview2_ready() -> bool:
    """Edge WebView2 нужен окну. На Windows 11 он обычно уже есть."""
    if sys.platform != "win32":
        return True
    import winreg

    guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    paths = (
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}",
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}",
    )
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for path in paths:
            try:
                with winreg.OpenKey(hive, path) as key:
                    version, _ = winreg.QueryValueEx(key, "pv")
            except OSError:
                continue
            if version and version != "0.0.0.0":
                return True
    return False


def main() -> None:
    import webview  # импорт здесь: без pywebview остальное приложение (`python -m compass`) работает

    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(cfg.data_dir / "compass.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    try:
        if not static_dir().is_dir():
            raise RuntimeError("В сборке нет интерфейса. Переустановите Compass.")
        if not webview2_ready():
            raise RuntimeError(
                "Не найден Microsoft Edge WebView2. Установите его с сайта Microsoft "
                "(Evergreen Bootstrapper) и запустите Compass снова."
            )
        port = cfg.port if "COMPASS_PORT" in os.environ else free_port()
        # одноразовый токен сессии: только окно приложения получает доступ к API
        token = secrets.token_urlsafe(32)
        app = create_app(build_services(cfg), session_token=token)
        server = uvicorn.Server(uvicorn.Config(app, host=cfg.host, port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, name="compass-engine", daemon=True)
        thread.start()
        wait_started(server, thread)
        log.info("Compass слушает http://%s:%s", cfg.host, port)
        try:
            webview.create_window(
                "Compass", f"http://{cfg.host}:{port}/?token={token}", width=1360, height=860, min_size=(960, 640)
            )
            webview.start()
        finally:
            server.should_exit = True  # lifespan остановит фоновый сканер
            thread.join(timeout=5)
    except Exception as exc:
        _message(f"{exc}\n\nПодробности: {cfg.data_dir / 'compass.log'}")
        log.error("%s", traceback.format_exc())
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
