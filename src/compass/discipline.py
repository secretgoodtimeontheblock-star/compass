"""Факты для слоя дисциплины и объяснений. Всё считает код; AI только пересказывает посчитанное.

Вопросы, на которые отвечает этот слой (из заметок о продукте):
- Что изменилось?                                     — `what_changed`
- Какое допущение делает этот бэктест слабым?          — `backtest_weaknesses`
- Где я нарушил свой план?                             — `discipline_facts`
- Чем эта сделка отличается от моих прежних?           — `trade_comparison`
- Какое понятие стоит понять перед решением?           — `compass.glossary` (привязка по кодам)

Здесь нет рекомендаций «что купить»: только сопоставление решений пользователя с его же правилами и историей."""

from __future__ import annotations

import statistics
from typing import Any

from compass import glossary
from compass.experiments import MULTIPLE_TESTING_WARN_AT

DAY_S = 86_400
YEAR_MS = 365 * 86_400_000
MIN_COMPARE = 3  # меньше прежних планов — сравнивать не с чем
SIZE_FLAG_RATIO = 1.5  # заметно крупнее/мельче обычного
SEVERITY_ORDER = {"bad": 0, "warn": 1, "info": 2}


def _w(code: str, severity: str, text: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "text": text, "learn": glossary.concepts_for(code)}


