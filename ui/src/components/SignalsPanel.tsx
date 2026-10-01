import { useState } from "react";
import { api } from "../api";
import { useAiRun } from "../lib/ai-context";
import { fmtDateTime, fmtNum, fmtPrice, STRATEGY_SHORT } from "../lib/format";
import type { Instrument, JournalDraft, PlanDto, RiskResponse, Signal } from "../types";
import { AiAnswer } from "./AiAnswer";
import { CockpitPanel } from "./CockpitPanel";
import { DayPanel } from "./DayPanel";
import { TickerIcon } from "./TickerIcon";

interface Props {
  signals: Signal[];
  selected: Instrument | undefined;
  unseenCount: number;
  onSelect: (i: Instrument) => void;
  onMarkSeen: () => void;
  onRecord: (draft: JournalDraft) => void;
  onChanged: () => void;
}

const STATUS_LABEL: Record<Signal["status"], string> = {
  active: "Актуален",
  expired: "Устарел",
  acted: "Есть план или запись в журнале",
  dismissed: "Отклонён",
};

export function SignalsPanel({ signals, selected, unseenCount, onSelect, onMarkSeen, onRecord, onChanged }: Props) {
  const [onlySelected, setOnlySelected] = useState(false);
  const [onlyActive, setOnlyActive] = useState(true);
  const byInstrument = onlySelected && selected ? signals.filter((s) => s.symbol === selected.symbol) : signals;
  const list = onlyActive ? byInstrument.filter((s) => s.status === "active") : byInstrument;

  return (
    <>
      <div className="panel-head">
        <label style={{ display: "inline-flex", gap: 6, fontWeight: 400, alignItems: "center" }}>
          <input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} />
          Только {selected?.symbol ?? "выбранный тикер"}
        </label>
        <label style={{ display: "inline-flex", gap: 6, fontWeight: 400, alignItems: "center" }}>
          <input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} />
          Только актуальные
        </label>
        <button className="btn small" disabled={unseenCount === 0} onClick={onMarkSeen}>
          Прочитано{unseenCount > 0 ? ` (${unseenCount})` : ""}
        </button>
      </div>
      <div className="scroll">
        <CockpitPanel refreshKey={signals.length} />
        <DayPanel refreshKey={signals.length} />
        {list.length === 0 && (
          <div className="empty">
            <b>{onlyActive && byInstrument.length > 0 ? "Актуальных сигналов нет." : "Сигналов пока нет."}</b> Добавьте тикеры в избранное и нажмите «Проверить сигналы»: приложение
            проверяет их само по расписанию, а сигнал появляется только когда стратегия меняет решение на
            закрытой свече.
          </div>
        )}
        {list.map((s) => (
          <SignalCard key={s.id} s={s} onSelect={onSelect} onRecord={onRecord} onChanged={onChanged} />
        ))}
      </div>
    </>
  );
}

function SignalCard({
  s,
  onSelect,
  onRecord,
  onChanged,
}: {
  s: Signal;
  onSelect: (i: Instrument) => void;
  onRecord: (draft: JournalDraft) => void;
  onChanged: () => void;
}) {
  const ai = useAiRun();
  const explain = (refresh = false) => void ai.run((r) => api.explainSignal(s.id, r), refresh);
  const [risk, setRisk] = useState<RiskResponse>();
  const [err, setErr] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [plan, setPlan] = useState<PlanDto>();
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("Вход по сигналу");
  const buy = s.side === "buy";

  const act = async (fn: (id: number) => Promise<Signal>) => {
    try {
      await fn(s.id);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось изменить сигнал");
    }
  };

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

  const savePlan = async () => {
    if (s.stop == null) return;
    setBusy(true);
    try {
      setPlan(
        await api.createPlan({
          market: s.market,
          symbol: s.symbol,
          entry: s.price,
          stop: s.stop,
          target: target ? Number(target) : null,
          reason,
          signal_id: s.id,
        }),
      );
      setErr(undefined);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Не удалось сохранить план");
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
          <TickerIcon symbol={s.symbol} market={s.market} size={18} /> {s.symbol}
        </button>
        <span className={`side-tag ${buy ? "up" : "down"}`}>{buy ? "ВХОД" : "ВЫХОД"}</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {STATUS_LABEL[s.status]}
        {s.status === "active" ? ` до ${fmtDateTime(s.expires_at)}` : ""}
        {s.late && (
          <span className="down" style={{ marginLeft: 6 }} title="Данные приходят позже, чем живёт сигнал">
            ⚠ данные с задержкой ~{Math.round(s.delay_seconds / 60)} мин
          </span>
        )}
        {s.status === "dismissed" ? (
          <button className="btn ghost small" style={{ marginLeft: 6 }} onClick={() => void act(api.restoreSignal)}>
            Вернуть
          </button>
        ) : s.status !== "acted" ? (
          <button className="btn ghost small" style={{ marginLeft: 6 }} onClick={() => void act(api.dismissSignal)}>
            Отклонить
          </button>
        ) : null}
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
          <dd>{fmtNum(risk.cost)}{risk.currency ? ` ${risk.currency}` : ""}</dd>
          <dt>Расчётная потеря при стопе</dt>
          <dd>{fmtNum(risk.risk_amount)}</dd>
          <dt>При худшем исполнении</dt>
          <dd>{fmtNum(risk.risk_amount_worse)}</dd>
        </dl>
      )}
      {risk?.warnings.map((w) => (
        <div key={w} className="notice" style={{ marginTop: 6 }}>{w}</div>
      ))}
      {risk && (
        <p className="caveat" style={{ marginTop: 6 }}>{risk.assumptions.join(" ")}</p>
      )}
      {buy && risk && risk.qty > 0 && !plan && (
        <div className="form-grid" style={{ padding: "8px 0 0" }}>
          <label className="field">
            <span>Цель (необязательно)</span>
            <input type="number" min={0} step="any" value={target} onChange={(e) => setTarget(e.target.value)} />
          </label>
          <label className="field full">
            <span>Причина входа</span>
            <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button className="btn small primary full" disabled={busy || !reason.trim()} onClick={savePlan}>
            Сохранить план
          </button>
          <p className="caveat full">План нельзя изменить после сохранения: потом по нему сверяется, как вы вошли на деле.</p>
        </div>
      )}
      {plan && (
        <div className="notice" style={{ marginTop: 6 }}>
          План №{plan.id} сохранён · {plan.strategy_version ?? "без стратегии"}
          {plan.reward_risk != null ? ` · выгода/риск ${plan.reward_risk}` : ""}
        </div>
      )}
      {buy && risk && risk.qty > 0 && (
        <button
          className={plan ? "btn small primary" : "btn small ghost"}
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
              planUid: plan?.uid,
              planId: plan?.id,
            })
          }
        >
          {plan ? "Записать сделку по плану" : "Записать без плана"}
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
