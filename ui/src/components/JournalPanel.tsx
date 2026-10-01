import { useEffect } from "react";
import { useScopedState } from "../lib/use-scoped-state";
import { api } from "../api";
import { useAiRun } from "../lib/ai-context";
import { fmtDateTime, fmtNum, fmtPrice, pnlClass, toLocalInput } from "../lib/format";
import type { Instrument, JournalDraft, JournalEntry, JournalMode, Position } from "../types";
import { AiAnswer } from "./AiAnswer";
import { PlansSection } from "./PlansSection";
import { WeekReview } from "./WeekReview";

interface Props {
  instrument: Instrument | undefined;
  entries: JournalEntry[]; // по выбранному тикеру
  positions: Position[]; // по всем тикерам выбранного режима
  mode: JournalMode;
  onModeChange: (m: JournalMode) => void;
  lastPrice: number | undefined;
  onChanged: () => void;
  onSelect: (i: Instrument) => void;
  draft: JournalDraft | null;
  onDraftConsumed: () => void;
}

const MODE_LABELS: Record<JournalMode, string> = { real: "Реальные", paper: "Учебные", historical: "Исторические" };

export function JournalPanel({ instrument, entries, positions, mode, onModeChange, lastPrice, onChanged, onSelect, draft, onDraftConsumed }: Props) {
  const scope = `${instrument?.market}:${instrument?.symbol}:${mode}`;
  const ai = useAiRun();
  const review = (refresh = false) => void ai.run((r) => api.reviewJournal({}, r), refresh);
  const [side, setSide] = useScopedState<"buy" | "sell">(scope, "buy");
  const [qty, setQty] = useScopedState(scope, "");
  const [price, setPrice] = useScopedState(scope, "");
  const [fee, setFee] = useScopedState(scope, "0");
  const [when, setWhen] = useScopedState(scope, () => toLocalInput(Date.now()));
  const [note, setNote] = useScopedState(scope, "");
  const [reason, setReason] = useScopedState(scope, "");
  const [plannedStop, setPlannedStop] = useScopedState(scope, "");
  const [signalId, setSignalId] = useScopedState<number | null>(scope, null);
  const [planRef, setPlanRef] = useScopedState<{ uid: string; id?: number } | null>(scope, null);
  const [error, setError] = useScopedState<string | undefined>(scope, undefined);
  const [busy, setBusy] = useScopedState(scope, false);
  const [deleted, setDeleted] = useScopedState<JournalEntry[] | null>(scope, null);
  const [info, setInfo] = useScopedState<string | undefined>(scope, undefined);

  useEffect(() => {
    if (!draft) return;
    setSide(draft.side);
    setQty(draft.qty);
    setPrice(draft.price);
    setReason(draft.reason);
    setPlannedStop(draft.plannedStop);
    setNote(draft.note);
    setSignalId(draft.signalId);
    setPlanRef(draft.planUid ? { uid: draft.planUid, id: draft.planId } : null);
    setWhen(toLocalInput(Date.now()));
    onDraftConsumed();
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
        mode,
        plan_uid: planRef?.uid ?? null,
      });
      setQty("");
      setNote("");
      setReason("");
      setPlannedStop("");
      setSignalId(null);
      setPlanRef(null);
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
      setInfo("Запись убрана из журнала, но не стёрта: её можно вернуть в разделе «Удалённые записи».");
      if (deleted) setDeleted(await api.deletedJournal());
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

  const showDeleted = async () => {
    setError(undefined);
    try {
      setDeleted(await api.deletedJournal());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось загрузить удалённые записи");
    }
  };

  const restoreEntry = async (id: number) => {
    setError(undefined);
    try {
      await api.restoreJournalEntry(id);
      setDeleted(await api.deletedJournal());
      setInfo("Запись возвращена.");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось вернуть запись");
    }
  };

  const saveBackup = async () => {
    setError(undefined);
    try {
      const res = await fetch("/api/journal/backup.json");
      if (!res.ok) throw new Error("Не удалось создать резервную копию");
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `compass-journal-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
      setInfo("Резервная копия сохранена: все режимы и удалённые записи. Храните файл вне этого компьютера.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать резервную копию");
    }
  };

  const loadBackup = async (file: File | undefined) => {
    if (!file) return;
    setError(undefined);
    try {
      const r = await api.restoreJournalBackup(JSON.parse(await file.text()));
      setInfo(`Восстановлено записей: ${r.added}. Уже были в журнале: ${r.skipped}.`);
      onChanged();
    } catch (e) {
      setError(e instanceof SyntaxError ? "Файл не похож на резервную копию Compass" : e instanceof Error ? e.message : "Не удалось восстановить");
    }
  };

  const open = positions.filter((p) => p.qty > 0 || p.trades > 0);

  return (
    <div className="scroll">
      <div className="seg" role="group" aria-label="Режим сделок" style={{ margin: "8px 12px 0" }}>
        {(Object.keys(MODE_LABELS) as JournalMode[]).map((m) => (
          <button key={m} aria-pressed={mode === m} onClick={() => onModeChange(m)}>
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>
      {mode !== "real" && (
        <p className="notice" style={{ margin: "6px 12px 0" }}>
          {mode === "paper"
            ? "Учебные сделки: без реальных денег, в статистику реальных сделок не входят."
            : "Исторические сделки: разбор старой или чужой истории, в статистику реальных сделок не входят."}
        </p>
      )}
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Мои позиции · {MODE_LABELS[mode].toLowerCase()}
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

      {info && <div className="notice" style={{ margin: "6px 12px 0" }}>{info}</div>}
      {error && <div className="error">{error}</div>}
      <div className="row" style={{ padding: "8px 12px 0", gap: 6, flexWrap: "wrap" }}>
        <button className="btn small" onClick={() => void saveBackup()}>Резервная копия</button>
        <label className="btn small" style={{ cursor: "pointer" }}>
          Восстановить из копии
          <input type="file" accept="application/json,.json" hidden onChange={(e) => { void loadBackup(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        <button className="btn small" onClick={() => void showDeleted()}>Удалённые записи</button>
      </div>
      {deleted && (
        <div style={{ padding: "6px 12px 0" }}>
          {deleted.length === 0 ? (
            <div className="muted">Удалённых записей нет.</div>
          ) : (
            deleted.map((d) => (
              <div className="card" key={d.id}>
                <div className="row">
                  <span className="muted">{d.symbol} · {MODE_LABELS[d.mode]}</span>
                  <span className="muted">{fmtDateTime(d.ts)}</span>
                </div>
                <div className="num">{d.side === "buy" ? "Покупка" : "Продажа"} {fmtNum(d.qty, 6)} × {fmtPrice(d.price)}</div>
                <button className="btn small" onClick={() => void restoreEntry(d.id)}>Вернуть</button>
              </div>
            ))
          )}
        </div>
      )}

      {mode === "real" && <WeekReview refreshKey={entries.length + positions.length} />}

      {positions.length > 0 && mode === "real" && (
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
            Записать сделку · {instrument.symbol} · {MODE_LABELS[mode].toLowerCase()}
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
              <span title="Деньги за единицу актива. Для облигаций — в рублях за бумагу, не в % номинала.">
                Цена{lastPrice ? ` (сейчас ${fmtPrice(lastPrice)})` : ""}
              </span>
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
            {planRef && (
              <p className="notice full">
                Сделка будет связана с планом{planRef.id ? ` №${planRef.id}` : ""}: потом её можно сверить с планом.
              </p>
            )}
            <button className="btn primary full" disabled={busy || !(Number(qty) > 0)} onClick={submit}>
              {busy ? "Сохраняем…" : "Записать"}
            </button>
          </div>
          <PlansSection instrument={instrument} refreshKey={entries.length} />

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
