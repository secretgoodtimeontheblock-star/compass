"""Готовые наборы инструментов: только реально торгуемое, без дублей, массовое добавление."""

from __future__ import annotations

from fastapi.testclient import TestClient

from compass import presets
from compass.api.app import create_app
from compass.catalog import moex_catalog, okx_catalog
from tests.conftest import Env


def test_every_listed_ticker_exists_in_the_exchange_catalog_and_groups_are_not_empty() -> None:
    moex = {r["symbol"] for r in moex_catalog()}
    okx = {r["symbol"] for r in okx_catalog()}
    for g in presets.groups("moex"):
        assert g["instruments"] and all(i["symbol"] in moex for i in g["instruments"]), g["id"]
    for g in presets.groups("crypto"):
        assert g["instruments"] and all(i["symbol"] in okx and i["symbol"].endswith("/USDT") for i in g["instruments"]), g["id"]
    assert presets.groups("nope") == []


def test_sets_are_broad_and_dropped_tickers_are_only_those_missing_from_catalog() -> None:
    moex = presets.groups("moex")
    crypto = presets.groups("crypto")
    assert len(moex) >= 10 and sum(len(g["instruments"]) for g in moex) >= 60
    assert len({i["symbol"] for g in crypto for i in g["instruments"]}) >= 30
    # названия берутся из справочника, а не выдумываются
    sber = next(i for g in moex for i in g["instruments"] if i["symbol"] == "SBER")
    assert sber["name"] == "Сбербанк" and sber["kind"] == "share"
    listed = {s for g in presets.MOEX_GROUPS for s in g.symbols}
    present = {i["symbol"] for g in moex for i in g["instruments"]}
    assert listed - present == set()  # все заявленные акции МосБиржи есть в справочнике


def test_group_ids_and_icons_are_unique_and_known_to_the_ui() -> None:
    for defs in (presets.MOEX_GROUPS, presets.CRYPTO_GROUPS):
        assert len({g.id for g in defs}) == len(defs)
        assert all(g.icon and g.note and g.symbols for g in defs)


def test_presets_api_marks_watched_and_adds_only_missing(env: Env) -> None:
    c = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    c.post("/api/watchlist", json={"market": "moex", "symbol": "SBER", "name": "Сбербанк"})
    groups = c.get("/api/presets", params={"market": "moex"}).json()
    banks = next(g for g in groups if g["id"] == "banks")
    assert next(i for i in banks["instruments"] if i["symbol"] == "SBER")["watched"] is True
    assert not next(i for i in banks["instruments"] if i["symbol"] == "VTBR")["watched"]
    r = c.post("/api/presets/moex/banks/add").json()
    assert r["already"] == 1 and r["added"] == len(banks["instruments"]) - 1
    again = c.post("/api/presets/moex/banks/add").json()
    assert again["added"] == 0
    symbols = {i["symbol"] for i in c.get("/api/watchlist").json()}
    assert {"SBER", "VTBR", "MOEX"} <= symbols
    assert c.post("/api/presets/moex/nope/add").status_code == 404
    assert c.get("/api/presets", params={"market": "nope"}).status_code == 404
