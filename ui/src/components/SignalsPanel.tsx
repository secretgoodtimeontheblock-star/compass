import { useState } from "react";
import { api } from "../api";
import { useAiRun } from "../lib/ai-context";
import { fmtDateTime, fmtNum, fmtPrice, STRATEGY_SHORT } from "../lib/format";
import type { Instrument, JournalDraft, RiskResponse, Signal } from "../types";
import { AiAnswer } from "./AiAnswer";

interface Props {
  signals: Signal[];
  selected: Instrument | undefined;
  unseenCount: number;
  onSelect: (i: Instrument) => void;
  onMarkSeen: () => void;
  onRecord: (draft: JournalDraft) => void;
}

export function SignalsPanel({ signals, selected, unseenCount, onSelect, onMarkSeen, onRecord }: Props) {
  const [onlySelected, setOnlySelected] = useState(false);
  const list = onlySelected && selected ? signals.filter((s) => s.symbol === selected.symbol) : signals;

  return (
    <>
      <div className="panel-head">
        <label style={{ display: "inline-flex", gap: 6, fontWeight: 400, alignItems: "center" }}>
          <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} />
          Только {selected?.symbol ?? "выбранный тикер"}
        </label>
        <button className="btn small" disabled={unseenCount === 0} onClick={onMarkSeen}>
          Прочитано{unseenCount > 0 ? ` (${unseenCount})` : ""}
        </button>
      </div>
      <div className="scroll">
        {list.length === 0 && (
          <div className="empty">
            <b>Сигналов пока нет.</b> Добавьте тикеры в избранное и нажмите «Проверить сигналы»: приложение
            проверяет их само по расписанию, а сигнал появляется только когда стратегия меняет решение на
            закрытой свече.
          </div>
        )}
        {list.map((s) => (
          <SignalCard key={s.id} s={s} onSelect={onSelect} onRecord={onRecord} />
        ))}
      </div>
    </>
  );
}

function SignalCard({
  s,
  onSelect,
  onRecord,
}: {
  s: Signal;
  onSelect: (i: Instrument) => void;
  onRecord: (draft: JournalDraft) => void;
}) {
  const ai = useAiRun();
  const explain = (refresh = false) => void ai.run((r) => api.explainSignal(s.id, r), refresh);
  const [risk, setRisk] = useState<RiskResponse>();
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const buy = s.side === "buy";

  const calc = async () => {
    if (s.stop == null) return;
    setBusy(true);
    try {
      setRisk(await api.risk({ market: s.market, symbol: s.symbol, entry: s.price, stop: s.stop }));
      setErr(undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ошибка расчёта");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`card${s.seen ? "" : " unseen"}`}>
      <div className="row">
        <button
          className="btn ghost small"
          style={{ padding: 0, color: "var(--text)", fontWeight: 700, fontSize: 14 }}
          onClick={() => onSelect({ market: s.market, symbol: s.symbol, name: s.symbol })}
        >
          {s.symbol}
        </button>
        <span className={`side-tag ${buy ? "up" : "down"}`}>{buy ? "ВХОД" : "ВЫХОД"}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {STRATEGY_SHORT[s.strategy] ?? s.strategy} · {s.tf} · {fmtDateTime(s.candle_ts)}
      </div>
      <dl className="kv num">
        <dt>Цена закрытия</dt>
        <dd>{fmtPrice(s.price)}</dd>
        {buy && (
          <>
            <dt>Ориентир стопа</dt>
            <dd>{s.stop != null ? fmtPrice(s.stop) : "—"}</dd>
          </>
        )}
      </dl>
      {buy && s.stop != null && !risk && (
        <button className="btn small" style={{ marginTop: 8 }} disabled={busy} onClick={calc}>
          {busy ? "Считаем…" : "Сколько купить?"}
        </button>
      )}
      {err && <div className="down" style={{ marginTop: 6 }}>{err}</div>}
      {risk && (
        <dl className="kv num" style={{ borderTop: "1px solid var(--border)", paddingTop: 6 }}>
          <dt>Количество</dt>
          <dd>
            <b>{fmtNum(risk.qty, 6)}</b>
            {risk.lot_size > 1 ? ` (${fmtNum(risk.lots, 0)} лот. по ${risk.lot_size})` : ""}
          </dd>
          <dt>Вход обойдётся</dt>
          <dd>{fmtNum(risk.cost)}</dd>
          <dt>Потеря при стопе</dt>
          <dd>{fmtNum(risk.risk_amount)}</dd>
          {risk.capped && (
            <>
              <dt className="down">Внимание</dt>
              <dd className="down">ограничено капиталом</dd>
            </>
          )}
        </dl>
      )}
      {buy && risk && (
        <button
          className="btn small primary"
          style={{ marginTop: 8 }}
          onClick={() =>
            onRecord({
              market: s.market,
              symbol: s.symbol,
              side: "buy",
              qty: String(risk.qty),
              price: String(s.price),
              plannedStop: s.stop != null ? String(s.stop) : "",
              reason: "Вход по сигналу",
              note: "",
              signalId: s.id,
            })
          }
        >
          Записать эту сделку
        </button>
      )}
      {!ai.result && (
        <button className="btn small" style={{ marginTop: 8, marginLeft: buy && s.stop != null && !risk ? 6 : 0 }} disabled={ai.busy} onClick={() => explain()}>
          {ai.busy ? "AI думает…" : ai.error ? "Повторить" : "Объяснить простыми словами"}
        </button>
      )}
      <AiAnswer ai={ai} onRefresh={() => explain(true)} />
    </div>
  );
}
