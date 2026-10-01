import { api } from "../api";
import { fmtNum, pnlClass } from "../lib/format";
import { atLeast, useLevel } from "../lib/experience";
import { useApi } from "../lib/use-api";
import type { AccountSnapshot, WatchStatus } from "../types";

/** Панель дня: по каждому счёту отдельно, без сложения рублей и USDT. */
export function DayPanel({ refreshKey = 0 }: { refreshKey?: number }) {
  const day = useApi(() => api.day(), [refreshKey], 60_000);
  const watch = useApi(() => api.watch(), [refreshKey], 60_000);
  const accounts = day.data?.accounts ?? [];
  if (day.error && accounts.length === 0) return <div className="notice">Панель дня недоступна: {day.error}</div>;
  if (accounts.length === 0) return null;
  return (
    <div style={{ padding: "6px 12px 0" }}>
      <div className="panel-head" style={{ padding: "0 0 4px" }}>
        Панель дня
      </div>
      {watch.data && <WatchLine w={watch.data} />}
      {(day.data?.problem_sources ?? []).map((p) => (
        <div className="error" key={`${p.market}${p.symbol}`} style={{ marginBottom: 6 }}>
          Источник {p.symbol}: {p.status === "stale" ? "данные не обновляются, сигналы по нему приостановлены" : "ошибка"}
          {p.message ? ` (${p.message})` : ""}
        </div>
      ))}
      {accounts.map((a) => (
        <AccountCard key={a.market} a={a} />
      ))}
      <p className="caveat" style={{ margin: "2px 0 6px" }}>
        {day.data?.notes.join(" ")}
      </p>
    </div>
  );
}

const TRUTH_LABEL: Record<AccountSnapshot["truth"]["status"], string> = {
  ok: "цены свежие",
  partial: "цены устарели у части позиций",
  unmarked: "нет рыночных цен",
};

const ageText = (sec: number | null) =>
  sec == null ? "" : sec < 90 ? "только что" : sec < 5400 ? `${Math.round(sec / 60)} мин назад` : `${Math.round(sec / 3600)} ч назад`;

function AccountCard({ a }: { a: AccountSnapshot }) {
  const level = useLevel();
  const trader = atLeast(level, "trader");
  const money = (v: number | null) => (v == null ? "—" : `${fmtNum(v)} ${a.currency}`);
  return (
    <div className="card">
      <div className="row">
        <b>{a.name}</b>
        <span className="muted">{a.currency}</span>
      </div>
      <dl className="kv num">
        {trader && (
          <>
            <dt>Капитал</dt>
            <dd>
              {money(a.equity)}
              {!a.equity_complete && <span className="muted"> (оценка неполная)</span>}
            </dd>
          </>
        )}
        <dt>Нереализованный результат</dt>
        <dd className={pnlClass(a.unrealized_total)}>{money(a.unrealized_total)}</dd>
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
                <td className={`r ${pnlClass(p.unrealized_pnl)}`}>
                  {p.unrealized_pnl == null ? "нет цены" : fmtNum(p.unrealized_pnl)}
                  {p.mark_status === "stale" && <span className="muted" title="Цена устарела"> ⏱</span>}
                </td>
                <td className="r muted">стоп {p.stop == null ? "не записан" : fmtNum(p.stop)}</td>
                <td className="r">{p.risk == null ? "риск не ограничен" : `риск ${fmtNum(p.risk)}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {trader && (
        <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
          Данные: {TRUTH_LABEL[a.truth.status]}
          {a.positions[0]?.mark_age_s != null ? ` · ответ источника ${ageText(a.positions[0].mark_age_s)}` : ""}
          {" · "}журнал: {a.truth.sources.journal.entries} зап.
          {" · "}сверка с брокером: {a.truth.reconciliation.status === "not_connected" ? "не подключена" : a.truth.reconciliation.status === "match" ? "совпадает" : "расхождения"}
        </div>
      )}
      {a.warnings.map((w) => (
        <div className="notice" key={w} style={{ marginTop: 6 }}>
          {w}
        </div>
      ))}
    </div>
  );
}

const hhmm = (sec: number | null) =>
  sec == null ? "—" : new Date(sec * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

function WatchLine({ w }: { w: WatchStatus }) {
  const paused = w.instruments.filter((i) => i.paused).length;
  return (
    <div className="card">
      <div className="row">
        <b>Наблюдение</b>
        <span className={w.background_scanner ? "up" : "down"}>{w.background_scanner ? "идёт" : "не идёт"}</span>
      </div>
      <div className="muted num">
        Каждые {w.interval_min} мин · последняя проверка {hhmm(w.last_scan_at)} · следующая {hhmm(w.next_scan_at)}
        {paused > 0 ? ` · на паузе: ${paused}` : ""}
      </div>
      {w.quiet_hours.enabled && (
        <div className="muted">
          Тихие часы {w.quiet_hours.from}–{w.quiet_hours.to}
          {w.quiet_hours.active_now ? ": сейчас действуют, уведомления откладываются" : ""}
        </div>
      )}
      {w.warnings.map((x) => (
        <div className="notice" key={x} style={{ marginTop: 4 }}>
          {x}
        </div>
      ))}
      <p className="caveat" style={{ margin: "4px 0 0" }}>{w.notes[0]}</p>
    </div>
  );
}
