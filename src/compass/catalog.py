"""Справочники инструментов, собранные scripts/build_catalog.py.

Поиск идёт по ним, а не по урезанному ответу биржи на 20 строк. Свечи по-прежнему
запрашиваются только для тикера, который пользователь открыл.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from compass.models import Instrument


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "data"
    return Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=4)
def load_catalog(name: str) -> tuple[dict, ...]:
    path = _data_dir() / name
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items") or []
    return tuple(items)


def moex_catalog() -> tuple[dict, ...]:
    return load_catalog("moex_catalog.json")


def okx_catalog() -> tuple[dict, ...]:
    return load_catalog("okx_catalog.json")


def match_instruments(rows, query: str, market_id: str, limit: int = 40) -> list[Instrument]:
    """Совпадение по тикеру важнее совпадения по короткому имени."""
    q = query.strip().casefold()
    if not q:
        return []
    scored: list[tuple[int, str, Instrument]] = []
    for row in rows:
        symbol = str(row["symbol"])
        name = str(row.get("name") or symbol)
        sym, nm = symbol.casefold(), name.casefold()
        if q == sym:
            rank = 0
        elif sym.startswith(q):
            rank = 1
        elif q in sym:
            rank = 2
        elif q in nm:
            rank = 3
        else:
            continue
        scored.append((rank, symbol, Instrument(symbol, name, market_id, str(row.get("kind") or ""))))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:limit]]
