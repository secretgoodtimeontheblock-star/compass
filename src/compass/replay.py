"""Учебный счёт и воспроизведение свечей.

Тренировка без реальных заявок: история открывается по одной свече, пользователь ставит заявки, они
исполняются по открытию следующей свечи с теми же комиссиями и проскальзыванием, что в бэктесте, а стоп
срабатывает по тем же консервативным правилам (открытие за стопом — выход по открытию). Будущие свечи
никогда не покидают сервер: клиент получает только уже «открытые» (`cursor`), а расчёты идут по ним.

Данные сессии заморожены при создании (копия свечей в базе): позднейшие правки источника её не меняют.
Учебные сделки лежат в собственных таблицах и никогда не попадают в журнал реальных сделок, панель дня,
лимиты и статистику счетов.

Честные ограничения: пользователь видит даты и может помнить, что было на самом деле, — это не
проверка навыка в неизвестных условиях; OHLCV не даёт порядка внутри свечи; ликвидность не учитывается (спред входит в проскальзывание заявки)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from compass.db import Connection

MIN_WARMUP = 60  # свечей до начала воспроизведения: индикаторам и глазу нужна история
MIN_REPLAY = 20
MAX_STEP = 50
MIN_TRADES_FOR_STATS = 10


class ReplayError(ValueError):
    """Ошибка действия в учебной сессии (по-русски, показывается пользователю)."""


@dataclass(frozen=True, slots=True)
class Bar:
    ts: int
    o: float
    h: float
    low: float
    c: float
    v: float


@dataclass(frozen=True, slots=True)
class Fill:
    ts: int
    side: str  # buy | sell
    qty: float
    price: float
    fee: float
    reason: str  # order | stop | gap_stop
    stop: float | None = None  # у покупки — стоп на момент входа


@dataclass(slots=True)
class Sim:
    """Состояние счёта. Наличные и позиция всегда восстанавливаются из сделок."""

    cash: float
    qty: float = 0.0
    cost: float = 0.0  # стоимость позиции по средней цене с комиссией покупки
    stop: float | None = None
    pending: tuple[str, float, float | None] | None = None  # (сторона, количество, стоп)

    @property
    def avg_price(self) -> float | None:
        return self.cost / self.qty if self.qty > 1e-12 else None


def apply_fill(sim: Sim, f: Fill) -> None:
    if f.side == "buy":
        sim.cash -= f.qty * f.price + f.fee
        sim.qty += f.qty
        sim.cost += f.qty * f.price + f.fee
        sim.stop = f.stop
    else:
        avg = sim.cost / sim.qty if sim.qty > 1e-12 else 0.0
        sim.cash += f.qty * f.price - f.fee
        sim.cost -= f.qty * avg
        sim.qty -= f.qty
        if sim.qty < 1e-9:
            sim.qty = sim.cost = 0.0
            sim.stop = None


def apply_bar(sim: Sim, bar: Bar, fee_pct: float, slippage_pct: float) -> tuple[list[Fill], list[str]]:
    """Одна новая свеча: сначала исполняется заявка (по открытию), потом проверяется стоп внутри свечи."""
    fee, slip = fee_pct / 100, slippage_pct / 100
    fills: list[Fill] = []
    events: list[str] = []
    if sim.pending is not None:
        side, qty, stop = sim.pending
        sim.pending = None
        if side == "buy":
            price = bar.o * (1 + slip)
            f = Fill(bar.ts, "buy", qty, price, qty * price * fee, "order", stop)
            if qty * price + f.fee > sim.cash + 1e-9:
                events.append("Заявка на покупку отменена: на открытии не хватило свободных денег.")
            elif stop is not None and stop >= bar.o:
                events.append("Заявка на покупку отменена: свеча открылась не выше вашего стопа.")
            else:
                fills.append(f)
                apply_fill(sim, f)
        else:
            qty = min(qty, sim.qty)
            if qty <= 1e-12:
                events.append("Заявка на продажу отменена: позиции уже нет.")
            else:
                price = bar.o * (1 - slip)
                f = Fill(bar.ts, "sell", qty, price, qty * price * fee, "order")
                fills.append(f)
                apply_fill(sim, f)
    if sim.qty > 1e-12 and sim.stop is not None:
        stop = sim.stop
        if bar.o <= stop:  # гэп через стоп: исполнение по открытию, хуже уровня стопа
            price, reason = bar.o * (1 - slip), "gap_stop"
        elif bar.low <= stop:
            price, reason = stop * (1 - slip), "stop"
        else:
            price = reason = None  # type: ignore[assignment]
        if reason is not None:
            f = Fill(bar.ts, "sell", sim.qty, price, sim.qty * price * fee, reason)
            fills.append(f)
            apply_fill(sim, f)
            events.append("Сработал стоп" + (" с гэпом: исполнение хуже уровня стопа." if reason == "gap_stop" else "."))
    return fills, events


def build_sim(capital: float, fills: list[Fill]) -> Sim:
    sim = Sim(cash=capital)
    for f in fills:
        apply_fill(sim, f)
    return sim


def equity_curve(capital: float, fills: list[Fill], bars: list[Bar]) -> list[tuple[int, float]]:
    """Капитал на закрытии каждой открытой свечи."""
    by_ts: dict[int, list[Fill]] = {}
    for f in fills:
        by_ts.setdefault(f.ts, []).append(f)
    sim = Sim(cash=capital)
    out = []
    for b in bars:
        for f in by_ts.get(b.ts, []):
            apply_fill(sim, f)
        out.append((b.ts, sim.cash + sim.qty * b.c))
    return out


def summarize(capital: float, fills: list[Fill], bars: list[Bar], start_index: int) -> dict[str, Any]:
    """Результат сессии по уже открытым свечам. Ничего, кроме фактов сессии."""
    curve = equity_curve(capital, fills, bars[start_index:])
    eq = [v for _, v in curve] or [capital]
    peak, dd = eq[0], 0.0
    for v in [capital, *eq]:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    base = bars[start_index].c
    sim = Sim(cash=capital)
    realized: list[float] = []
    for f in fills:
        if f.side == "sell":
            avg = sim.cost / sim.qty if sim.qty > 1e-12 else 0.0
            realized.append(f.qty * (f.price - avg) - f.fee)
        apply_fill(sim, f)
    sells = len(realized)
    buys = [f for f in fills if f.side == "buy"]
    return {
        "equity": round(eq[-1], 2),
        "return_pct": round((eq[-1] / capital - 1) * 100, 2),
        "buy_hold_pct": round((bars[-1].c / base - 1) * 100, 2),
        "max_drawdown_pct": round(dd * 100, 2),
        "fills": len(fills),
        "closed_trades": sells,
        "win_rate_pct": round(sum(1 for p in realized if p > 0) / sells * 100, 1) if sells >= MIN_TRADES_FOR_STATS else None,
        "realized": round(sum(realized), 2),
        "fees": round(sum(f.fee for f in fills), 2),
        "entries_without_stop": sum(1 for f in buys if f.stop is None),
        "max_entry_risk_pct": round(
            max((f.qty * (f.price - f.stop) / capital * 100 for f in buys if f.stop is not None), default=0.0), 2
        ),
        "note": (
            None if sells >= MIN_TRADES_FOR_STATS
            else f"Закрытых сделок {sells}: меньше {MIN_TRADES_FOR_STATS} — доля прибыльных не показывается, выводов об умении делать нельзя."
        ),
    }


@dataclass
class Session:
    id: int
    uid: str
    created_at: int
    market: str
    symbol: str
    tf: str
    source: str
    capital: float
    fee_pct: float
    slippage_pct: float
    start_index: int
    cursor: int  # индекс последней открытой свечи
    status: str
    data_hash: str
    bars: list[Bar] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    sim: Sim | None = None


_COLS = "id, uid, created_at, market, symbol, tf, source, capital, fee_pct, slippage_pct, start_index, cursor, status, data_hash, pending, stop, candles"


class ReplayStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._lock = conn.lock

    def create(self, *, market: str, symbol: str, tf: str, source: str, capital: float, fee_pct: float,
               slippage_pct: float, bars: list[Bar], start_index: int, data_hash: str) -> Session:
        if start_index < MIN_WARMUP - 1 or len(bars) - 1 - start_index < MIN_REPLAY:
            raise ReplayError(
                f"Нужно хотя бы {MIN_WARMUP} свечей истории до начала и {MIN_REPLAY} свечей для воспроизведения"
            )
        uid = uuid.uuid4().hex
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO replay_sessions (uid, created_at, market, symbol, tf, source, capital, fee_pct, "
                "slippage_pct, start_index, cursor, status, data_hash, candles) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, int(time.time()), market, symbol, tf, source, capital, fee_pct, slippage_pct, start_index,
                 start_index, "active", data_hash,
                 json.dumps([[b.ts, b.o, b.h, b.low, b.c, b.v] for b in bars])),
            )
        return self.get(int(cur.lastrowid))  # type: ignore[return-value]

    def get(self, session_id: int) -> Session | None:
        with self._lock:
            row = self._conn.execute(f"SELECT {_COLS} FROM replay_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            fills = self._conn.execute(
                "SELECT ts, side, qty, price, fee, reason, stop FROM replay_fills WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        (id_, uid, created, market, symbol, tf, source, capital, fee, slip, start, cursor, status, dh, pending, stop,
         candles) = row
        s = Session(id_, uid, created, market, symbol, tf, source, capital, fee, slip, start, cursor, status, dh)
        s.bars = [Bar(*c) for c in json.loads(candles)]
        s.fills = [Fill(*f) for f in fills]
        s.sim = build_sim(capital, s.fills)
        s.sim.stop = stop if s.sim.qty > 1e-12 else None  # столбец — актуальный стоп, в том числе изменённый вручную
        if pending:
            side, qty, pstop = json.loads(pending)
            s.sim.pending = (side, qty, pstop)
        return s

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, market, symbol, tf, status, cursor, start_index, "
                "json_array_length(candles) FROM replay_sessions ORDER BY id DESC LIMIT 100"
            ).fetchall()
        keys = ("id", "created_at", "market", "symbol", "tf", "status", "cursor", "start_index", "total")
        out = []
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            d["replayed"] = d["cursor"] - d["start_index"]
            d["remaining"] = d["total"] - 1 - d["cursor"]
            del d["cursor"], d["start_index"], d["total"]
            out.append(d)
        return out

    def save_pending(self, session_id: int, pending: tuple[str, float, float | None] | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE replay_sessions SET pending = ? WHERE id = ?",
                (None if pending is None else json.dumps(list(pending)), session_id),
            )

    def save_stop(self, session_id: int, stop: float | None) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE replay_sessions SET stop = ? WHERE id = ?", (stop, session_id))

    def advance(self, session_id: int, cursor: int, fills: list[Fill], sim: Sim, status: str) -> None:
        with self._lock, self._conn:
            for f in fills:
                self._conn.execute(
                    "INSERT INTO replay_fills (session_id, ts, side, qty, price, fee, reason, stop) VALUES (?,?,?,?,?,?,?,?)",
                    (session_id, f.ts, f.side, f.qty, f.price, f.fee, f.reason, f.stop),
                )
            self._conn.execute(
                "UPDATE replay_sessions SET cursor = ?, status = ?, pending = ?, stop = ? WHERE id = ?",
                (cursor, status, None if sim.pending is None else json.dumps(list(sim.pending)), sim.stop, session_id),
            )

    def finish(self, session_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE replay_sessions SET status = 'finished' WHERE id = ?", (session_id,))
