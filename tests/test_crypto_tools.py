from __future__ import annotations

import time
from datetime import UTC, datetime

import ccxt
import pytest
from fastapi.testclient import TestClient

from compass.api.app import create_app
from compass.crypto_tools import (
    btc_regime,
    coin_profile,
    correlation,
    dca_plan,
    fee_in_quote,
    fifo_report,
    parse_cbr_usd,
    quote_warning,
    slippage_from_spread,
    spread_from_book,
)
from compass.journal import Entry
from compass.markets.crypto import CryptoAdapter
from compass.models import Candle
from tests.conftest import Env
from tests.test_markets import FakeExchange


def _c(ts: int, close: float, high: float | None = None, low: float | None = None, volume: float = 10) -> Candle:
    return Candle(ts, close, high if high is not None else close, low if low is not None else close, close, volume)


def test_young_and_thin_coin_profile() -> None:
    day = 86_400_000
    young = [_c(i * day, 1, volume=10) for i in range(30)]
    warnings = " ".join(coin_profile(young)["warnings"])
    assert "молодая" in warnings and "ликвидность" in warnings
    old = [_c(i * day, 2, volume=100_000) for i in range(400)]
    assert coin_profile(old)["warnings"] == []


def test_btc_regime_and_correlation() -> None:
    day = 86_400_000
    up = [_c(i * day, 100 * (1.01 ** i)) for i in range(30)]
    assert btc_regime(up)["regime"] == "up"
    flat_btc = {i * day: 100.0 for i in range(50)}
    same = {i * day: 50.0 + i for i in range(50)}
    # постоянный ряд не имеет дисперсии доходностей
    assert correlation(flat_btc, same)[0] is None
    linked = {i * day: 100 + (i % 7) * 3 + i * 0.2 for i in range(80)}
    alt = {i * day: 20 + (i % 7) * 1.5 + i * 0.05 for i in range(80)}
    corr, n = correlation(linked, alt)
    assert corr is not None and corr > 0.9 and n >= 40


def test_spread_sets_slippage_and_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    book = spread_from_book({"bids": [[100, 1]], "asks": [[102, 1]]})
    assert book is not None
    assert slippage_from_spread(book["spread_pct"]) == pytest.approx(book["spread_pct"] / 2)

    class Book(FakeExchange):
        def fetch_order_book(self, symbol, limit=5):
            return {"bids": [[100, 1]], "asks": [[102, 2]]}

    assert CryptoAdapter(exchange=Book([])).order_book_spread("BTC/USDT")["ask"] == 102

    class Down:
        id = "down"

        def fetch_ohlcv(self, *a, **k):
            raise ccxt.NetworkError("down")

    hour = 3_600_000
    up = FakeExchange([[int(time.time() * 1000) - hour, 1, 2, 0.5, 1.5, 9]])
    up.id = "up"
    adapter = CryptoAdapter(exchange=Down())
    adapter._fallback = up
    adapter.fallback_id = "up"
    got = adapter.fetch_candles("BTC/USDT", "1h", 0, 5)
    assert got[-1].close == 1.5 and adapter.source_id == "crypto:up"


def test_dca_fee_quote_and_rub() -> None:
    plan = dca_plan(1000, 4, 0.1, [10, 10, 10, 10])
    assert plan["cash_each"] == 250
    assert plan["qty"] == pytest.approx(4 * 250 * 0.999 / 10)
    assert fee_in_quote(0.01, 2000) == 20
    parsed = parse_cbr_usd({"Date": "2026-10-01T00:00:00", "Valute": {"USD": {"Value": 90.5, "Nominal": 1}}})
    assert parsed["usd_rub"] == 90.5
    assert "USDT" in (quote_warning("BTC") or "")


def test_fifo_matches_oldest_lot_in_the_sell_year() -> None:
    buy_ts = int(datetime(2025, 12, 1, tzinfo=UTC).timestamp() * 1000)
    sell_ts = int(datetime(2026, 2, 1, tzinfo=UTC).timestamp() * 1000)
    entries = [
        Entry("crypto", "ETH/USDT", "buy", 2, 100, buy_ts, fee=1, mode="real"),
        Entry("crypto", "ETH/USDT", "buy", 1, 200, buy_ts + 1, fee=0, mode="real"),
        Entry("crypto", "ETH/USDT", "sell", 2, 150, sell_ts, fee=1, mode="real"),
    ]
    report = fifo_report(entries, 2026, "crypto")
    # два старых лота по себестоимости (2*100+1)/2 = 100.5
    assert report["rows"][0]["cost"] == pytest.approx(201)
    assert report["rows"][0]["proceeds"] == pytest.approx(299)
    assert fifo_report(entries, 2025, "crypto")["rows"] == []


def test_tax_csv_and_exchange_setting(env: Env) -> None:
    env.services.journal.add(Entry("crypto", "BTC/USDT", "buy", 1, 10, 1_700_000_000_000, mode="real"))
    client = TestClient(create_app(env.services), base_url="http://127.0.0.1")
    csv = client.get("/api/journal/tax.csv?year=2026&market=crypto")
    assert csv.status_code == 200 and "FIFO" in csv.text
    assert client.put("/api/settings", json={"crypto_exchange": "bybit", "crypto_fallback": "bybit"}).status_code == 422
    assert client.put("/api/settings", json={"crypto_exchange": "bybit", "crypto_fallback": "okx"}).status_code == 200
