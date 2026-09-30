import { api } from "../api";
import { fmtNum, pnlClass } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { WeekAccount } from "../types";

/** Недельный разбор по рассчитанным показателям журнала. Каждый счёт отдельно. */
export function WeekReview({ refreshKey }: { refreshKey: number }) {
  const week = useApi(() => api.reviewWeek(), [refreshKey]);
  const accounts = (week.data?.accounts ?? []).filter((a) => a.entries > 0 || a.closed_trades > 0);
  return (
    <div style={{ padding: "0 12px" }}>
      <div className="panel-head" style={{ padding: "8px 0 2px" }}>
        Неделя
      </div>
      {week.error && <div className="error">{week.error}</div>}
      {week.data && accounts.length === 0 && <p className="caveat">За последние 7 дней в журнале нет реальных сделок.</p>}
      {accounts.map((a) => (
        <Card key={a.market} a={a} />
      ))}
    </div>
  );
}

function Card({ a }: { a: WeekAccount }) {
  const m = (v: number | null) => (v == null ? "—" : `${fmtNum(v)} ${a.currency}`);
  const d = a.plan_deviations;
  return (
    <div className="card">
      <div className="row">
        <b>{a.name}</b>
        <span className="muted">{a.days} дн.</span>
      </div>
      <dl className="kv num">
        <dt>Зафиксировано после комиссий</dt>
        <dd className={pnlClass(a.realized)}>{m(a.realized)}</dd>
        <dt>Закрытых сделок / входов</dt>
        <dd>
          {a.closed_trades} / {a.entries}
        </dd>
        <dt>Комиссии</dt>
        <dd>{m(a.fees)}</dd>
        {a.win_rate_pct != null && (
          <>
            <dt>Доля прибыльных</dt>
            <dd>{fmtNum(a.win_rate_pct, 1)}%</dd>
          </>
        )}
        {a.best && (
          <>
            <dt>Лучшая / худшая</dt>
            <dd>
              {a.best.symbol} {m(a.best.pnl)} / {a.worst?.symbol} {m(a.worst?.pnl ?? null)}
            </dd>
          </>
        )}
        <dt>Входов без стопа</dt>
        <dd className={a.entries_without_stop > 0 ? "down" : ""}>{a.entries_without_stop}</dd>
        <dt>Входов по плану / без плана</dt>
        <dd>
          {a.entries_with_plan} / {a.entries_without_plan}
        </dd>
      </dl>
      {d.plans_reviewed > 0 && (
        <p className="caveat" style={{ margin: "4px 0" }}>
          Сверка с планами ({d.plans_reviewed}): вход хуже плана — {d.worse_entry}, объём больше плана — {d.bigger_qty}, риск
          выше допустимого — {d.risk_exceeded}, стоп не записан — {d.no_stop}.
        </p>
      )}
      {a.days_over_daily_limit.length > 0 && (
        <div className="notice">Дни с убытком выше дневного лимита: {a.days_over_daily_limit.join(", ")}.</div>
      )}
      {a.days_over_trades_limit.length > 0 && (
        <div className="notice">
          Дни с числом входов выше вашего лимита ({a.max_trades_per_day}): {a.days_over_trades_limit.join(", ")}.
        </div>
      )}
      {a.notes.map((n) => (
        <p className="caveat" key={n} style={{ margin: "2px 0 0" }}>
          {n}
        </p>
      ))}
    </div>
  );
}
