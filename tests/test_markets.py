from __future__ import annotations

import ccxt
import httpx
import pytest

from compass.markets import CryptoAdapter, MarketError, MoexAdapter

MSK_2026_01_05_10_00_UTC_MS = 1767596400000  # 2026-01-05 10:00 МСК = 07:00 UTC


def _moex_row(i: int) -> list:
    return [100 + i, 101 + i, 102 + i, 99 + i, 1000.0, 50 + i, f"2026-01-05 10:{i % 60:02d}:00", "x"]


def _candles_json(rows: list[list]) -> dict:
    cols = ["open", "close", "high", "low", "value", "volume", "begin", "end"]
    return {"candles": {"columns": cols, "data": rows}}


def test_moex_parses_msk_time_and_ohlc() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_candles_json([_moex_row(0)]))

    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(handler)))
    (c,) = a.fetch_candles("SBER", "1h", None, 10)
    assert c.ts == MSK_2026_01_05_10_00_UTC_MS
    assert (c.open, c.close, c.high, c.low, c.volume) == (100, 101, 102, 99, 50)


def test_moex_paginates_until_short_page() -> None:
    offsets: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        off = int(req.url.params["start"])
        offsets.append(str(off))
        # две полные страницы, потом короткая
        n = 500 if off < 1000 else 7
        rows = [_moex_row(off + i) for i in range(n)]
        for k, r in enumerate(rows):  # уникальные минуты, чтобы не склеить строки
            r[6] = f"2026-01-{1 + (off + k) // 1440:02d} {((off + k) % 1440) // 60:02d}:{(off + k) % 60:02d}:00"
        return httpx.Response(200, json=_candles_json(rows))

    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(handler)))
    got = a.fetch_candles("SBER", "1m", None, 5000)
    assert offsets == ["0", "500", "1000"]
    assert len(got) == 1007


def test_moex_limit_keeps_latest() -> None:
    rows = [_moex_row(i) for i in range(30)]
    a = MoexAdapter(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_candles_json(rows))))
    )
    got = a.fetch_candles("SBER", "1m", None, 5)
    assert len(got) == 5 and got[-1].open == 129


def test_moex_http_error_becomes_market_error() -> None:
    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(MarketError):
        a.fetch_candles("SBER", "1d", None, 10)


def test_moex_rejects_unknown_timeframe() -> None:
    with pytest.raises(MarketError):
        MoexAdapter(httpx.Client()).fetch_candles("SBER", "4h", None, 10)


def test_moex_search_keeps_only_traded_shares() -> None:
    payload = {
        "securities": {
            "columns": ["secid", "shortname", "is_traded", "group"],
            "data": [
                ["SBER", "Сбербанк", 1, "stock_shares"],
                ["SBERP", "Сбербанк-п", 1, "stock_shares"],
                ["OLD", "Старая", 0, "stock_shares"],
                ["SU26238", "ОФЗ", 1, "stock_bonds"],
            ],
        }
    }
    a = MoexAdapter(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
        catalog=[],
    )
    assert [i.symbol for i in a.search("SBER")] == ["SBER", "SBERP"]


class FakeExchange:
    id = "fake"

    def __init__(self, series: list[list[float]]) -> None:
        self.series = series
        self.calls: list[int | None] = []

    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        self.calls.append(since)
        return [r for r in self.series if since is None or r[0] >= since][:limit]

    def load_markets(self):
        return {
            "BTC/USDT": {"symbol": "BTC/USDT", "spot": True},
            "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "spot": False},
            "ETH/USDT": {"symbol": "ETH/USDT", "spot": True},
        }


def test_crypto_sorts_dedups_and_limits() -> None:
    hour = 3_600_000
    series = [[i * hour, 1, 2, 0.5, 1.5, 10] for i in (3, 1, 2, 2)]  # неупорядочено, дубль
    a = CryptoAdapter(exchange=FakeExchange(series))
    got = a.fetch_candles("BTC/USDT", "1h", 0, 2)
    assert [c.ts for c in got] == [2 * hour, 3 * hour]


def test_crypto_does_not_loop_when_cursor_stuck() -> None:
    ex = FakeExchange([[1000, 1, 1, 1, 1, 1], [1000, 1, 1, 1, 1, 1]])
    CryptoAdapter(exchange=ex).fetch_candles("BTC/USDT", "1h", 5_000_000, 100)
    assert len(ex.calls) <= 2


