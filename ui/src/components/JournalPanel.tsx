import { useEffect, useState } from "react";
import { api } from "../api";
import { useAiRun } from "../lib/ai-context";
import { fmtDateTime, fmtNum, fmtPrice, pnlClass, toLocalInput } from "../lib/format";
import type { Instrument, JournalDraft, JournalEntry, Position } from "../types";
import { AiAnswer } from "./AiAnswer";

interface Props {
  instrument: Instrument | undefined;
  entries: JournalEntry[]; // по выбранному тикеру
  positions: Position[]; // по всем
  lastPrice: number | undefined;
  onChanged: () => void;
  onSelect: (i: Instrument) => void;
  draft: JournalDraft | null;
}

export function JournalPanel({ instrument, entries, positions, lastPrice, onChanged, onSelect, draft }: Props) {
  const ai = useAiRun();
  const review = (refresh = false) => void ai.run((r) => api.reviewJournal({}, r), refresh);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("0");
  const [when, setWhen] = useState(() => toLocalInput(Date.now()));
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [plannedStop, setPlannedStop] = useState("");
  const [signalId, setSignalId] = useState<number | null>(null);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!draft) return;
    setSide(draft.side);
    setQty(draft.qty);
    setPrice(draft.price);
    setReason(draft.reason);
    setPlannedStop(draft.plannedStop);
    setNote(draft.note);
    setSignalId(draft.signalId);
    setWhen(toLocalInput(Date.now()));
  }, [draft]);

  const submit = async () => {
    if (!instrument) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.addJournal({
        market: instrument.market,
        symbol: instrument.symbol,
        side,
        qty: Number(qty),
        price: Number(price || lastPrice || 0),
        ts: new Date(when).getTime(),
        fee: Number(fee) || 0,
        note,
        reason,
        planned_stop: plannedStop ? Number(plannedStop) : null,
        signal_id: signalId,
      });
      setQty("");
      setNote("");
      setReason("");
      setPlannedStop("");
      setSignalId(null);
      setWhen(toLocalInput(Date.now()));
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setError(undefined);
    try {
      await api.removeJournal(id);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить");
    }
  };

  const exportCsv = async () => {
    setError(undefined);
    try {
      const res = await fetch("/api/journal.csv");
      if (!res.ok) throw new Error("Не удалось выгрузить журнал");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "compass-journal.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выгрузить журнал");
    }
  };

  const open = positions.filter((p) => p.qty > 0 || p.trades > 0);

  return (
    <div className="scroll">
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Мои позиции
        <button className="btn small" onClick={() => void exportCsv()}>
          Экспорт CSV
        </button>
      </div>
      {open.length === 0 ? (
        <div className="empty" style={{ paddingTop: 4 }}>
          Здесь появятся ваши сделки. Приложение ничего не покупает само: вы совершаете сделку у брокера или
          на бирже и записываете её сюда, чтобы видеть результат и разбирать ошибки.
        </div>
      ) : (
        <table className="num">
          <thead>
            <tr>
              <th>Тикер</th>
              <th className="r">Кол-во</th>
              <th className="r">Ср. цена</th>
              <th className="r">Зафикс.</th>
            </tr>
          </thead>
          <tbody>
            {open.map((p) => (
              <tr key={`${p.market}:${p.symbol}`}>
                <td>
                  <button
                    className="btn ghost small"
                    style={{ padding: 0, color: "var(--text)", fontWeight: 600 }}
                    onClick={() => onSelect({ market: p.market, symbol: p.symbol, name: p.symbol })}
                  >
                    {p.symbol}
                  </button>
                </td>
                <td className="r">{fmtNum(p.qty, 6)}</td>
                <td className="r">{p.avg_price == null ? "—" : fmtPrice(p.avg_price)}</td>
                <td className={`r ${pnlClass(p.realized_pnl)}`}>{fmtNum(p.realized_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {positions.length > 0 && (
        <div style={{ padding: "8px 12px 0" }}>
          {!ai.result && (
            <button className="btn" disabled={ai.busy} onClick={() => review()}>
              {ai.busy ? "AI разбирает…" : ai.error ? "Повторить" : "Разобрать журнал с AI"}
            </button>
          )}
          <AiAnswer ai={ai} onRefresh={() => review(true)} />
        </div>
      )}

      {instrument ? (
        <>
          <div className="panel-head" style={{ paddingBottom: 6 }}>
            Записать сделку · {instrument.symbol}
          </div>
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
            <label className="field">
              <span>Цена{lastPrice ? ` (сейчас ${fmtPrice(lastPrice)})` : ""}</span>
              <input
                type="number"
                min={0}
                step="any"
                value={price}
                placeholder={lastPrice ? String(lastPrice) : ""}
                onChange={(e) => setPrice(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Комиссия</span>
              <input type="number" min={0} step="any" value={fee} onChange={(e) => setFee(e.target.value)} />
            </label>
            <label className="field">
              <span>Когда</span>
              <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
            </label>
            {side === "buy" && (
              <label className="field">
                <span>Плановый стоп</span>
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={plannedStop}
                  onChange={(e) => setPlannedStop(e.target.value)}
                />
              </label>
            )}
            <label className="field full">
              <span>Причина входа или выхода</span>
              <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
            </label>
            <label className="field full">
              <span>Заметка</span>
              <textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
            </label>
            <button className="btn primary full" disabled={busy || !(Number(qty) > 0)} onClick={submit}>
              {busy ? "Сохраняем…" : "Записать"}
            </button>
          </div>
          {error && <div className="error">{error}</div>}

          <div className="panel-head" style={{ paddingBottom: 2 }}>
            История по {instrument.symbol}
          </div>
          {entries.length === 0 ? (
            <div className="empty" style={{ paddingTop: 4 }}>
              Записей по этому тикеру нет.
            </div>
          ) : (
            entries.map((e) => (
              <div className="card" key={e.id}>
                <div className="row">
                  <span className={`side-tag ${e.side === "buy" ? "up" : "down"}`}>
                    {e.side === "buy" ? "ПОКУПКА" : "ПРОДАЖА"}
                  </span>
                  <span className="muted">{fmtDateTime(e.ts)}</span>
                </div>
                <div className="num">
                  {fmtNum(e.qty, 6)} × {fmtPrice(e.price)}
                  {e.fee > 0 && <span className="muted"> · комиссия {fmtNum(e.fee)}</span>}
                </div>
                {e.planned_stop != null && e.side === "buy" && (
                  <div className="muted">Плановый стоп {fmtPrice(e.planned_stop)}</div>
                )}
                {e.reason && <div style={{ marginTop: 4 }}>{e.reason}</div>}
                {e.note && <div className="muted" style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>{e.note}</div>}
                <button className="btn ghost small" style={{ marginTop: 4 }} onClick={() => remove(e.id)}>
                  Удалить запись
                </button>
              </div>
            ))
          )}
        </>
      ) : (
        <div className="empty">Выберите тикер, чтобы записать по нему сделку.</div>
      )}
    </div>
  );
}
