"""Уведомления о сигналах. Отправка никогда не роняет скан: сбой канала
логируется, сигнал при этом уже сохранён в базе и виден в ленте."""

from __future__ import annotations

import logging
import os
from typing import Protocol

import httpx

from compass.models import feed_delay_s
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
    delay = feed_delay_s(s.market, s.tf)
    late = f"\n⚠ Данные с задержкой ~{delay // 60} мин: цена на бирже уже могла уйти." if delay else ""
    return f"{head}\nСтратегия: {name}\n{body}{late}\n{DISCLAIMER}"


def format_digest(signals: list[Signal]) -> str:
    lines = [f"🔔 Сигналы, накопившиеся за тихие часы: {len(signals)}"]
    for s in signals[:10]:
        lines.append(f"{'🟢 вход' if s.side == 'buy' else '🔴 выход'}: {s.symbol} ({s.tf}) по {s.price:g}")
    if len(signals) > 10:
        lines.append(f"…и ещё {len(signals) - 10}")
    lines.append("Подробности — в приложении.")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


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
        self._post(format_signal(signal))

    def send_digest(self, signals: list[Signal]) -> None:
        self._post(format_digest(signals))

    def _post(self, text: str) -> None:
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

    def send_digest(self, signals: list[Signal]) -> None:
        for n in self._notifiers:
            try:
                digest = getattr(n, "send_digest", None)
                if digest is not None:
                    digest(signals)
                else:
                    for s in signals:
                        n.send(s)
            except Exception:
                log.exception("Сбой канала уведомлений %s", type(n).__name__)
