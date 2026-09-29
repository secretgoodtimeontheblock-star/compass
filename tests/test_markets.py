from __future__ import annotations

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


def _catalog_client(calls: list[str]) -> httpx.Client:
    cols = ["SECID", "SHORTNAME", "SECNAME", "LOTSIZE", "STATUS"]
    boards = {
        "TQBR": [
            ["SBER", "Сбербанк", "Сбербанк России ПАО ао", 10, "A"],
            ["SBERP", "Сбербанк-п", "Сбербанк России ПАО ап", 10, "A"],
            ["GAZP", "ГАЗПРОМ ао", "Газпром ПАО ао", 10, "A"],
            ["OLD", "Старая", "Старая", 1, "N"],
        ],
        "TQTF": [["TMOS", "TMOS ETF", "Тинькофф iMOEX", 1, "A"]],
        "TQIF": [],
    }

    def handler(req: httpx.Request) -> httpx.Response:
        board = req.url.path.split("/boards/")[1].split("/")[0]
        calls.append(board)
        return httpx.Response(200, json={"securities": {"columns": cols, "data": boards[board]}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_moex_search_uses_full_catalog_and_ranks() -> None:
    calls: list[str] = []
    a = MoexAdapter(_catalog_client(calls))
    assert [i.symbol for i in a.search("SBER")] == ["SBER", "SBERP"]
    assert [i.symbol for i in a.search("газпром")] == ["GAZP"]  # по названию
    assert [i.symbol for i in a.search("tmos")] == ["TMOS"]  # ETF с другой площадки
    assert a.search("old") == []  # не торгуется
    assert calls == ["TQBR", "TQTF", "TQIF"]  # каталог загружен один раз


def test_moex_lot_size_and_board_come_from_catalog() -> None:
    a = MoexAdapter(_catalog_client([]))
    assert a.lot_size("SBER") == 10
    assert a._board_of("TMOS") == "TQTF"
    with pytest.raises(MarketError):
        a.lot_size("NOPE")


def test_moex_catalog_failure_is_market_error() -> None:
    a = MoexAdapter(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    with pytest.raises(MarketError):
        a.search("SBER")


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
            "WBTC/BTC": {"symbol": "WBTC/BTC", "spot": True},
            "BTC/EUR": {"symbol": "BTC/EUR", "spot": True},
            "DEAD/USDT": {"symbol": "DEAD/USDT", "spot": True, "active": False},
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


def test_crypto_search_spot_only() -> None:
    a = CryptoAdapter(exchange=FakeExchange([]))
    # база раньше подстроки, USDT-пары раньше остальных; фьючерсы и неактивные — нет
    assert [i.symbol for i in a.search("btc")] == ["BTC/USDT", "BTC/EUR", "WBTC/BTC"]
    assert [i.symbol for i in a.search("btcusdt")] == ["BTC/USDT"]
    assert a.search("dead") == []
