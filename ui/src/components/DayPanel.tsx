import { api } from "../api";
import { fmtNum, pnlClass } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { AccountSnapshot } from "../types";

/** Панель дня: по каждому счёту отдельно, без сложения рублей и USDT. */
export function DayPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const day = useApi(() => api.day(), [refreshKey], 60_000);
  const accounts = day.data?.accounts ?? [];
  if (day.error && accounts.length === 0) return <div className="notice">Панель дня недоступна: {day.error}</div>;
  if (accounts.length === 0) return null;
  return (
    <div style={{ padding: "6px 12px 0" }}>
      <div className="panel-head" style={{ padding: "0 0 4px" }}>
        Панель дня
      </div>
      {accounts.map((a) => (
        <AccountCard key={a.market} a={a} />
      ))}
      <p className="caveat" style={{ margin: "2px 0 6px" }}>
        {day.data?.notes.join(" ")} Без рыночных цен: нереализованный результат не учитывается.
      </p>
    </div>
  );
}

function AccountCard({ a }: { a: AccountSnapshot }) {
  const money = (v: number | null) => (v == null ? "—" : `${fmtNum(v)} ${a.currency}`);
  return (
    <div className="card">
      <div className="row">
        <b>{a.name}</b>
        <span className="muted">{a.currency}</span>
      </div>
      <dl className="kv num">
        <dt>Свободно</dt>
        <dd>{money(a.free)}</dd>
        <dt>В позициях</dt>
        <dd>
          {money(a.exposure)}
          {a.exposure_pct != null ? ` (${fmtNum(a.exposure_pct, 1)}%)` : ""}
        </dd>
        <dt>Риск по стопам</dt>
        <dd className={a.heat_pct != null && a.heat_pct > a.max_open_risk_pct ? "down" : ""}>
          {money(a.open_risk)}
          {a.heat_pct != null ? ` (${fmtNum(a.heat_pct, 2)}% из ${a.max_open_risk_pct}%)` : ""}
        </dd>
        <dt>Сегодня зафиксировано</dt>
        <dd className={pnlClass(a.daily_pnl)}>{money(a.daily_pnl)}</dd>
      </dl>
      {a.positions.length > 0 && (
        <table className="num">
          <tbody>
            {a.positions.map((p) => (
              <tr key={p.symbol}>
                <td>{p.symbol}</td>
                <td className="r">{fmtNum(p.qty, 6)}</td>
                <td className="r muted">стоп {p.stop == null ? "не записан" : fmtNum(p.stop)}</td>
                <td className="r">{p.risk_at_stop == null ? "риск не ограничен" : `риск ${fmtNum(p.risk_at_stop)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {a.warnings.map((w) => (
        <div className="notice" key={w} style={{ marginTop: 6 }}>
          {w}
        </div>
      ))}
    </div>
  );
}
