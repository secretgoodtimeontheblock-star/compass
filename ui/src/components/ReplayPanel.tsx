import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { fmtDateTime, fmtNum, fmtPct, fmtPrice, pnlClass } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { Candle, Instrument, JournalEntry, ReplaySession } from "../types";
import { PriceChart } from "./PriceChart";

const OVERLAYS = { sma20: false, sma50: false, volume: false };
const REASON: Record<string, string> = { order: "по заявке", stop: "стоп", gap_stop: "стоп с гэпом" };

interface Props {
  instrument: Instrument | undefined;
  tf: string;
  theme: string;
}

/** Тренировка на истории: свечи открываются по одной, реальных заявок нет, в журнал ничего не пишется. */
export function ReplayPanel({ instrument, tf, theme }: Props) {
  const [id, setId] = useState<number | null>(null);
  const list = useApi(() => api.replayList(), [id]);
  if (id == null) {
    return <StartForm instrument={instrument} tf={tf} onOpen={setId} sessions={list.data ?? []} />;
  }
  return <Session id={id} theme={theme} onExit={() => setId(null)} />;
}

function StartForm({
  instrument,
  tf,
  onOpen,
  sessions,
}: {
  instrument: Instrument | undefined;
  tf: string;
  onOpen: (id: number) => void;
  sessions: { id: number; market: string; symbol: string; tf: string; status: string; replayed: number; remaining: number }[];
}) {
  const [bars, setBars] = useState(120);
  const [capital, setCapital] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const start = async () => {
    if (!instrument) return;
    setBusy(true);
    setError(undefined);
    try {
      const s = await api.replayStart({
        market: instrument.market,
        symbol: instrument.symbol,
        tf,
        replay_bars: bars,
        capital: capital ? Number(capital) : undefined,
      });
      onOpen(s.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось начать тренировку");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scroll">
      <div className="panel-head">Учебный счёт</div>
      <p className="caveat" style={{ margin: "0 12px" }}>
        История открывается по одной свече. Вы ставите заявки, они исполняются по открытию следующей свечи, как в
        проверке стратегий. Реальных заявок нет, сделки не попадают в журнал и статистику реальных сделок.
      </p>
      {!instrument ? (
        <div className="empty">Выберите тикер, на котором хотите потренироваться.</div>
      ) : (
        <div className="form-grid" style={{ paddingTop: 8 }}>
          <label className="field">
            <span>Сколько свечей открыть</span>
            <select value={bars} onChange={(e) => setBars(Number(e.target.value))}>
              {[60, 120, 250, 500].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Учебные деньги (необязательно)</span>
            <input type="number" min={1} step="any" value={capital} placeholder="как на счёте" onChange={(e) => setCapital(e.target.value)} />
          </label>
          <button className="btn primary full" disabled={busy} onClick={() => void start()}>
            {busy ? "Готовим…" : `Тренироваться на ${instrument.symbol} (${tf})`}
          </button>
          {error && <div className="error full">{error}</div>}
        </div>
      )}
      {sessions.length > 0 && (
        <>
          <div className="panel-head" style={{ paddingBottom: 2 }}>
            Прошлые тренировки
          </div>
          {sessions.map((s) => (
            <div className="card" key={s.id}>
              <div className="row">
                <b>
                  №{s.id} · {s.symbol} · {s.tf}
                </b>
                <span className="muted">{s.status === "finished" ? "завершена" : `осталось ${s.remaining} св.`}</span>
              </div>
              <button className="btn small" onClick={() => onOpen(s.id)}>
                Открыть
              </button>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function Session({ id, theme, onExit }: { id: number; theme: string; onExit: () => void }) {
  const [s, setS] = useState<ReplaySession>();
  const [candles, setCandles] = useState<Candle[]>([]);
  const [error, setError] = useState<string>();
  const [events, setEvents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("");
  const [stop, setStop] = useState("");
  const [newStop, setNewStop] = useState("");

  const load = useCallback(async () => {
    const [state, cs] = await Promise.all([api.replayGet(id), api.replayCandles(id)]);
    setS(state);
    setCandles(cs.candles);
  }, [id]);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить сессию"));
  }, [load]);

  const run = async (fn: () => Promise<ReplaySession & { events?: string[] }>) => {
    setBusy(true);
    setError(undefined);
    try {
      const r = await fn();
      setEvents(r.events ?? []);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  };

  const trades = useMemo<JournalEntry[]>(
    () =>
      (s?.fills ?? []).map((f, i) => ({
        id: i,
        market: (s?.market ?? "moex") as JournalEntry["market"],
        symbol: s?.symbol ?? "",
        side: f.side,
        qty: f.qty,
        price: f.price,
        ts: f.ts,
        fee: f.fee,
        note: "",
        signal_id: null,
        planned_stop: f.stop,
        reason: "",
        mode: "paper",
        uid: String(i),
        deleted_at: null,
        plan_uid: null,
      })),
    [s],
  );

  if (!s) return <div className="empty">{error ?? "Загрузка…"}</div>;
  const active = s.status === "active";
  const r = s.result;
  const money = (v: number) => fmtNum(v);

  return (
    <div className="scroll">
      <div className="panel-head">
        №{s.id} · {s.symbol} · {s.tf}
        <button className="btn small" onClick={onExit}>
          К списку
        </button>
      </div>
      <p className="notice" style={{ margin: "0 12px 6px" }}>
        Тренировка на учебных деньгах: реальных заявок нет. Последняя открытая свеча: {fmtDateTime(s.cursor_ts)}.
        {active ? ` Осталось свечей: ${s.remaining}.` : " Сессия завершена."}
      </p>
      <div style={{ position: "relative", height: 260, margin: "0 8px" }}>
        <PriceChart candles={candles} tf={s.tf} signals={[]} trades={trades} overlays={OVERLAYS} theme={theme} resetKey={`replay:${s.id}`} />
      </div>

      {active && (
        <div className="row" style={{ padding: "8px 12px 0", gap: 6, flexWrap: "wrap" }}>
          {[1, 5, 20].map((n) => (
            <button key={n} className="btn small" disabled={busy || s.remaining < 1} onClick={() => void run(() => api.replayStep(s.id, n))}>
              +{n} св.
            </button>
          ))}
          <button className="btn small" disabled={busy} onClick={() => void run(() => api.replayFinish(s.id))}>
            Завершить
          </button>
        </div>
      )}
      {events.map((e) => (
        <div className="notice" key={e} style={{ margin: "6px 12px 0" }}>
          {e}
        </div>
      ))}
      {error && <div className="error">{error}</div>}

      <dl className="kv num" style={{ padding: "8px 12px 0" }}>
        <dt>Последняя цена</dt>
        <dd>{fmtPrice(s.last_close)}</dd>
        <dt>Свободные деньги</dt>
        <dd>{money(s.cash)}</dd>
        <dt>Позиция</dt>
        <dd>
          {s.qty > 0 ? `${fmtNum(s.qty, 6)} шт. по ${fmtPrice(s.avg_price ?? 0)}` : "нет"}
          {s.stop != null ? ` · стоп ${fmtPrice(s.stop)}` : s.qty > 0 ? " · стоп не задан" : ""}
        </dd>
        <dt>Капитал</dt>
        <dd className={pnlClass(r.return_pct)}>
          {money(r.equity)} ({fmtPct(r.return_pct)})
        </dd>
        <dt>«Купил и держи» с начала</dt>
        <dd className={pnlClass(r.buy_hold_pct)}>{fmtPct(r.buy_hold_pct)}</dd>
      </dl>

      {active && (
        <>
          <div className="panel-head" style={{ paddingBottom: 6 }}>
            Заявка на следующую свечу
          </div>
          {s.pending ? (
            <div className="card">
              <div className="num">
                {s.pending.side === "buy" ? "Покупка" : "Продажа"} {fmtNum(s.pending.qty, 6)}
                {s.pending.stop != null ? ` · стоп ${fmtPrice(s.pending.stop)}` : ""}
              </div>
              <div className="muted">Исполнится по открытию следующей свечи.</div>
              <button className="btn small" disabled={busy} onClick={() => void run(() => api.replayCancelOrder(s.id))}>
                Отменить заявку
              </button>
            </div>
          ) : (
            <div className="form-grid">
              <div className="seg full" style={{ justifySelf: "start" }}>
                <button aria-pressed={side === "buy"} onClick={() => setSide("buy")}>
                  Покупка
                </button>
                <button aria-pressed={side === "sell"} onClick={() => setSide("sell")}>
                  Продажа
                </button>
              </div>
              <label className="field">
                <span>Количество</span>
                <input type="number" min={0} step="any" value={qty} onChange={(e) => setQty(e.target.value)} />
              </label>
              {side === "buy" && (
                <label className="field">
                  <span>Стоп (цена)</span>
                  <input type="number" min={0} step="any" value={stop} onChange={(e) => setStop(e.target.value)} />
                </label>
              )}
              <button
                className="btn primary full"
                disabled={busy || !(Number(qty) > 0)}
                onClick={() =>
                  void run(() => api.replayOrder(s.id, { side, qty: Number(qty), stop: side === "buy" && stop ? Number(stop) : null })).then(() => {
                    setQty("");
                    setStop("");
                  })
                }
              >
                Поставить заявку
              </button>
              {s.qty > 0 && (
                <>
                  <label className="field">
                    <span>Новый стоп позиции</span>
                    <input type="number" min={0} step="any" value={newStop} onChange={(e) => setNewStop(e.target.value)} />
                  </label>
                  <button
                    className="btn small"
                    disabled={busy || !newStop}
                    onClick={() => void run(() => api.replayStop(s.id, Number(newStop))).then(() => setNewStop(""))}
                  >
                    Изменить стоп
                  </button>
                </>
              )}
            </div>
          )}
        </>
      )}

      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Итоги тренировки
      </div>
      <ul className="caveat" style={{ paddingLeft: 18 }}>
        <li>Закрытых сделок: {r.closed_trades}; комиссии {money(r.fees)}; зафиксировано {money(r.realized)}.</li>
        <li>Максимальная просадка {fmtPct(r.max_drawdown_pct, 2, false)}.</li>
        <li>Входов без стопа: {r.entries_without_stop}; максимальный риск на вход {fmtPct(r.max_entry_risk_pct, 2, false)} капитала.</li>
        {r.win_rate_pct != null && <li>Доля прибыльных: {fmtPct(r.win_rate_pct, 1, false)}.</li>}
        {r.note && <li>{r.note}</li>}
      </ul>

      {s.fills.length > 0 && (
        <table className="num">
          <thead>
            <tr>
              <th>Когда</th>
              <th>Что</th>
              <th className="r">Кол-во</th>
              <th className="r">Цена</th>
              <th>Как</th>
            </tr>
          </thead>
          <tbody>
            {[...s.fills].reverse().map((f, i) => (
              <tr key={`${f.ts}${i}`}>
                <td>{fmtDateTime(f.ts)}</td>
                <td className={f.side === "buy" ? "up" : "down"}>{f.side === "buy" ? "покупка" : "продажа"}</td>
                <td className="r">{fmtNum(f.qty, 6)}</td>
                <td className="r">{fmtPrice(f.price)}</td>
                <td className="muted">
                  {REASON[f.reason] ?? f.reason}
                  {f.risk_pct != null ? ` · риск ${fmtPct(f.risk_pct, 2, false)}` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <ul className="caveat" style={{ paddingLeft: 18 }}>
        {s.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}