def backtest_weaknesses(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Допущения и ограничения конкретного бэктеста, из-за которых его результату нельзя верить безоговорочно.
    result — ответ /api/backtest. Порядок: самые серьёзные первыми."""
    m = result.get("metrics", {})
    ex = result.get("execution", {})
    model = ex.get("model", {})
    cov = result.get("coverage", {})
    out: list[dict[str, Any]] = []
    integrity = result.get("integrity")
    if integrity is not None and not integrity.get("passed", True):
        out.append(_w("integrity_failed", "bad", "Не пройдена проверка целостности: " + integrity.get("summary", "")))
    trades = int(m.get("trades") or 0)
    if trades < 10:
        out.append(_w("few_trades", "bad", f"Всего {trades} закрытых сделок: любые показатели по такой выборке случайны."))
    elif trades < 30:
        out.append(_w("few_trades", "warn", f"Закрытых сделок {trades}: для уверенных выводов о правиле мало."))
    span = (cov.get("last_ts") or 0) - (cov.get("first_ts") or 0)
    if cov and span < YEAR_MS:
        out.append(_w("short_history", "warn", f"История покрывает {span / 86_400_000:.0f} дн. — меньше года: в неё не попали "
                      "разные режимы рынка, результат может отражать только один из них."))
    if model and not model.get("spread_pct"):
        out.append(_w("no_spread", "warn", "Спред принят равным нулю: каждая сделка в жизни теряет на разнице цен покупки и продажи."))
    part = ex.get("max_participation_pct") or m.get("max_participation_pct")
    if part and part > 20:
        out.append(_w("high_participation", "bad", f"Заявки занимали бы до {part:g}% объёма свечи: по таким размерам цена исполнения была бы заметно хуже."))
    elif part and part > 5:
        out.append(_w("high_participation", "warn", f"Заявки занимали бы до {part:g}% объёма свечи: ликвидность может ухудшить исполнение."))
    if m.get("ambiguous_bars"):
        out.append(_w("ambiguous", "info", f"В {m['ambiguous_bars']} свечах стоп и цель достигнуты вместе: порядок событий неизвестен, принят стоп."))
    if m.get("gap_exits"):
        out.append(_w("gaps", "info", f"{m['gap_exits']} выходов по гэпу: исполнение было хуже уровня стопа."))
    rules = (result.get("run_card") or {}).get("rules")
    if not rules or not rules.get("stop_atr_mult"):
        out.append(_w("no_stops", "info", "Стопов нет: потери в бэктесте ничем не ограничены, а размер сделки — весь капитал."))
    trials = result.get("trials") or {}
    if (trials.get("total_variants") or 0) >= MULTIPLE_TESTING_WARN_AT:
        out.append(_w("prior_variants", "warn", f"Это правило на этом инструменте уже пробовали в {trials['total_variants']} вариантах: "
                      "лучший из них мог оказаться хорошим случайно."))
    if result.get("stale"):
        out.append(_w("stale_data", "warn", "Источник данных недоступен: расчёт по сохранённым свечам, актуальность не подтверждена."))
    intraday = result.get("intraday") or {}
    if intraday.get("feed_delay_seconds"):
        out.append(_w("delayed_feed", "warn", f"Котировки идут с задержкой ~{intraday['feed_delay_seconds'] // 60} мин: "
                      "на таком таймфрейме реальный сигнал придёт позже, чем в истории."))
    bh = m.get("buy_hold_return_pct")
    if bh is not None and m.get("total_return_pct") is not None and m["total_return_pct"] < bh:
        out.append(_w("below_buy_hold", "info", f"Правило ({m['total_return_pct']:g}%) не обогнало простое удержание ({bh:g}%): "
                      "сложность оправдана, только если риск или просадка заметно ниже."))
    if m.get("open_trade"):
        out.append(_w("open_trade", "info", "Последняя сделка не закрыта и оценена по последней цене закрытия."))
    out.append(_w("in_sample_only", "info", "Это бэктест на всей истории сразу: параметры могли быть подобраны на тех же данных. "
                  "Проверка вне выборки и лаборатория показывают, повторится ли результат на новых."))
    out.sort(key=lambda w: SEVERITY_ORDER[w["severity"]])
    return out


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _plan_metrics(p: Any) -> dict[str, float | None]:
    return {
        "risk_pct": p.risk_amount / p.capital * 100 if p.capital else None,
        "stop_distance_pct": (p.entry - p.stop) / p.entry * 100 if p.entry else None,
        "size_pct": p.cost / p.capital * 100 if p.capital else None,
        "reward_risk": p.reward_risk,
    }


_LABELS = {
    "risk_pct": "Риск на сделку, % капитала", "stop_distance_pct": "Расстояние до стопа, %",
    "size_pct": "Размер позиции, % капитала", "reward_risk": "Цель к риску (R)",
}


def trade_comparison(plan: Any, previous: list[tuple[Any, dict[str, Any]]]) -> dict[str, Any]:
    """Сравнивает план с прежними планами пользователя: размер, риск, стоп, цель, исходы закрытых.
    previous — [(план, разбор плана)] без самого сравниваемого. Никаких оценок «хорошо/плохо»: только отличия."""
    this = _plan_metrics(plan)
    prev = [p for p, _ in previous if p.id != plan.id]
    out: dict[str, Any] = {
        "plan_id": plan.id, "symbol": plan.symbol, "this": this, "previous_plans": len(prev), "differences": [],
        "outcomes": None, "same_strategy": None, "enough": len(prev) >= MIN_COMPARE,
    }
    if len(prev) < MIN_COMPARE:
        out["note"] = f"Прежних планов {len(prev)}: для сравнения нужно хотя бы {MIN_COMPARE}."
        return out
    metrics = [_plan_metrics(p) for p in prev]
    for key, label in _LABELS.items():
        med = _median([m[key] for m in metrics if m[key] is not None])
        cur = this[key]
        if med is None or cur is None or med == 0:
            continue
        ratio = cur / med
        note = None
        if ratio >= SIZE_FLAG_RATIO:
            note = "заметно больше обычного"
        elif ratio <= 1 / SIZE_FLAG_RATIO:
            note = "заметно меньше обычного"
        out["differences"].append({"metric": key, "label": label, "this": round(cur, 3), "median": round(med, 3),
                                   "ratio": round(ratio, 2), "note": note})
    closed = [(p, r) for p, r in previous if p.id != plan.id and r.get("status") == "closed"]
    results = [r["result_pct"] for _, r in closed if r.get("result_pct") is not None]
    rs = [r["r_multiple"] for _, r in closed if r.get("r_multiple") is not None]
    if results:
        out["outcomes"] = {
            "closed": len(results), "wins": sum(1 for x in results if x > 0), "median_result_pct": round(_median(results), 2),
            "median_r": round(_median(rs), 2) if rs else None,
        }
    same = [(p, r) for p, r in closed if plan.strategy and p.strategy == plan.strategy and r.get("result_pct") is not None]
    if same:
        res = [r["result_pct"] for _, r in same]
        out["same_strategy"] = {"strategy": plan.strategy, "closed": len(res), "wins": sum(1 for x in res if x > 0),
                                "median_result_pct": round(_median(res), 2)}
    out["note"] = ("Сравнение с вашими же прежними планами: это отличия, а не оценка качества. "
                   "Малое число закрытых планов не позволяет судить о результативности." if len(closed) < 10
                   else "Сравнение с вашими же прежними планами: это отличия, а не оценка качества.")
    return out


def discipline_facts(cockpit: dict[str, Any], weeklies: list[dict[str, Any]]) -> dict[str, Any]:
    """Где пользователь отступил от собственных правил: текущие нарушения (кокпит) и недельная картина по журналу."""
    rules = next((q for q in cockpit["questions"] if q["id"] == "rules"), {"items": []})
    week = []
    for w in weeklies:
        dev = w["plan_deviations"]
        week.append({
            "account": w["name"], "currency": w["currency"], "days": w["days"], "closed_trades": w["closed_trades"],
            "realized": w["realized"], "entries": w["entries"], "entries_without_stop": w["entries_without_stop"],
            "entries_without_plan": w["entries_without_plan"], "plans_reviewed": dev["plans_reviewed"],
            "worse_entry": dev["worse_entry"], "bigger_qty": dev["bigger_qty"], "risk_exceeded": dev["risk_exceeded"],
            "plan_no_stop": dev["no_stop"], "days_over_daily_limit": w["days_over_daily_limit"],
            "days_over_trades_limit": w["days_over_trades_limit"], "fees": w["fees"],
        })
    violations = [i["text"] for i in rules.get("items", [])]
    for w in week:
        if w["entries_without_stop"]:
            violations.append(f"{w['account']}: входов без записанного стопа за {w['days']} дн. — {w['entries_without_stop']} из {w['entries']}.")
        if w["risk_exceeded"]:
            violations.append(f"{w['account']}: фактический риск выше бюджета плана в {w['risk_exceeded']} из {w['plans_reviewed']} планов.")
        if w["bigger_qty"]:
            violations.append(f"{w['account']}: количество больше планового в {w['bigger_qty']} из {w['plans_reviewed']} планов.")
        if w["worse_entry"]:
            violations.append(f"{w['account']}: вход хуже плановой цены в {w['worse_entry']} из {w['plans_reviewed']} планов.")
        if w["days_over_daily_limit"]:
            violations.append(f"{w['account']}: дней с превышением дневного лимита убытка — {len(w['days_over_daily_limit'])}.")
        if w["days_over_trades_limit"]:
            violations.append(f"{w['account']}: дней с превышением лимита входов — {len(w['days_over_trades_limit'])}.")
        if w["entries_without_plan"]:
            violations.append(f"{w['account']}: входов без плана — {w['entries_without_plan']} из {w['entries']}.")
    return {"violations": violations, "weeks": week, "status": cockpit["status"]}


def what_changed(
    now_ms: int, hours: int, accounts: list[dict[str, Any]], entries: list[dict[str, Any]], signals: list[dict[str, Any]],
    plans: list[dict[str, Any]], position_moves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Что изменилось за окно: записи журнала, новые сигналы и планы, движение цен по открытым позициям."""
    cutoff = now_ms - hours * 3_600_000
    new_entries = [e for e in entries if e["ts"] >= cutoff]
    new_signals = [s for s in signals if (s.get("created_at") or 0) * 1000 >= cutoff]
    new_plans = [p for p in plans if (p.get("created_at") or 0) * 1000 >= cutoff]
    out = {
        "hours": hours, "entries": new_entries, "signals": new_signals, "plans": new_plans, "moves": position_moves,
        "accounts": [{"name": a["name"], "currency": a["currency"], "equity": a["equity"], "unrealized_total": a["unrealized_total"],
                      "daily_pnl": a["daily_pnl"], "heat_pct": a["heat_pct"], "open_positions": len(a["positions"])} for a in accounts],
    }
    out["empty"] = not (new_entries or new_signals or new_plans or position_moves)
    return out


def facts_text_changed(c: dict[str, Any]) -> str:
    lines = [f"Окно: последние {c['hours']} ч."]
    for a in c["accounts"]:
        lines.append(
            f"Счёт {a['name']} ({a['currency']}): капитал {a['equity']}, нереализованный результат {a['unrealized_total']}, "
            f"зафиксировано сегодня {a['daily_pnl']}, суммарный риск {a['heat_pct']}% капитала, открытых позиций {a['open_positions']}."
        )
    for e in c["entries"]:
        lines.append(f"Запись журнала: {e['symbol']} {'покупка' if e['side'] == 'buy' else 'продажа'} {e['qty']} × {e['price']}.")
    for s in c["signals"]:
        lines.append(f"Новый сигнал: {s['symbol']} {s['strategy']} ({'вход' if s['side'] == 'buy' else 'выход'}), статус {s.get('status', '')}.")
    for p in c["plans"]:
        lines.append(f"Новый план: {p['symbol']} вход {p['entry']}, стоп {p['stop']}, риск {p['risk_amount']} {p.get('currency') or ''}.")
    for m in c["moves"]:
        lines.append(f"Позиция {m['symbol']}: последняя свеча {m['change_pct']:+.2f}%, цена {m['mark_price']}.")
    if c["empty"]:
        lines.append("За окно не было новых записей, сигналов, планов и данных о движении позиций.")
    return "\n".join(lines)


def facts_text_weaknesses(result: dict[str, Any], weaknesses: list[dict[str, Any]]) -> str:
    m = result.get("metrics", {})
    ex = (result.get("execution") or {}).get("model", {})
    cov = result.get("coverage", {})
    lines = [
        f"стратегия: {result.get('strategy')}, параметры: {result.get('params')}",
        f"свечей в истории: {cov.get('candles')}",
        f"доходность правила, %: {m.get('total_return_pct')}; «купил и держи», %: {m.get('buy_hold_return_pct')}",
        f"максимальная просадка, %: {m.get('max_drawdown_pct')}; закрытых сделок: {m.get('trades')}",
        f"расходы: комиссия {ex.get('fee_pct')}%, проскальзывание {ex.get('slippage_pct')}%, спред {ex.get('spread_pct')}%",
        "Найденные кодом слабые допущения (по убыванию серьёзности):",
    ]
    lines += [f"- [{w['severity']}] {w['text']}" for w in weaknesses]
    return "\n".join(lines)


def facts_text_discipline(d: dict[str, Any]) -> str:
    lines = [f"Общий статус дня: {d['status']}"]
    if d["violations"]:
        lines.append("Отступления от ваших правил:")
        lines += [f"- {v}" for v in d["violations"]]
    else:
        lines.append("Отступлений от правил по данным нет.")
    for w in d["weeks"]:
        lines.append(
            f"Неделя ({w['days']} дн.), {w['account']}: закрытых сделок {w['closed_trades']}, зафиксировано {w['realized']} {w['currency']}, "
            f"входов {w['entries']}, комиссии {w['fees']}."
        )
    return "\n".join(lines)


def facts_text_comparison(c: dict[str, Any]) -> str:
    t = c["this"]
    lines = [
        f"План: {c['symbol']}; риск {t['risk_pct']:.2f}% капитала, стоп на расстоянии {t['stop_distance_pct']:.2f}%, "
        f"размер {t['size_pct']:.1f}% капитала, цель к риску {t['reward_risk']}.",
        f"Прежних планов: {c['previous_plans']}.",
    ]
    for d in c["differences"]:
        flag = f" — {d['note']}" if d["note"] else ""
        lines.append(f"{d['label']}: сейчас {d['this']}, обычно (медиана) {d['median']}{flag}.")
    if c["outcomes"]:
        o = c["outcomes"]
        lines.append(f"Закрытые прежние планы: {o['closed']}, прибыльных {o['wins']}, медианный результат {o['median_result_pct']}%.")
    if c["same_strategy"]:
        s = c["same_strategy"]
        lines.append(f"По той же стратегии закрыто {s['closed']}, прибыльных {s['wins']}, медианный результат {s['median_result_pct']}%.")
    if not c["enough"]:
        lines.append(c["note"])
    return "\n".join(lines)
