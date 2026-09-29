from __future__ import annotations

from compass.data_quality import check_candles
from compass.models import Candle

H, D = 3_600_000, 86_400_000


def series(step: int, n: int, skip: set[int] = frozenset(), price: float = 100.0) -> list[Candle]:
    return [Candle(i * step, price, price + 1, price - 1, price, 10.0) for i in range(n) if i not in skip]


def codes(r: dict) -> set[str]:
    return {i["code"] for i in r["issues"]}


def test_clean_series_is_ok() -> None:
    r = check_candles(series(H, 50), "crypto", "1h")
    assert r["status"] == "ok" and r["issues"] == []


def test_crypto_gap_counts_missing_bars() -> None:
    r = check_candles(series(H, 50, skip={10, 11, 12}), "crypto", "1h")
    (gap,) = r["issues"]
    assert gap["code"] == "gaps" and gap["count"] == 1 and "3 свечей" in gap["message"] and r["status"] == "warning"


def test_moex_weekend_is_not_a_gap_but_month_is() -> None:
    assert check_candles(series(D, 40, skip={5, 6}), "moex", "1d")["status"] == "ok"
    assert "gaps" in codes(check_candles(series(D, 40, skip=set(range(10, 25))), "moex", "1d"))


def test_moex_intraday_overnight_break_is_normal() -> None:
    assert check_candles(series(H, 30, skip=set(range(10, 20))), "moex", "1h")["status"] == "ok"


def test_duplicates_and_disorder_are_errors() -> None:
    c = series(D, 5)
    r = check_candles([*c, c[-1]], "crypto", "1d")
    assert r["status"] == "error" and "duplicates" in codes(r)
    assert "unsorted" in codes(check_candles([c[2], c[1], c[0]], "crypto", "1d"))


def test_invalid_ohlc_is_error() -> None:
    bad = [*series(D, 5), Candle(5 * D, 100, 90, 95, 100, 1)]  # high < low
    r = check_candles(bad, "crypto", "1d")
    assert r["status"] == "error" and "invalid_ohlc" in codes(r)
    assert "invalid_ohlc" in codes(check_candles([Candle(0, 0, 1, 0, 1, 1)], "crypto", "1d"))


def test_jump_warns_and_mentions_splits_for_stocks() -> None:
    c = series(D, 10)
    c.append(Candle(10 * D, 40, 41, 39, 40, 5))  # -60%
    msg = next(i["message"] for i in check_candles(c, "moex", "1d")["issues"] if i["code"] == "jumps")
    assert "сплит" in msg and "обвал" in msg
    assert "сплит" not in next(i["message"] for i in check_candles(c, "crypto", "1d")["issues"] if i["code"] == "jumps")


def test_zero_volume_share() -> None:
    c = [Candle(i * D, 10, 11, 9, 10, 0.0 if i % 2 else 5.0) for i in range(20)]
    assert "zero_volume" in codes(check_candles(c, "moex", "1d"))
