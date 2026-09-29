"""Атаки на локальный API со стороны чужих веб-страниц."""

import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app

TOKEN = "t" * 43
BODY = {"market": "moex", "symbol": "SBER", "side": "buy", "qty": 1, "price": 1, "ts": 1}


def _client(env, token=None, host="127.0.0.1"):
    return TestClient(create_app(env.services, session_token=token), base_url=f"http://{host}")


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example", "localhost.evil.example"])
def test_foreign_host_rejected(env, host):
    """Подмена DNS: браузер идёт на чужое имя, которое резолвится в 127.0.0.1."""
    c = _client(env)
    r = c.get("/api/settings", headers={"Host": host})
    assert r.status_code == 403
    assert c.get("/api/journal", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_local_hosts_allowed(env, host):
    assert _client(env).get("/api/settings", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1", "null", "http://127.0.0.1:1"])
def test_foreign_origin_cannot_write_journal(env, origin):
    c = _client(env)
    r = c.post("/api/journal", json=BODY, headers={"Origin": origin})
    assert r.status_code == 403
    assert env.services.journal.list() == []


def test_same_origin_write_allowed(env):
    c = _client(env)
    r = c.post("/api/journal", json=BODY, headers={"Origin": "http://127.0.0.1", "Host": "127.0.0.1"})
    assert r.status_code == 201


def test_token_exchange_sets_strict_cookie_and_api_requires_it(env):
    c = _client(env, TOKEN)
    assert c.get("/api/settings").status_code == 401
    assert c.get("/api/health").status_code == 200
    assert c.get("/?token=wrong", follow_redirects=False).status_code == 401
    assert c.get("/api/settings").status_code == 401
    r = c.get(f"/?token={TOKEN}", follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/api/settings").status_code == 200


def test_wrong_cookie_rejected(env):
    c = _client(env, TOKEN)
    c.cookies.set("compass_session", "x" * 43)
    assert c.get("/api/journal").status_code == 401
    assert c.post("/api/journal", json=BODY).status_code == 401
    assert env.services.journal.list() == []
