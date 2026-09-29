"""Уведомления о сигналах. Отправка никогда не роняет скан: сбой канала
логируется, сигнал при этом уже сохранён в базе и виден в ленте."""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

from compass.signals import Signal
from compass.strategies import STRATEGIES

log = logging.getLogger("compass.notify")

DISCLAIMER = "Не инвестиционная рекомендация."


def format_signal(s: Signal) -> str:
    name = STRATEGIES[s.strategy].name if s.strategy in STRATEGIES else s.strategy
    if s.side == "buy":
        head = f"🟢 Сигнал на ВХОД: {s.symbol} ({s.tf})"
        stop = f"\nОриентир стопа: {s.stop:g}" if s.stop else ""
        body = f"Цена закрытия: {s.price:g}{stop}"
    else:
        head = f"🔴 Сигнал на ВЫХОД: {s.symbol} ({s.tf})"
        body = f"Цена закрытия: {s.price:g}"
    return f"{head}\nСтратегия: {name}\n{body}\n{DISCLAIMER}"


class Notifier(Protocol):
    def send(self, signal: Signal) -> None: ...


class TelegramNotifier:
    """Без токена работает в dry-run (пишет в лог) — как и в ORION."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN")
        self._chat = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID")
        self._client = client or httpx.Client(timeout=10.0)

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat)

    def send(self, signal: Signal) -> None:
        text = format_signal(signal)
        if not self.enabled:
            log.info("[telegram dry-run]\n%s", text)
            return
        try:
            r = self._client.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat, "text": text},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            # URL содержит токен — в лог кладём только тип ошибки и статус
            status = getattr(getattr(e, "response", None), "status_code", None)
            log.warning("Telegram: не удалось отправить (%s, статус %s)", type(e).__name__, status)


class CompositeNotifier:
    def __init__(self, *notifiers: Notifier) -> None:
        self._notifiers = notifiers

    def send(self, signal: Signal) -> None:
        for n in self._notifiers:
            try:
                n.send(signal)
            except Exception:
                log.exception("Сбой канала уведомлений %s", type(n).__name__)
