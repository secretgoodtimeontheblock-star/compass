"""Выполнение AI-запросов: выбор провайдера из настроек, согласие на отправку данных,
кэш ответов, ограничение параллельных запусков, проверка чисел в ответе."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

from compass.ai.audit import unverified_numbers
from compass.ai.prompts import Prompt
from compass.ai.providers import AiError, AiNotReady, ModelInfo, Provider
from compass.db import Connection
from compass.settings import Settings

log = logging.getLogger("compass.ai")

CACHE_MAX_ROWS = 500


@dataclass(frozen=True, slots=True)
class AiResult:
    text: str
    provider: str
    model: str
    cached: bool
    warnings: list[str] = field(default_factory=list)


class AiService:
    def __init__(
        self,
        providers: dict[str, Provider],
        settings: Settings,
        conn: Connection,
        max_parallel: int = 2,
        slot_wait_s: float = 2.0,
    ) -> None:
        self._slot_wait_s = slot_wait_s
        self._providers = providers
        self._settings = settings
        self._conn = conn
        self._db_lock = conn.lock
        # AI-запрос — это процесс CLI или сетевой вызов на десятки секунд; лавину не запускаем
        self._slots = threading.BoundedSemaphore(max_parallel)

    # ---------- состояние для интерфейса ----------

    def status(self) -> dict:
        cfg = self._settings.all()
        pid = cfg["ai_provider"]
        p = self._providers.get(pid)
        base = {"provider": pid, "model": None, "cloud": None, "available": False, "reason": "", "consent": False}
        if p is None:
            return {**base, "reason": "AI выключен"}
        ok, reason = p.available()
        return {
            **base,
            "model": self._model_for(cfg, p),
            "cloud": p.cloud,
            "available": ok,
            "reason": reason,
            "consent": (not p.cloud) or cfg["ai_consent"] == pid,
        }

    def providers_info(self) -> list[dict]:
        return [{"id": p.id, "name": p.name, "cloud": p.cloud, "default_model": p.default_model} for p in self._providers.values()]

    def provider_models(self, pid: str) -> list[ModelInfo]:
        p = self._providers.get(pid)
        if p is None:
            raise AiNotReady(f"Неизвестный провайдер: {pid}", "unknown_provider")
        return p.models()

    @staticmethod
    def _model_for(cfg: dict, p: Provider) -> str:
        return (cfg["ai_models"].get(p.id) or p.default_model)

    # ---------- запуск ----------

    def run(self, prompt: Prompt, refresh: bool = False) -> AiResult:
        cfg = self._settings.all()
        pid = cfg["ai_provider"]
        provider = self._providers.get(pid)
        if provider is None:
            raise AiNotReady("AI выключен. Включите его в настройках и выберите провайдера.", "off")
        if provider.cloud and cfg["ai_consent"] != pid:
            raise AiNotReady(
                f"Для работы через «{provider.name}» данные (тикер, цены, а для разбора журнала — ваши записи) "
                "будут отправлены стороннему сервису. Нужно ваше согласие.",
                "consent",
            )
        model = self._model_for(cfg, provider)
        # разделитель \x1f (в тексте не встречается): ("ab","c") и ("a","bc") не должны дать один ключ
        joined = "\x1f".join([pid, model, prompt.task, prompt.system, prompt.user])  # noqa: FLY002
        key = hashlib.sha256(joined.encode("utf-8")).hexdigest()

        if not refresh:
            hit = self._cache_get(key)
            if hit is not None:
                return AiResult(hit, pid, model, True, self._warnings(prompt, hit))

        if not self._slots.acquire(timeout=self._slot_wait_s):
            raise AiNotReady("AI ещё обрабатывает другие запросы — подождите немного.", "busy")
        try:
            started = time.monotonic()
            text = provider.ask(prompt.system, prompt.user, model)
            log.info("AI %s/%s %s: %.1f с, %d симв.", pid, model, prompt.task, time.monotonic() - started, len(text))
        finally:
            self._slots.release()
        self._cache_put(key, pid, model, text)
        return AiResult(text, pid, model, False, self._warnings(prompt, text))

    @staticmethod
    def _warnings(prompt: Prompt, text: str) -> list[str]:
        warnings = list(prompt.data_warnings)
        if not prompt.audit:
            return warnings
        bad = unverified_numbers(text, prompt.facts)
        if not bad:
            return warnings
        shown = ", ".join(bad[:8])
        return warnings + [f"В ответе есть числа, которых нет в данных приложения: {shown}. Проверьте их сами: модель могла ошибиться."]

    # ---------- кэш ----------

    def _cache_get(self, key: str) -> str | None:
        with self._db_lock:
            row = self._conn.execute("SELECT response FROM ai_cache WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key: str, provider: str, model: str, text: str) -> None:
        with self._db_lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO ai_cache (key, provider, model, response, created_at) VALUES (?,?,?,?,?)",
                (key, provider, model, text, int(time.time())),
            )
            # держим кэш в пределах: старые ответы дешевле пересчитать, чем хранить вечно
            self._conn.execute(
                "DELETE FROM ai_cache WHERE key NOT IN (SELECT key FROM ai_cache ORDER BY created_at DESC LIMIT ?)",
                (CACHE_MAX_ROWS,),
            )


__all__ = ["AiError", "AiNotReady", "AiResult", "AiService"]
