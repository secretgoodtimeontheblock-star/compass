import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtDateTime, fmtNum, fmtPrice } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { Instrument, PlanDto, PlanReview } from "../types";

const STATUS: Record<PlanReview["status"], string> = {
  open: "вход не выполнен",
  entered: "позиция открыта",
  closed: "закрыт",
};

/** Планы по тикеру и сверка «план/факт» по записанным сделкам. */
export function PlansSection({ instrument, refreshKey }: { instrument: Instrument; refreshKey: number }) {
  const plans = useApi(() => api.plans({ market: instrument.market, symbol: instrument.symbol }), [
    instrument.market,
    instrument.symbol,
    refreshKey,
  ]);
  const list = plans.data ?? [];
  if (list.length === 0) return null;
  return (
    <>
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Планы по {instrument.symbol}
      </div>
      {list.map((p) => (
        <PlanCard key={p.id} plan={p} refreshKey={refreshKey} />
      ))}
    </>
  );
}

function PlanCard({ plan, refreshKey }: { plan: PlanDto; refreshKey: number }) {
  const [open, setOpen] = useState(false);
  const [rev, setRev] = useState<PlanReview>();
  const [err, setErr] = useState<string>();

  // сверка перечитывается при каждом изменении журнала: старая уже могла устареть
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api
      .planReview(plan.id)
      .then((r) => {
        if (alive) {
          setRev(r);
          setErr(undefined);
        }
      })
      .catch((e) => alive && setErr(e instanceof Error ? e.message : "Не удалось сверить план"));
    return () => {
      alive = false;
    };
  }, [open, plan.id, refreshKey]);

  return (
    <div className="card">
      <div className="row">
        <b>План №{plan.id}</b>
        <span className="muted">{fmtDateTime(plan.created_at * 1000)}</span>
      </div>
      <div className="num">
        Вход {fmtPrice(plan.entry)} · стоп {fmtPrice(plan.stop)}
        {plan.target != null ? ` · цель ${fmtPrice(plan.target)}` : ""} · {fmtNum(plan.qty, 6)} шт.
      </div>
      <div className="muted">
        {plan.strategy_version ?? "без стратегии"} · {plan.source}
        {plan.reward_risk != null ? ` · выгода/риск ${plan.reward_risk}` : ""}
      </div>
      <div className="muted">
        Расчётная потеря при стопе {fmtNum(plan.risk_amount)}, при худшем исполнении {fmtNum(plan.risk_amount_worse)}
        {plan.currency ? ` ${plan.currency}` : ""}
      </div>
      <div style={{ marginTop: 4 }}>{plan.reason}</div>
      <button className="btn ghost small" style={{ marginTop: 4 }} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {open ? "Скрыть сверку" : "Сверить с фактом"}
      </button>
      {err && open && <div className="error">{err}</div>}
      {open && rev && (
        <div style={{ marginTop: 6 }}>
          <div className="muted">Статус: {STATUS[rev.status]}</div>
          {rev.status === "open" ? (
            <div className="muted">Записей журнала по этому плану пока нет.</div>
          ) : (
            <>
              <table className="num">
                <thead>
                  <tr>
                    <th>Что</th>
                    <th className="r">План</th>
                    <th className="r">Факт</th>
                    <th className="r">Разница</th>
                  </tr>
                </thead>
                <tbody>
                  {rev.deviations.map((d) => (
                    <tr key={d.code}>
                      <td>{d.label}</td>
                      <td className="r">{fmtPrice(d.planned)}</td>
                      <td className="r">{d.actual == null ? "не записан" : fmtPrice(d.actual)}</td>
                      <td className={`r ${d.worse ? "down" : ""}`}>
                        {d.diff_pct == null ? "—" : `${d.diff_pct > 0 ? "+" : ""}${fmtNum(d.diff_pct, 2)}%`}
                        {d.worse ? " ⚠" : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rev.actual_risk_at_stop != null && (
                <div className={rev.risk_exceeded ? "down" : "muted"}>
                  Риск по записанному стопу {fmtNum(rev.actual_risk_at_stop)} при допустимых{" "}
                  {fmtNum(rev.risk_budget ?? 0)}
                  {rev.risk_exceeded ? " — превышен" : ""}
                </div>
              )}
              {rev.result_pct != null && (
                <div className="num">
                  Результат по ценам: {rev.result_pct > 0 ? "+" : ""}
                  {fmtNum(rev.result_pct, 2)}%{rev.r_multiple != null ? ` · ${fmtNum(rev.r_multiple, 2)} R` : ""}
                  <span className="muted"> · комиссии {fmtNum(rev.fees)} отдельно</span>
                </div>
              )}
              {rev.notes?.map((n) => (
                <p className="caveat" key={n}>
                  {n}
                </p>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
