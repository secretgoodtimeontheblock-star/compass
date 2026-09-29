from __future__ import annotations

import socket
import threading

import pytest

from compass.desktop import free_port, wait_started


class FakeServer:
    def __init__(self, started: bool = False) -> None:
        self.started = started


def test_free_port_is_actually_free() -> None:
    port = free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))  # не должно быть занято


def test_wait_started_returns_when_ready() -> None:
    wait_started(FakeServer(started=True), threading.current_thread(), timeout=1)  # type: ignore[arg-type]


def test_wait_started_fails_fast_when_engine_thread_died() -> None:
    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()
    with pytest.raises(RuntimeError, match="не запустился"):
        wait_started(FakeServer(), t, timeout=5)  # type: ignore[arg-type]


def test_wait_started_times_out() -> None:
    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    try:
        with pytest.raises(RuntimeError, match="не ответил"):
            wait_started(FakeServer(), t, timeout=0.2)  # type: ignore[arg-type]
    finally:
        stop.set()
