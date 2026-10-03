"""Провайдеры AI. Единый интерфейс: `ask(system, prompt, model) -> текст`.

Провайдер видит только текст, который ему передал код (посчитанные факты); доступа к
файлам, базе и торговым функциям у него нет.

Cursor CLI запускается в режиме `--mode ask` (только чтение) из пустой папки. На Windows
шим `cursor-agent.cmd` НЕ используется: текст запроса прошёл бы через cmd.exe, и символы
вроде `&` или `"` из заметок журнала могли бы сработать как команды. Вместо этого
вызывается `node.exe index.js ...` напрямую со списком аргументов.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

log = logging.getLogger("compass.ai")

MAX_PROMPT_CHARS = 24_000  # предел на текст запроса: лимит командной строки Windows ~32k
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/\[\],=][A-Za-z0-9._:/\[\],=\-]{0,99}$")


class AiError(RuntimeError):
    """AI не смог ответить (сбой провайдера, таймаут, отказ модели)."""


class AiNotReady(AiError):
    """AI не настроен: выключен, нет согласия на отправку данных, провайдер недоступен."""

    def __init__(self, message: str, code: str = "not_ready") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    label: str


class Provider(Protocol):
    id: str
    name: str
    cloud: bool  # True — данные уходят на сторонний сервер
    default_model: str

    def available(self) -> tuple[bool, str]:
        """(готов ли, пояснение если нет)."""
        ...

    def models(self) -> list[ModelInfo]: ...

    def ask(self, system: str, prompt: str, model: str) -> str: ...


def check_model_id(model: str) -> str:
    """Идентификатор модели идёт в командную строку — принимаем только безопасный алфавит
    и не даём начинаться с «-» (иначе это был бы чужой флаг CLI)."""
    if not MODEL_ID_RE.match(model):
        raise AiNotReady(f"Недопустимое имя модели: {model!r}", "bad_model")
    return model


# ---------------------------------------------------------------- Cursor CLI


def _version_key(name: str) -> tuple[int, ...] | None:
    """YYYY.M.D-[HH-MM-SS-]commit — как в шиме Cursor: сравниваем по дате."""
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})(?:-(\d{2})-(\d{2})-(\d{2}))?-[a-f0-9]+$", name)
    if not m:
        return None
    return tuple(int(g) if g else 0 for g in m.groups())


# В настройках Компаса только эти две модели. Fast-варианты сюда не входят:
# Composer 2.5 — обычный ответ, Grok 4.7 High — более глубокий разбор тех же фактов.
CURSOR_MODELS = (
    ModelInfo("composer-2.5", "Composer 2.5"),
    ModelInfo("grok-4.7-high", "Grok 4.7 High"),
)
_FAST_ALIASES = {"composer-2.5-fast": "composer-2.5", "grok-4.7-high-fast": "grok-4.7-high"}


def cursor_session_token() -> str | None:
    """Токен уже открытого Cursor на этом компьютере. В базу Компаса и в логи не пишется."""
    if os.environ.get("CURSOR_AUTH_TOKEN") or os.environ.get("CURSOR_API_KEY"):
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    db = Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if not db.is_file():
        return None
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        row = con.execute("SELECT value FROM ItemTable WHERE key = ?", ("cursorAuth/accessToken",)).fetchone()
        con.close()
    except sqlite3.Error:
        return None
    token = row[0] if row else None
    if not isinstance(token, str) or len(token) < 20:
        return None
    return token


def locate_cursor_agent() -> list[str] | None:
    """Префикс команды для запуска Cursor CLI без посредников или None, если не найден."""
    shim = shutil.which("cursor-agent") or shutil.which("agent")
    if not shim and sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            installed = Path(local) / "cursor-agent" / "cursor-agent.cmd"
            if installed.is_file():
                shim = str(installed)
    if not shim:
        return None
    if sys.platform != "win32":
        return [shim]  # на POSIX шим — обычный скрипт, cmd.exe нет
    base = Path(shim).resolve().parent
    node = "node.exe"
    candidates = [base]
    versions = base / "versions"
    if versions.is_dir():
        keyed = [(k, p) for p in versions.iterdir() if p.is_dir() and (k := _version_key(p.name))]
        candidates = [p for _, p in sorted(keyed, reverse=True)] + candidates
    for d in candidates:
        if (d / node).is_file() and (d / "index.js").is_file():
            return [str(d / node), str(d / "index.js")]
    return None


class CursorProvider:
    id = "cursor"
    name = "Cursor CLI"
    cloud = True
    default_model = "composer-2.5"

    def __init__(
        self,
        workdir: Path,
        locate: Callable[[], list[str] | None] = locate_cursor_agent,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        timeout_s: float = 180.0,
    ) -> None:
        self._workdir, self._locate, self._run, self._timeout = workdir, locate, run, timeout_s
        self._models_cache: tuple[float, list[ModelInfo]] | None = None

    def _exec(self, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        prefix = self._locate()
        if prefix is None:
            raise AiNotReady("Cursor CLI не найден. Установите его и выполните вход (cursor-agent login).", "unavailable")
        self._workdir.mkdir(parents=True, exist_ok=True)  # пустая папка: агенту нечего читать
        env = {**os.environ, "CURSOR_INVOKED_AS": "cursor-agent", "NO_COLOR": "1"}
        token = cursor_session_token()
        if token and "CURSOR_AUTH_TOKEN" not in env and "CURSOR_API_KEY" not in env:
            env["CURSOR_AUTH_TOKEN"] = token
        env.setdefault("NODE_COMPILE_CACHE", str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "cursor-compile-cache"))
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # без мигающей консоли
        try:
            return self._run(
                [*prefix, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=self._workdir,
                env=env,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired as e:
            raise AiError(f"Cursor CLI не ответил за {timeout:.0f} с") from e
        except OSError as e:
            raise AiError(f"Не удалось запустить Cursor CLI: {e}") from e

    def available(self) -> tuple[bool, str]:
        if self._locate() is None:
            return False, "Cursor CLI не найден"
        try:
            r = self._exec(["status"], 30)
        except AiError as e:
            return False, str(e)
        text = (r.stdout + r.stderr).lower()
        if r.returncode == 0 and "logged in" in text:
            return True, ""
        # Сессия редактора не проходит команду status, но с тем же токеном список моделей открывается.
        try:
            listed = self._exec(["--list-models"], 60)
        except AiError as e:
            return False, str(e)
        if listed.returncode == 0 and "composer-2.5" in listed.stdout:
            return True, ""
        return False, "Нет входа в Cursor: выполните `cursor-agent login`"

    def models(self) -> list[ModelInfo]:
        if self._models_cache and time.monotonic() - self._models_cache[0] < 600:
            return self._models_cache[1]
        r = self._exec(["--list-models"], 60)
        if r.returncode != 0:
            raise AiError("Не удалось получить список моделей Cursor")
        out: list[ModelInfo] = []
        for raw in r.stdout.splitlines():
            line = raw.replace("\u200b", "").strip()
            m = re.match(r"^([A-Za-z0-9._:/\[\],=\-]+) - (.+)$", line)
            if m and MODEL_ID_RE.match(m.group(1)):
                out.append(ModelInfo(m.group(1), m.group(2).replace("(current, default)", "").strip()))
        allowed = {m.id for m in CURSOR_MODELS}
        by_id = {m.id: m for m in out if m.id in allowed}
        chosen = [by_id.get(m.id, m) for m in CURSOR_MODELS]
        self._models_cache = (time.monotonic(), chosen)
        return chosen

    def ask(self, system: str, prompt: str, model: str) -> str:
        model = _FAST_ALIASES.get(model, model)
        model = check_model_id(model or self.default_model)
        full = (
            f"ИНСТРУКЦИИ:\n{system}\n\n"
            "Отвечай только текстом. Не используй инструменты, не читай и не изменяй файлы, "
            "не запускай команды.\n\n"
            f"ЗАДАЧА:\n{prompt}"
        )
        if len(full) > MAX_PROMPT_CHARS:
            raise AiError("Запрос слишком длинный для AI. Сократите период или число записей.")
        r = self._exec(
            ["-p", "--mode", "ask", "--output-format", "json", "--trust", "--workspace", str(self._workdir),
             "--model", model, full],
            self._timeout,
        )
        payload = _last_json_line(r.stdout)
        if payload is None:
            tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["пустой ответ"]
            raise AiError(f"Cursor CLI вернул неожиданный ответ: {tail[0][:200]}")
        if r.returncode != 0 or payload.get("is_error"):
            raise AiError(f"Cursor CLI: {str(payload.get('result') or 'ошибка')[:300]}")
        text = str(payload.get("result") or "").strip()
        if not text:
            raise AiError("Cursor CLI вернул пустой ответ")
        return text


def _last_json_line(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    return None


# ---------------------------------------------------------------- Claude API

CLAUDE_MODELS = (
    ModelInfo("claude-opus-5-5", "Claude Opus 5.5"),
    ModelInfo("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ModelInfo("claude-haiku-4-5", "Claude Haiku 4.5"),
)
_EFFORT_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}  # Haiku 4.5 параметр effort отвергает
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_OFFICIAL_HOST = "api.anthropic.com"


class ClaudeProvider:
    id = "claude"
    name = "Claude API"
    cloud = True
    default_model = "claude-opus-5-5"

    def __init__(self, client_factory: Callable[[], Any] | None = None, max_tokens: int = 8000) -> None:
        self._factory = client_factory
        self._max_tokens = max_tokens

    def _client(self) -> Any:
        if self._factory:
            return self._factory()
        try:
            import anthropic
        except ImportError as e:
            raise AiNotReady("Пакет anthropic не установлен (pip install anthropic)", "unavailable") from e
        return anthropic.Anthropic()

    def available(self) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "Пакет anthropic не установлен"
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True, ""
        # SDK умеет брать вход из профиля `ant auth login` — заранее это не проверить
        return True, "Ключ ANTHROPIC_API_KEY не найден в окружении; сработает, только если выполнен `ant auth login`"

    def models(self) -> list[ModelInfo]:
        return list(CLAUDE_MODELS)

    def ask(self, system: str, prompt: str, model: str) -> str:
        model = check_model_id(model or self.default_model)
        client = self._client()
        import anthropic
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if model in _EFFORT_MODELS:
            kwargs["output_config"] = {"effort": "low"}  # короткие пояснения, глубокие рассуждения не нужны
        # Серверный fallback при отказе классификатора: только на официальном API и только для моделей, где он есть
        base = str(getattr(client, "base_url", ""))
        if model in _FALLBACK_MODELS and _OFFICIAL_HOST in base:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        try:
            resp = client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AiNotReady("Claude API: не удалось войти. Задайте ANTHROPIC_API_KEY или выполните `ant auth login`.", "auth") from e
        except anthropic.PermissionDeniedError as e:
            raise AiError("Claude API: ключу не хватает прав") from e
        except anthropic.NotFoundError as e:
            raise AiError(f"Claude API: модель {model} недоступна") from e
        except anthropic.RateLimitError as e:
            raise AiError("Claude API: превышен лимит запросов, попробуйте позже") from e
        except anthropic.BadRequestError as e:
            raise AiError(f"Claude API отклонил запрос: {getattr(e, 'message', e)}") from e
        except anthropic.APIConnectionError as e:
            raise AiError("Claude API: нет связи с сервером") from e
        except anthropic.APIStatusError as e:
            raise AiError(f"Claude API: ошибка сервера ({e.status_code})") from e
        if resp.stop_reason == "refusal":
            raise AiError("Модель отказалась отвечать на этот запрос.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise AiError("Claude API вернул пустой ответ")
        if resp.stop_reason == "max_tokens":
            text += "\n\n[Ответ обрезан по длине.]"
        return text


# ---------------------------------------------------------------- Ollama (локально)


class OllamaProvider:
    id = "ollama"
    name = "Ollama (локально)"
    cloud = False
    default_model = ""

    def __init__(self, host: str | None = None, client: httpx.Client | None = None, timeout_s: float = 300.0) -> None:
        self._host = (host or os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        if not self._host.startswith("http"):
            self._host = "http://" + self._host
        self._client = client or httpx.Client(timeout=timeout_s)

    def available(self) -> tuple[bool, str]:
        try:
            self._client.get(f"{self._host}/api/tags", timeout=3).raise_for_status()
            return True, ""
        except httpx.HTTPError:
            return False, "Ollama не запущена (ожидается на " + self._host + ")"

    def models(self) -> list[ModelInfo]:
        try:
            r = self._client.get(f"{self._host}/api/tags", timeout=5)
            r.raise_for_status()
            return [ModelInfo(m["name"], m["name"]) for m in r.json().get("models", []) if "name" in m]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise AiError("Не удалось получить список моделей Ollama") from e

    def ask(self, system: str, prompt: str, model: str) -> str:
        if not model:
            raise AiNotReady("Выберите модель Ollama в настройках (нужна скачанная: ollama pull <модель>)", "no_model")
        model = check_model_id(model)
        try:
            r = self._client.post(
                f"{self._host}/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                },
            )
            r.raise_for_status()
            text = str(r.json().get("message", {}).get("content", "")).strip()
        except httpx.HTTPStatusError as e:
            raise AiError(f"Ollama вернула ошибку {e.response.status_code}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise AiError("Нет связи с Ollama") from e
        if not text:
            raise AiError("Ollama вернула пустой ответ")
        return text
