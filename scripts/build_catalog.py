"""Скачивает списки инструментов и кладёт их в пакет.

МосБиржа: акции и фонды TQBR (так их показывает Альфа-Инвестиции и другие
брокеры), ОФЗ, корпоративные облигации и валютные пары SELT.
OKX: все активные спотовые пары. Свечи по ним не качаются — только справочник,
чтобы поиск находил тикер без ограничения в 20 строк.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import ccxt
import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "compass" / "data"
ISS = "https://iss.moex.com/iss"

BOARDS = (
    ("stock", "shares", "TQBR", "share"),
    ("stock", "bonds", "TQOB", "bond"),
    ("stock", "bonds", "TQCB", "bond"),
    ("currency", "selt", "CETS", "fx"),
)


def moex_board(client: httpx.Client, engine: str, market: str, board: str, kind: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    start = 0
    while True:
        url = f"{ISS}/engines/{engine}/markets/{market}/boards/{board}/securities.json"
        data = client.get(
            url,
            params={
                "iss.meta": "off",
                "securities.columns": "SECID,SHORTNAME,LOTSIZE,STATUS",
                "start": start,
            },
        ).json()["securities"]
        cols = {name: i for i, name in enumerate(data["columns"])}
        rows = data["data"] or []
        if not rows:
            break
        first = rows[0][cols["SECID"]]
        if first in seen:
            break
        for row in rows:
            if row[cols["STATUS"]] != "A":
                continue
            symbol = str(row[cols["SECID"]])
            seen.add(symbol)
            lot = row[cols["LOTSIZE"]]
            items.append(
                {
                    "symbol": symbol,
                    "name": str(row[cols["SHORTNAME"]] or symbol),
                    "lot": int(lot) if lot else 1,
                    "engine": engine,
                    "market": market,
                    "board": board,
                    "kind": kind,
                }
            )
        start += len(rows)
        if len(rows) < 100:
            break
        print(f"  {board}: {len(items)}", flush=True)
    return items


def okx_spot() -> list[dict]:
    ex = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    markets = ex.load_markets()
    items = []
    for m in markets.values():
        if m.get("spot") and m.get("active", True) and m.get("symbol"):
            items.append({"symbol": m["symbol"], "name": m["symbol"]})
    items.sort(key=lambda x: x["symbol"])
    return items


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=40)
    moex: list[dict] = []
    taken: set[str] = set()
    for engine, market, board, kind in BOARDS:
        print(f"MOEX {board}", flush=True)
        for row in moex_board(client, engine, market, board, kind):
            if row["symbol"] in taken:
                continue
            taken.add(row["symbol"])
            moex.append(row)
        print(f"  итого {board}: {sum(1 for x in moex if x['board'] == board)}", flush=True)
    moex.sort(key=lambda x: x["symbol"])
    (OUT / "moex_catalog.json").write_text(
        json.dumps({"updated": date.today().isoformat(), "items": moex}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"OKX spot", flush=True)
    crypto = okx_spot()
    (OUT / "okx_catalog.json").write_text(
        json.dumps({"updated": date.today().isoformat(), "items": crypto}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"moex {len(moex)}, okx {len(crypto)}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
