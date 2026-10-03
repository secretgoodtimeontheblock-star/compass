"""Крипто-маршруты: справка по монете, DCA, курс рубля, налоговая выгрузка FIFO.

Оповещения о цене живут в alerts.py — здесь их не дублируем.
"""

from __future__ import annotations

import csv
import io
import time
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from compass.crypto_tools import (
    EDUCATION,
    TAKER_FEE_PCT,
    btc_link_note,
    btc_regime,
    coin_profile,
    correlation,
    dca_plan,
    fee_in_quote,
    fifo_report,
    parse_cbr_usd,
    quote_warning,
    rub_per_usdt,
)
from compass.markets.base import MarketError
from compass.markets.crypto import CRYPTO_EXCHANGES
from compass.models import closed_candles

if TYPE_CHECKING:
    from compass.api.app import Services

_CBR = "https://www.cbr-xml-daily.ru/daily_json.js"
_rub_cache: dict[str, Any] = {}


class DcaRequest(BaseModel):
    budget: float = Field(gt=0)
    parts: int = Field(ge=2, le=365)
    fee_pct: float = Field(TAKER_FEE_PCT, ge=0, lt=100)
    prices: list[float] | None = None


class FeeCoinRequest(BaseModel):
    fee_coin: float = Field(ge=0)
    price: float = Field(gt=0)


def register_crypto_routes(app: FastAPI, svc: Services) -> None:
    @app.get("/api/crypto/exchanges")
    def exchanges() -> dict:
        a = svc.adapters.get("crypto")
        return {
            "options": list(CRYPTO_EXCHANGES),
            "active": getattr(a, "exchange_id", None),
            "fallback": getattr(a, "fallback_id", None),
            "live_stream": getattr(a, "exchange_id", None) == "okx" and svc.live is not None,
            "note": "Живой поток свечей есть только у OKX. Остальные биржи обновляются опросом. Только спот.",
        }

    @app.get("/api/crypto/brief")
    def brief(symbol: str) -> dict:
        if "crypto" not in svc.adapters:
            raise HTTPException(404, "Крипторынок не подключён")
        daily = _daily(symbol)
        profile = coin_profile(daily)
        btc = _daily("BTC/USDT") if symbol.upper() != "BTC/USDT" else daily
        regime = btc_regime(btc)
        corr, overlap = correlation(
            {c.ts: c.close for c in daily}, {c.ts: c.close for c in btc}
        ) if symbol.upper() != "BTC/USDT" else (None, 0)
        link = btc_link_note(symbol, corr, overlap)
        currency = _quote(symbol)
        spread = None
        book = getattr(svc.adapters["crypto"], "order_book_spread", None)
        if book:
            try:
                spread = book(symbol)
            except MarketError:
                spread = None
        warnings = list(profile["warnings"])
        q = quote_warning(currency)
        if q:
            warnings.append(q)
        if regime["regime"] != "unknown" and not symbol.upper().startswith("BTC/"):
            warnings.append(regime["note"])
        if link and corr is not None and corr >= 0.7:
            warnings.append(link)
        return {
            "symbol": symbol,
            "exchange": getattr(svc.adapters["crypto"], "exchange_id", None),
            "currency": currency,
            "profile": profile,
            "btc": regime,
            "btc_correlation": corr,
            "btc_overlap": overlap,
            "btc_link": link,
            "spread": spread,
            "warnings": warnings,
            "education": list(EDUCATION),
            "rub": _rub(),
            "fee_note": (
                "Комиссия в расчётах — одна ставка 0,1% (типичный taker), без скидки за объём. "
                "Если биржа списала её в монете, в журнал пишите сумму в USDT: количество монеты × цена."
            ),
        }

    @app.post("/api/crypto/dca")
    def dca(req: DcaRequest) -> dict:
        return dca_plan(req.budget, req.parts, req.fee_pct, req.prices)

    @app.post("/api/crypto/fee-quote")
    def fee_quote(req: FeeCoinRequest) -> dict:
        quote = fee_in_quote(req.fee_coin, req.price)
        return {"fee_quote": quote, "note": "Эту сумму и записывайте в поле комиссии журнала."}

    @app.get("/api/journal/tax.csv")
    def tax_csv(year: int | None = Query(None), market: str | None = Query("crypto")) -> Response:
        report = fifo_report(svc.journal.list(mode="real"), year, market)
        buf = io.StringIO()
        buf.write("﻿")
        writer = csv.writer(buf)
        writer.writerow([report["disclaimer"]])
        writer.writerow(["market", "symbol", "year", "sell_ts_ms", "qty", "proceeds", "cost", "pnl"])
        for row in report["rows"]:
            writer.writerow([row["market"], row["symbol"], row["year"], row["sell_ts"], row["qty"], row["proceeds"], row["cost"], row["pnl"]])
        writer.writerow([])
        writer.writerow(["pnl_total", report["pnl"]])
        name = f"compass-fifo-{market or 'all'}-{year or 'all'}.csv"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    def _daily(symbol: str) -> list:
        try:
            res = svc.cache.get("crypto", symbol, "1d", 500)
        except MarketError:
            return []
        return closed_candles(res.candles, "1d", svc.now_ms())

    def _quote(symbol: str) -> str | None:
        info = getattr(svc.adapters["crypto"], "instrument_info", None)
        if info is None:
            return symbol.split("/")[-1] if "/" in symbol else None
        try:
            return info(symbol).currency
        except MarketError:
            return symbol.split("/")[-1] if "/" in symbol else None

    def _rub() -> dict | None:
        now = time.time()
        cached = _rub_cache.get("data")
        if cached and now - _rub_cache.get("at", 0) < 3600:
            return cached
        try:
            r = httpx.get(_CBR, timeout=8.0)
            r.raise_for_status()
            parsed = parse_cbr_usd(r.json())
        except (httpx.HTTPError, ValueError):
            return cached
        data = {**parsed, **rub_per_usdt(parsed["usd_rub"])}
        _rub_cache["data"] = data
        _rub_cache["at"] = now
        return data
