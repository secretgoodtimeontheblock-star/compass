"""То, чего спотовому движку не хватает именно на крипте: профиль монеты, режим биткоина,
спред стакана, план DCA, курс в рубли и FIFO по журналу.

Здесь нет фьючерсов, плеча, фандинга и стейкинга. Счёт крипты в приложении — USDT.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

import numpy as np

from compass.journal import Entry
from compass.models import Candle

YOUNG_DAYS = 240
THIN_QUOTE_VOLUME = 100_000.0  # медианный дневной оборот в валюте котировки
BTC_WINDOW = 20
BTC_MOVE = 0.05
MIN_CORR_BARS = 40
TAKER_FEE_PCT = 0.1

EDUCATION = (
    "Только спот и лонг. Стейкинг, фандинг и плечо здесь не считаются и не предлагаются.",
    "Монета на бирже — не ваши ключи: биржа может заморозить вывод. Seed-фразу никому не сообщают.",
    "«Сигналы» из чатов и личные сообщения с ссылкой на биржу — частый путь к фишингу.",
    "У мелкой монеты широкий спред и проскальзывание: свеча в бэктесте выглядит лучше, чем реальная заявка.",
    "Резкий рост малоизвестной монеты без новостей часто оказывается пампом: цена так же быстро возвращается.",
)

DISCLAIMER_TAX = (
    "Не налоговая консультация. FIFO по реальным сделкам журнала в валюте котировки: "
    "USDT и рубли не пересчитываются, комиссия в монете должна быть записана деньгами котировки."
)


def coin_profile(candles: list[Candle]) -> dict[str, Any]:
    """Возраст ряда и оборот. Молодая или тонкая монета — слабое место бэктеста."""
    warnings: list[str] = []
    if len(candles) < 2:
        return {
            "history_days": 0, "median_quote_volume": None, "bars": len(candles),
            "warnings": ["Дневной истории почти нет: бэктест по этой монете ничего не доказывает."],
        }
    days = (candles[-1].ts - candles[0].ts) / 86_400_000
    volumes = sorted(c.close * c.volume for c in candles if c.close > 0 and c.volume >= 0)
    median = float(volumes[len(volumes) // 2]) if volumes else 0.0
    if days < YOUNG_DAYS:
        warnings.append(
            f"История около {int(days)} дн. (меньше {YOUNG_DAYS}): монета молодая, проверка на ней короткая."
        )
    if median < THIN_QUOTE_VOLUME:
        warnings.append(
            f"Медианный дневной оборот около {median:,.0f} в валюте котировки: ликвидность низкая, "
            "фиксированный спред для такой монеты слишком оптимистичен.".replace(",", " ")
        )
    return {
        "history_days": int(days),
        "median_quote_volume": round(median, 2),
        "bars": len(candles),
        "warnings": warnings,
    }


def quote_warning(currency: str | None) -> str | None:
    if currency and currency != "USDT":
        return (
            f"Счёт и риск крипты считаются только в USDT, а пара котируется в {currency}. "
            "Размер позиции и результат в USDT будут неверными."
        )
    return None


def btc_regime(candles: list[Candle]) -> dict[str, Any]:
    if len(candles) < BTC_WINDOW + 1:
        return {
            "regime": "unknown", "change_20d_pct": None,
            "note": "Мало дневных свечей биткоина: режим рынка не определён.",
        }
    change = candles[-1].close / candles[-1 - BTC_WINDOW].close - 1
    pct = round(change * 100, 2)
    if change >= BTC_MOVE:
        regime, note = "up", f"Биткоин за {BTC_WINDOW} дней {pct:+.1f}%: альткоин часто повторяет этот рост."
    elif change <= -BTC_MOVE:
        regime, note = "down", f"Биткоин за {BTC_WINDOW} дней {pct:+.1f}%: альткоин часто падает вместе с ним."
    else:
        regime, note = "flat", f"Биткоин за {BTC_WINDOW} дней {pct:+.1f}%: выраженного хода нет."
    return {"regime": regime, "change_20d_pct": pct, "note": note}


def _log_returns(closes: dict[int, float]) -> dict[int, float]:
    ts = sorted(closes)
    out = {}
    for prev, cur in pairwise(ts):
        a, b = closes[prev], closes[cur]
        if a > 0 and b > 0:
            out[cur] = math.log(b / a)
    return out


def correlation(a: dict[int, float], b: dict[int, float]) -> tuple[float | None, int]:
    """Корреляция лог-доходностей по общим датам. Мало пересечения — None."""
    ra, rb = _log_returns(a), _log_returns(b)
    common = sorted(set(ra) & set(rb))
    if len(common) < MIN_CORR_BARS:
        return None, len(common)
    x = np.array([ra[t] for t in common])
    y = np.array([rb[t] for t in common])
    if float(x.std()) < 1e-12 or float(y.std()) < 1e-12:
        return None, len(common)
    return round(float(np.corrcoef(x, y)[0, 1]), 2), len(common)


def btc_link_note(symbol: str, corr: float | None, overlap: int) -> str | None:
    if symbol.upper().startswith("BTC/"):
        return None
    if corr is None:
        if overlap:
            return f"С биткоином всего {overlap} общих дневных доходностей: связь не оценивалась."
        return None
    if corr >= 0.7:
        return (
            f"Связь с биткоином {corr:.2f} на {overlap} днях: отдельная монета в портфеле "
            "часто тот же риск, что и BTC."
        )
    return f"Связь с биткоином {corr:.2f} на {overlap} днях (порог предупреждения 0.70)."


def basket_note(correlations: list[float | None], symbols: list[str]) -> str | None:
    """Все открытые альты сильно совпадали с BTC — портфель не диверсифицирован."""
    known = [c for c in correlations if c is not None]
    if len(symbols) < 2 or len(known) < len(symbols):
        return None
    if min(known) >= 0.7:
        return (
            "Открытые криптопозиции сильно совпадали с биткоином: по сути это одна ставка на BTC, "
            "а не несколько разных."
        )
    return None


def spread_from_book(book: dict[str, Any]) -> dict[str, float] | None:
    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None
    try:
        bid, ask = float(bids[0][0]), float(asks[0][0])
    except (TypeError, ValueError, IndexError):
        return None
    if bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    return {"bid": bid, "ask": ask, "spread_pct": round((ask - bid) / mid * 100, 4)}


def slippage_from_spread(spread_pct: float) -> float:
    """Половина спреда — переход через стакан. Ниже 0.01% не опускаемся: это шум котировки."""
    return round(max(spread_pct / 2, 0.01), 4)


def dca_plan(budget: float, parts: int, fee_pct: float, prices: list[float] | None = None) -> dict[str, Any]:
    """Равные суммы по расписанию. Это калькулятор, не бэктест: движок умеет только «в рынке / вне рынка»."""
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("Сумма плана должна быть больше нуля")
    if isinstance(parts, bool) or not isinstance(parts, int) or not 2 <= parts <= 365:
        raise ValueError("Число покупок — от 2 до 365")
    if not math.isfinite(fee_pct) or not 0 <= fee_pct < 100:
        raise ValueError("Комиссия — процент от 0 до 100")
    cash = budget / parts
    out: dict[str, Any] = {
        "parts": parts,
        "cash_each": round(cash, 8),
        "fee_pct": fee_pct,
        "budget": budget,
        "note": (
            "Каждая покупка — одна и та же сумма, не одно и то же количество монет. "
            f"Комиссия {fee_pct:g}% — одна ставка, без скидки за объём и без разницы maker/taker. "
            "План ничего не покупает."
        ),
    }
    if not prices:
        return out
    if len(prices) != parts:
        raise ValueError("Число цен должно совпадать с числом покупок")
    qty = 0.0
    for price in prices:
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Цена покупки должна быть больше нуля")
        qty += cash * (1 - fee_pct / 100) / price
    last = prices[-1]
    value = qty * last
    out.update(
        qty=round(qty, 8),
        avg_price=round(budget / qty, 8) if qty else None,
        value=round(value, 8),
        pnl=round(value - budget, 8),
        prices=prices,
    )
    return out


def fee_in_quote(fee_coin: float, price: float) -> float:
    """Комиссия, списанная в купленной монете, в деньгах котировки."""
    if fee_coin < 0 or price <= 0:
        raise ValueError("Комиссия в монете не отрицательна, цена больше нуля")
    return round(fee_coin * price, 8)


def rub_per_usdt(usd_rub: float) -> dict[str, Any]:
    return {
        "usd_rub": usd_rub,
        "assumption": "USDT принят равным доллару США. Это курс ЦБ, не стакан биржи: USDT может стоить иначе.",
    }


def parse_cbr_usd(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        usd = payload["Valute"]["USD"]
        value = float(usd["Value"]) / float(usd.get("Nominal") or 1)
        as_of = str(payload.get("Date") or "")
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("Ответ ЦБ не содержит курс доллара") from e
    if value <= 0:
        raise ValueError("Курс доллара ЦБ неположителен")
    return {"usd_rub": round(value, 4), "as_of": as_of, "source": "cbr-xml-daily"}


def fifo_report(entries: list[Entry], year: int | None = None, market: str | None = "crypto") -> dict[str, Any]:
    """Закрытые лоты FIFO. Очередь покупок строится по всей истории, в отчёт попадают продажи выбранного года."""
    if year is not None and not 2009 <= year <= 2100:
        raise ValueError("Год отчёта не похож на календарный")
    rows: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str], list[Entry]] = {}
    for e in entries:
        if e.deleted_at is not None or e.mode != "real":
            continue
        if market and e.market != market:
            continue
        grouped.setdefault((e.market, e.symbol), []).append(e)
    for (mk, symbol), items in sorted(grouped.items()):
        lots: list[list[float]] = []
        for e in sorted(items, key=lambda x: (x.ts, x.side != "buy", x.id or 0)):
            if e.side == "buy":
                lots.append([e.qty, (e.qty * e.price + e.fee) / e.qty])
                continue
            left, cost = e.qty, 0.0
            while left > 1e-12 and lots:
                take = min(left, lots[0][0])
                cost += take * lots[0][1]
                lots[0][0] -= take
                left -= take
                if lots[0][0] <= 1e-12:
                    lots.pop(0)
            if left > 1e-8:
                continue  # продажа больше позиции журнал обычно не пускает; такой хвост в FIFO не закрываем
            sold_year = datetime.fromtimestamp(e.ts / 1000, UTC).year
            if year is not None and sold_year != year:
                continue
            proceeds = e.qty * e.price - e.fee
            rows.append({
                "market": mk, "symbol": symbol, "year": sold_year, "sell_ts": e.ts,
                "qty": round(e.qty, 8), "proceeds": round(proceeds, 8), "cost": round(cost, 8),
                "pnl": round(proceeds - cost, 8),
            })
    pnl = round(sum(r["pnl"] for r in rows), 8)
    return {"year": year, "market": market, "rows": rows, "pnl": pnl, "disclaimer": DISCLAIMER_TAX}