def test_bundled_catalogs_include_broker_and_okx_names() -> None:
    from compass.catalog import moex_catalog, okx_catalog

    moex = moex_catalog()
    symbols = {row["symbol"] for row in moex}
    assert {"SBER", "TMOS", "USD000UTSTOM"} <= symbols
    assert any(row["kind"] == "bond" for row in moex)
    assert any(row["symbol"] == "BTC/USDT" for row in okx_catalog())
    assert len(okx_catalog()) > 100


def test_catalog_search_ranks_ticker_above_name() -> None:
    catalog = [
        {"symbol": "SBER", "name": "Сбербанк", "lot": 10, "engine": "stock", "market": "shares", "board": "TQBR"},
        {"symbol": "SBERP", "name": "Сбербанк-п", "lot": 10, "engine": "stock", "market": "shares", "board": "TQBR"},
        {"symbol": "USD000UTSTOM", "name": "USD_TOD", "lot": 1000, "engine": "currency", "market": "selt", "board": "CETS"},
    ]
    a = MoexAdapter(httpx.Client(), catalog=catalog)
    assert [i.symbol for i in a.search("sber")] == ["SBER", "SBERP"]
    assert a.lot_size("SBER") == 10
    assert a.catalog_size() == 3


def test_crypto_search_spot_only() -> None:
    a = CryptoAdapter(exchange=FakeExchange([]))
    assert [i.symbol for i in a.search("btc")] == ["BTC/USDT"]


def _moex_info_json(**over) -> dict:
    sec = {"SECID": "SBER", "LOTSIZE": 1, "MINSTEP": 0.01, "DECIMALS": 2, "CURRENCYID": "SUR", "FACEUNIT": "SUR",
           "FACEVALUE": 3, "STATUS": "A", **over}
    md = {"BOARDID": "TQBR", "TRADINGSTATUS": over.pop("_status", "T")}
    return {
        "securities": {"columns": list(sec), "data": [list(sec.values())]},
        "marketdata": {"columns": list(md), "data": [list(md.values())]},
    }


def test_moex_instrument_info_reads_lot_step_currency_and_status() -> None:
    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_moex_info_json()))),
                    catalog=[])
    info = a.instrument_info("SBER")
    assert (info.lot, info.price_step, info.currency, info.trading_open) == (1, 0.01, "RUB", True)
    assert info.price_unit == "money" and info.complete and info.source == "moex:iss"
    closed = MoexAdapter(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_moex_info_json() | {
            "marketdata": {"columns": ["BOARDID", "TRADINGSTATUS"], "data": [["TQBR", "N"]]}}))), catalog=[])
    assert closed.instrument_info("SBER").trading_open is False


def test_moex_bond_price_is_percent_of_face_with_accrued() -> None:
    body = _moex_info_json(SECID="SU26238RMFS4", FACEVALUE=1000, ACCRUEDINT=23.15, MINSTEP=0.001, DECIMALS=3)
    cat = [{"symbol": "SU26238RMFS4", "lot": 1, "engine": "stock", "market": "bonds", "board": "TQOB", "kind": "bond"}]
    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))), catalog=cat)
    info = a.instrument_info("SU26238RMFS4")
    assert info.price_unit == "percent_of_face" and info.face_value == 1000 and info.accrued == 23.15


def test_moex_info_falls_back_to_catalog_lot_and_says_incomplete() -> None:
    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    cat = [{"symbol": "GAZP", "lot": 10, "engine": "stock", "market": "shares", "board": "TQBR", "kind": "share"}]
    info = MoexAdapter(httpx.Client(transport=httpx.MockTransport(down)), catalog=cat).instrument_info("GAZP")
    assert info.lot == 10 and not info.complete and info.price_step is None and info.currency is None
    with pytest.raises(MarketError):
        MoexAdapter(httpx.Client(transport=httpx.MockTransport(down)), catalog=[]).instrument_info("ZZZZ")


class InfoExchange(FakeExchange):
    precisionMode = ccxt.TICK_SIZE

    def load_markets(self):
        return {"BTC/USDT": {"symbol": "BTC/USDT", "spot": True, "quote": "USDT", "active": True,
                             "precision": {"amount": 1e-08, "price": 0.1},
                             "limits": {"amount": {"min": 1e-05}, "cost": {"min": 5.0}}}}


def test_crypto_instrument_info_uses_exchange_precision_and_minimums() -> None:
    info = CryptoAdapter(exchange=InfoExchange([])).instrument_info("BTC/USDT")
    assert (info.qty_step, info.price_step, info.min_qty, info.min_cost, info.currency) == (1e-08, 0.1, 1e-05, 5.0, "USDT")
    with pytest.raises(MarketError):
        CryptoAdapter(exchange=InfoExchange([])).instrument_info("NOPE/USDT")
