"""Готовые наборы инструментов по темам: чтобы новичок не искал тикеры по одному.

Наборы составлены вручную из ликвидных бумаг и крупных монет. Это удобство, а не рекомендация: состав —
подборка для знакомства, а не список «что покупать». Каждый тикер сверяется со справочником биржи
(`catalog.py`), поэтому в ответ попадает только то, что действительно торгуется; названия берутся оттуда же.
Иконки групп — идентификаторы, интерфейс сам выбирает картинку."""

from __future__ import annotations

from dataclasses import dataclass

from compass.catalog import moex_catalog, okx_catalog
from compass.models import Instrument


@dataclass(frozen=True, slots=True)
class Group:
    id: str
    title: str
    icon: str
    note: str
    symbols: tuple[str, ...]


def _s(text: str) -> tuple[str, ...]:
    return tuple(text.split())


MOEX_GROUPS = (
    Group("blue", "Голубые фишки", "star", "Самые крупные и ликвидные акции рынка.", _s("SBER GAZP LKOH YDEX ROSN GMKN NVTK TATN MGNT MTSS T MOEX")),
    Group("banks", "Банки и финансы", "bank", "Банки, биржа, финансовые холдинги.", _s("SBER SBERP VTBR T MOEX CBOM BSPB SVCB RENI AFKS")),
    Group("oil", "Нефть и газ", "fuel", "Добыча и переработка; зависят от цен на сырьё.", _s("GAZP LKOH ROSN NVTK TATN TATNP SNGS SNGSP SIBN")),
    Group("metals", "Металлы и добыча", "pickaxe", "Сталь, золото, алмазы, алюминий.", _s("GMKN PLZL ALRS CHMF NLMK MAGN RUAL SELG")),
    Group("retail", "Ретейл и потребление", "cart", "Магазины и товары повседневного спроса.", _s("MGNT X5 LENT OZON BELU")),
    Group("it", "IT и технологии", "cpu", "Интернет-компании и разработчики ПО.", _s("YDEX OZON VKCO HEAD POSI ASTR WUSH T")),
    Group("energy", "Электроэнергетика", "bolt", "Генерация и сети.", _s("IRAO HYDR FEES UPRO ENPG MSNG")),
    Group("telecom", "Телеком", "radio", "Связь и интернет-провайдеры.", _s("MTSS RTKM TTLK")),
    Group("chem", "Химия и удобрения", "flask", "Удобрения и нефтехимия.", _s("PHOR AKRN NKNC KZOS")),
    Group("transport", "Транспорт", "plane", "Авиа, морские перевозки.", _s("AFLT FLOT FESH")),
    Group("builders", "Строительство", "building", "Застройщики; чувствительны к ставке.", _s("PIKK SMLT LSRG")),
    Group("etf", "Фонды (БПИФ)", "layers", "Готовые корзины бумаг одной покупкой.", _s("TMOS SBMX EQMX LQDT SBGB TBRU AKMB DIVD")),
)

CRYPTO_GROUPS = (
    Group("major", "Основные монеты", "star", "Крупнейшие по капитализации.", _s("BTC ETH BNB SOL XRP")),
    Group("alts", "Крупные альткоины", "coins", "Следующий эшелон: популярные и ликвидные.", _s("ADA DOGE TRX AVAX LINK DOT LTC BCH XLM ETC ATOM NEAR")),
    Group("defi", "DeFi", "blocks", "Децентрализованные финансы.", _s("UNI AAVE CRV INJ ONDO")),
    Group("l2", "Новые сети и L2", "network", "Масштабирование и молодые блокчейны; рискованнее.", _s("ARB OP APT SUI SEI TIA")),
    Group("ai", "ИИ и данные", "brain", "Проекты вокруг ИИ и хранения данных.", _s("FET RENDER WLD TAO ICP FIL")),
    Group("meme", "Мемкоины", "smile", "Очень волатильны: цена держится на настроениях.", _s("DOGE SHIB PEPE WIF BONK NOT")),
)

QUOTE = "USDT"


def groups(market: str) -> list[dict]:
    """Наборы рынка с тикерами, найденными в справочнике. Пустые наборы не возвращаются."""
    if market == "moex":
        cat = {str(r["symbol"]): r for r in moex_catalog()}
        defs = MOEX_GROUPS

        def make(s: str) -> Instrument | None:
            row = cat.get(s)
            return Instrument(s, str(row.get("name") or s), "moex", str(row.get("kind") or "")) if row else None

    elif market == "crypto":
        cat = {str(r["symbol"]): r for r in okx_catalog()}
        defs = CRYPTO_GROUPS

        def make(s: str) -> Instrument | None:
            pair = f"{s}/{QUOTE}"
            return Instrument(pair, pair, "crypto") if pair in cat else None

    else:
        return []
    out = []
    for g in defs:
        items = [i for i in (make(s) for s in g.symbols) if i is not None]
        if items:
            out.append({
                "id": g.id, "title": g.title, "icon": g.icon, "note": g.note,
                "instruments": [{"market": i.market, "symbol": i.symbol, "name": i.name, "kind": i.kind} for i in items],
            })
    return out


def find(market: str, group_id: str) -> dict | None:
    return next((g for g in groups(market) if g["id"] == group_id), None)
