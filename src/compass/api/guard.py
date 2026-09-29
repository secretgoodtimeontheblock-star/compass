"""Защита локального API от чужих веб-страниц в браузере пользователя.

Сервер слушает 127.0.0.1, но браузер может слать туда запросы с любого сайта
(и через подмену DNS — под чужим именем хоста). Поэтому:
- Host должен быть локальным: подмена DNS приходит с чужим именем;
- Origin, если браузер его прислал, должен совпадать с адресом самого сервера;
- окно приложения получает одноразовый токен, который меняется на cookie
  HttpOnly+SameSite=Strict; без cookie /api/* отвечает 401.
Это защита от чужих сайтов, а не от программ на этом же компьютере: любая
локальная программа может прочитать токен из командной строки процесса.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE = "compass_session"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _hostname(host_header: str) -> str:
    # urlsplit понимает и «host:port», и «[::1]:port»
    return (urlsplit(f"//{host_header}").hostname or "").lower() if host_header else ""


def _host_ok(host_header: str) -> bool:
    name = _hostname(host_header)
    return (f"[{name}]" if ":" in name else name) in LOCAL_HOSTS


def install_guard(app: FastAPI, session_token: str | None) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if not _host_ok(host):
            return JSONResponse({"detail": "Недопустимый адрес сервера"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None:
            parts = urlsplit(origin)
            if parts.scheme != "http" or parts.netloc.lower() != host.lower():
                return JSONResponse({"detail": "Запрос должен идти из Compass"}, status_code=403)

        if session_token is None:
            return await call_next(request)

        supplied = request.query_params.get("token")
        if request.url.path == "/" and supplied is not None:
            if not hmac.compare_digest(supplied, session_token):
                return JSONResponse({"detail": "Неверный токен сессии"}, status_code=401)
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, session_token, httponly=True, samesite="strict", path="/")
            return resp
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            cookie = request.cookies.get(COOKIE, "")
            if not hmac.compare_digest(cookie, session_token):
                return JSONResponse({"detail": "Нет сессии: откройте Compass через приложение"}, status_code=401)
        return await call_next(request)
