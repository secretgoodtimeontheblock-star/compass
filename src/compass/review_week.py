"""Недельный разбор по рассчитанным показателям журнала (реальные сделки одного счёта).

Только факты: результат после комиссий, число сделок, отклонения от собственных планов, входы без стопа,
дни с превышенным дневным лимитом. Причин и выводов здесь нет: их можно обсуждать с AI поверх этих чисел,
но числа считает код. Каждый счёт отдельно, без сложения рублей и USDT."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from compass.accounts import MARKET_TZ, Account
from compass.journal import Entry
from compass.plans import Plan, review
from compass.portfolio import realized_events

DAY_MS = 86_400_000
MIN_TRADES_FOR_STATS = 10


def _local_date(ts_ms: int, market: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, MARKET_TZ[market]).date().isoformat()


def weekly(
    account: Account, entries: list[Entry], plan_by_uid: Callable[[str], Plan | None], now_ms: int, days: int = 7
) -> dict[str, Any]:
    start = now_ms - days * DAY_MS
    events = [(ts, sym, p) for ts, sym, p in realized_events(entries) if ts >= start]
    buys = [e for e in entries if e.side == "buy" and e.ts >= start]
    pnls = [p for _, _, p in events]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    per_day: dict[str, dict[str, float]] = {}
    for ts, _, p in events:
        d = per_day.setdefault(_local_date(ts, account.market), {"entries": 0, "pnl": 0.0})
        d["pnl"] += p
    for e in buys:
        per_day.setdefault(_local_date(e.ts, account.market), {"entries": 0, "pnl": 0.0})["entries"] += 1
    limit = None if not account.capital else account.capital * account.daily_loss_limit_pct / 100
    days_over_limit = sorted(d for d, v in per_day.items() if limit is not None and v["pnl"] <= -limit)
    busiest = max(per_day.items(), key=lambda kv: kv[1]["entries"], default=(None, {"entries": 0}))
    fees = sum(e.fee for e in entries if e.ts >= start)

    planned = [e for e in buys if e.plan_uid]
    plans_seen: dict[str, dict[str, Any]] = {}
    for e in planned:
        if e.plan_uid in plans_seen:
            continue
        plan = plan_by_uid(e.plan_uid)
        if plan is not None:
            same = [x for x in entries if x.symbol == plan.symbol]
            rv = review(plan, same)
            devs = {d["code"]: d for d in rv["deviations"]}
            plans_seen[e.plan_uid] = {
                "worse_entry": bool(devs.get("entry", {}).get("worse")),
                "bigger_qty": bool(devs.get("qty", {}).get("worse")),
                "risk_exceeded": bool(rv.get("risk_exceeded")),
                "no_stop": devs.get("stop", {}).get("actual") is None,
            }
    n_trades = len(pnls)
    return {
        "market": account.market, "name": account.name, "currency": account.currency, "days": days,
        "from_ts": start, "to_ts": now_ms,
        "closed_trades": n_trades, "entries": len(buys), "fees": round(fees, 2),
        "realized": round(sum(pnls), 2),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(len(wins) / n_trades * 100, 1) if n_trades >= MIN_TRADES_FOR_STATS else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "best": None if not events else {"symbol": max(events, key=lambda x: x[2])[1], "pnl": round(max(pnls), 2)},
        "worst": None if not events else {"symbol": min(events, key=lambda x: x[2])[1], "pnl": round(min(pnls), 2)},
        "entries_without_stop": sum(1 for e in buys if e.planned_stop is None),
        "entries_with_plan": len(planned), "entries_without_plan": len(buys) - len(planned),
        "plan_deviations": {
            "plans_reviewed": len(plans_seen),
            "worse_entry": sum(v["worse_entry"] for v in plans_seen.values()),
            "bigger_qty": sum(v["bigger_qty"] for v in plans_seen.values()),
            "risk_exceeded": sum(v["risk_exceeded"] for v in plans_seen.values()),
            "no_stop": sum(v["no_stop"] for v in plans_seen.values()),
        },
        "days_over_daily_limit": days_over_limit,
        "busiest_day": None if busiest[0] is None else {"date": busiest[0], "entries": int(busiest[1]["entries"])},
        "max_trades_per_day": account.max_trades_per_day,
        "days_over_trades_limit": sorted(
            d for d, v in per_day.items()
            if account.max_trades_per_day is not None and v["entries"] > account.max_trades_per_day
        ),
        "notes": [
            "Результат — зафиксированный по журналу после комиссий, без нереализованной прибыли и убытка.",
            (
                f"Закрытых сделок {n_trades}: меньше {MIN_TRADES_FOR_STATS} — доля прибыльных не показывается, "
                "по такой выборке об умении судить нельзя."
            ) if n_trades < MIN_TRADES_FOR_STATS else "Даже при достаточном числе сделок неделя — короткий срок.",
        ],
    }
