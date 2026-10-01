"""Decision Cockpit: один согласованный ответ на семь вопросов трейдера.

Кокпит ничего не считает сам: он собирает готовые факты из Portfolio Truth (портфель), планов сделок,
сигналов и состояния наблюдения и расставляет приоритеты. Вопросы:

1. Что происходит сейчас?            4. Какие планы активны?
2. Какие у меня позиции?             5. Какие сигналы требуют внимания?
3. Сколько я реально рискую?         6. Нарушаю ли я собственные правила?
                                      7. Актуальны ли данные?

Общий статус: «stop» — достигнут ваш собственный дневной лимит убытка (план говорит остановиться),
«attention» — есть что проверить, «ok» — нарушений правил нет и данные свежие. Compass ничего не блокирует:
это подсказка о ваших же правилах, решение остаётся за вами.
"""

from __future__ import annotations

from typing import Any

PLAN_STALE_DAYS = 14  # план без входа дольше — повод пересмотреть: рынок мог уйти
SEVERITY_ORDER = {"stop": 0, "bad": 1, "attention": 2, "info": 3}
MAX_ITEMS = 12


def _q(qid: str, question: str, answer: str, status: str, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": qid, "question": question, "answer": answer, "status": status, "items": items or []}


def _money(v: float | None, cur: str) -> str:
    return "—" if v is None else f"{v:,.2f} {cur}".replace(",", " ")


def _signed(v: float, cur: str) -> str:
    return f"{v:+,.2f} {cur}".replace(",", " ")


def _attn(out: list[dict[str, Any]], severity: str, section: str, text: str, hint: str = "") -> None:
    out.append({"severity": severity, "section": section, "text": text, "hint": hint})


def build(
    accounts: list[dict[str, Any]],
    plans: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    watch: dict[str, Any],
    problem_sources: list[dict[str, Any]],
    now_ms: int,
) -> dict[str, Any]:
    """accounts — снимки Portfolio Truth по счетам; plans — незакрытые планы с полем review; signals — актуальные
    сигналы; watch — состояние наблюдения; problem_sources — источники со сбоем."""
    attention: list[dict[str, Any]] = []
    positions_n = sum(len(a["positions"]) for a in accounts)

    # 1. что происходит сейчас
    now_items = [
        {"label": "Наблюдение", "value": "идёт" if watch.get("background_scanner") else "не идёт"},
        {"label": "Активных сигналов", "value": len(signals)},
        {"label": "Открытых позиций", "value": positions_n},
        {"label": "Активных планов", "value": len(plans)},
    ]
    if not watch.get("background_scanner"):
        _attn(attention, "attention", "now", "Наблюдение не идёт: сигналы не ищутся, уведомления не приходят.",
              "Запустите приложение целиком или проверьте интервал сканирования в настройках.")
    quiet = (watch.get("quiet_hours") or {}).get("active_now")
    if quiet:
        _attn(attention, "info", "now", "Сейчас тихие часы: уведомления откладываются до их окончания.")
    parts = [f"сигналов {len(signals)}", f"позиций {positions_n}", f"планов {len(plans)}"]
    q_now = _q("now", "Что происходит сейчас?",
               ("Наблюдение идёт: " if watch.get("background_scanner") else "Наблюдение не идёт: ") + ", ".join(parts) + ".",
               "ok" if watch.get("background_scanner") else "attention", now_items)

    # 2. позиции
    pos_items: list[dict[str, Any]] = []
    pos_lines = []
    for a in accounts:
        cur = a["currency"]
        for p in a["positions"]:
            pos_items.append({
                "market": a["market"], "currency": cur, "symbol": p["symbol"], "qty": p["qty"], "avg_price": p["avg_price"],
                "mark_price": p.get("mark_price"), "mark_status": p.get("mark_status"),
                "unrealized_pnl": p.get("unrealized_pnl"), "unrealized_pct": p.get("unrealized_pct"),
                "stop": p["stop"], "weight_pct": p.get("weight_pct"),
            })
        if a["positions"]:
            unreal = a["unrealized_total"]
            part = f"{a['name']}: {len(a['positions'])} поз., нереализованный результат {_signed(unreal, cur)}"
            if a["truth"]["status"] != "ok":
                part += " (оценка неполная)"
            pos_lines.append(part)
    q_pos = _q("positions", "Какие у меня позиции?",
               "; ".join(pos_lines) + "." if pos_lines else "Открытых позиций нет.", "ok", pos_items)

    # 3. реальный риск
    risk_items: list[dict[str, Any]] = []
    risk_status, risk_lines = "ok", []
    for a in accounts:
        cur = a["currency"]
        over = a["heat_pct"] is not None and a["heat_pct"] > a["max_open_risk_pct"]
        risk_items.append({
            "market": a["market"], "name": a["name"], "currency": cur, "open_risk": a["open_risk"], "heat_pct": a["heat_pct"],
            "limit_pct": a["max_open_risk_pct"], "unprotected": a["unprotected"], "over_limit": over,
            "daily_pnl": a["daily_pnl"], "daily_limit": a["daily_limit"],
        })
        if not a["positions"]:
            continue
        line = f"{a['name']}: риск по стопам {_money(a['open_risk'], cur)}"
        if a["heat_pct"] is not None:
            line += f" ({a['heat_pct']:g}% из {a['max_open_risk_pct']:g}%)"
        risk_lines.append(line)
        if over or a["unprotected"]:
            risk_status = "bad"  # подробности и подсказки — в разделе правил, чтобы не дублировать их в списке внимания
    q_risk = _q("risk", "Сколько я реально рискую?",
                "; ".join(risk_lines) + ". Риск — расчётный сценарий по записанным стопам, цена может пройти стоп гэпом."
                if risk_lines else "Открытых позиций нет — риска по ним нет.", risk_status, risk_items)

    # 4. планы
    plan_items: list[dict[str, Any]] = []
    plan_status = "ok"
    for p in plans:
        rv = p["review"]
        stale_days = (now_ms / 1000 - p["created_at"]) / 86_400 if rv["status"] == "open" else 0
        flags = []
        if rv["status"] == "open" and stale_days >= PLAN_STALE_DAYS:
            flags.append(f"ждёт входа {int(stale_days)} дн. — рынок мог уйти, пересмотрите план")
        if rv.get("risk_exceeded"):
            flags.append("фактический риск выше бюджета плана")
        stop_dev = next((d for d in rv["deviations"] if d["code"] == "stop"), None)
        if rv["status"] == "entered" and stop_dev and stop_dev["actual"] is None:
            flags.append("вход выполнен, стоп из плана в журнале не записан")
        plan_items.append({
            "id": p["id"], "market": p["market"], "symbol": p["symbol"], "status": rv["status"], "entry": p["entry"],
            "stop": p["stop"], "target": p["target"], "risk_amount": p["risk_amount"], "currency": p["currency"], "flags": flags,
        })
        for fl in flags:
            sev = "bad" if "выше бюджета" in fl or "стоп" in fl else "attention"
            plan_status = "bad" if sev == "bad" else (plan_status if plan_status == "bad" else "attention")
            _attn(attention, sev, "plans", f"План {p['symbol']}: {fl}.", "Откройте план и сверьте его с журналом.")
    waiting = sum(1 for p in plans if p["review"]["status"] == "open")
    entered = sum(1 for p in plans if p["review"]["status"] == "entered")
    q_plans = _q("plans", "Какие планы активны?",
                 f"Планов: {len(plans)} (ждут входа — {waiting}, вход выполнен — {entered})." if plans else "Активных планов нет.",
                 plan_status, plan_items)

    # 5. сигналы
    sig_items = [
        {"id": s["id"], "market": s["market"], "symbol": s["symbol"], "strategy": s["strategy"], "side": s["side"],
         "tf": s.get("tf"), "late": bool(s.get("late")), "expires_at": s.get("expires_at"), "seen": bool(s.get("seen"))}
        for s in signals[:MAX_ITEMS]
    ]
    unseen = [s for s in signals if not s.get("seen")]
    if unseen:
        _attn(attention, "attention", "signals", f"Новых сигналов: {len(unseen)}.", "Решите по каждому: план, отказ или пропуск.")
    late = [s for s in signals if s.get("late")]
    if late:
        _attn(attention, "info", "signals", f"{len(late)} сигн. пришли с задержкой источника: к моменту получения они могли устареть.")
    q_sig = _q("signals", "Какие сигналы требуют внимания?",
               (f"Актуальных сигналов: {len(signals)}, из них новых {len(unseen)}." if signals else "Актуальных сигналов нет."),
               "attention" if unseen else "ok", sig_items)

    # 6. правила
    rules_items: list[dict[str, Any]] = []
    stop_today = False
    for a in accounts:
        cur = a["currency"]
        if a["daily_limit_breached"]:
            stop_today = True
            rules_items.append({"market": a["market"], "code": "daily_loss", "severity": "stop",
                                "text": f"{a['name']}: дневной лимит убытка достигнут ({_signed(a['daily_pnl'], cur)} при лимите −{_money(a['daily_limit'], cur)})."})
        if a["trades_limit_reached"]:
            rules_items.append({"market": a["market"], "code": "trades_limit", "severity": "bad",
                                "text": f"{a['name']}: лимит входов на день достигнут ({a['entries_today']})."})
        if a["heat_pct"] is not None and a["heat_pct"] > a["max_open_risk_pct"]:
            rules_items.append({"market": a["market"], "code": "heat", "severity": "bad",
                                "text": f"{a['name']}: суммарный риск {a['heat_pct']:g}% выше предела {a['max_open_risk_pct']:g}%."})
        for p in a["positions"]:
            if p.get("stop_breached"):
                rules_items.append({"market": a["market"], "code": "stop_breached", "severity": "bad",
                                    "text": f"{a['name']}: цена {p['symbol']} не выше записанного стопа — сверьте журнал с брокером."})
        for sym in a["unprotected"]:
            rules_items.append({"market": a["market"], "code": "no_stop", "severity": "bad",
                                "text": f"{a['name']}: у {sym} не записан стоп."})
        for w in a["warnings"]:
            if w.startswith("Концентрация"):
                rules_items.append({"market": a["market"], "code": "concentration", "severity": "attention", "text": f"{a['name']}: {w}"})
    for it in rules_items:
        _attn(attention, it["severity"], "rules", it["text"],
              "Ваш собственный план на сегодня — остановиться." if it["code"] == "daily_loss" else "")
    for p in plan_items:
        for fl in p["flags"]:
            if "выше бюджета" in fl:
                rules_items.append({"market": p["market"], "code": "plan_risk", "severity": "bad", "text": f"План {p['symbol']}: {fl}."})
    if rules_items:
        sev = "stop" if stop_today else ("bad" if any(i["severity"] == "bad" for i in rules_items) else "attention")
        q_rules = _q("rules", "Нарушаю ли я собственные правила?", f"Нарушено или достигнуто правил: {len(rules_items)}.",
                     "bad" if sev == "stop" else sev, rules_items)
    else:
        q_rules = _q("rules", "Нарушаю ли я собственные правила?", "Нарушений ваших лимитов и планов нет.", "ok", [])

    # 7. данные
    data_items: list[dict[str, Any]] = []
    data_status, data_lines = "ok", []
    for a in accounts:
        t = a["truth"]
        m = t["sources"]["market"]
        data_items.append({"market": a["market"], "name": a["name"], "truth": t["status"], "marked": m["marked"],
                           "stale": m["stale"], "missing": m["missing"], "reconciliation": t["reconciliation"]["status"],
                           "equity_complete": a["equity_complete"]})
        if t["status"] == "unmarked":
            data_status = "bad"
            data_lines.append(f"{a['name']}: нет рыночных цен, позиции оценены по входу")
            _attn(attention, "bad", "data", f"{a['name']}: нет рыночных цен — нереализованный результат и риск неизвестны.",
                  "Проверьте источник данных и подключение.")
        elif t["status"] == "partial":
            data_status = "attention" if data_status == "ok" else data_status
            data_lines.append(f"{a['name']}: цены устарели или отсутствуют у части позиций")
            _attn(attention, "attention", "data", f"{a['name']}: оценка по ценам неполная ({m['stale']} устар., {m['missing']} без цены).")
        if t["reconciliation"]["status"] == "mismatch":
            _attn(attention, "bad", "data", f"{a['name']}: журнал расходится с выпиской брокера.", "Сверьте позиции и поправьте журнал.")
            data_status = "bad"
    for s in problem_sources:
        data_status = "bad" if data_status == "bad" else "attention"
        word = "данные не обновляются" if s["status"] == "stale" else "ошибка источника"
        data_lines.append(f"{s['symbol']}: {word}")
        data_items.append({"market": s["market"], "symbol": s["symbol"], "source_status": s["status"], "message": s.get("message", "")})
        _attn(attention, "attention", "data", f"Источник {s['symbol']}: {word}; сигналы по нему приостановлены.")
    for w in watch.get("warnings", []):
        _attn(attention, "info", "data", w)
    q_data = _q("data", "Актуальны ли данные?",
                "Цены и источники в порядке." if not data_lines else "; ".join(data_lines) + ".", data_status, data_items)

    attention.sort(key=lambda x: SEVERITY_ORDER[x["severity"]])
    if stop_today:
        status = "stop"
        headline = "Дневной лимит убытка достигнут: по вашему собственному плану на сегодня стоп."
    elif any(x["severity"] in ("bad", "attention") for x in attention):
        status = "attention"
        top = next(x for x in attention if x["severity"] in ("bad", "attention"))
        headline = top["text"]
    else:
        status = "ok"
        headline = "Всё в порядке: нарушений ваших правил нет, данные свежие."
    return {
        "generated_at": now_ms, "status": status, "headline": headline,
        "questions": [q_now, q_pos, q_risk, q_plans, q_sig, q_rules, q_data],
        "attention": attention[:MAX_ITEMS],
        "notes": [
            "Кокпит собирает факты из журнала, цен, планов и сигналов; он ничего не блокирует и не исполняет.",
            "Риск по стопу — расчётный сценарий, а не гарантированный максимум потерь.",
        ],
    }
