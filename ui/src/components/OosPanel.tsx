import { useState } from "react";
import { api } from "../api";
import { fmtDate, fmtNum, fmtPct, pnlClass } from "../lib/format";
import type { BacktestRequestBody, OosResponse, OosSegment, Strategy } from "../types";

interface Props {
  strategy: Strategy;
  request: BacktestRequestBody; // текущие настройки вкладки «Проверка»
}

const VERDICT: Record<OosResponse["verdict"]["status"], { title: string; cls: string }> = {
  held: { title: "Результат сохранился на проверочном периоде", cls: "notice" },
  degraded: { title: "Результат НЕ сохранился на проверочном периоде", cls: "error" },
  inconclusive: { title: "Вывод сделать нельзя", cls: "notice" },
};

/** Значения по умолчанию для перебора: от половины до двух значений параметра в допустимых границах. */
function suggest(strategy: Strategy): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of strategy.params) {
    const raw = [0.5, 1, 1.5, 2].map((k) => Math.round(p.default * k));
    const vals = [...new Set(raw.map((v) => Math.min(p.max, Math.max(p.min, v))))];
    out[p.name] = vals.join(", ");
  }
  return out;
}

export function OosPanel({ strategy, request }: Props) {
  const [open, setOpen] = useState(false);
  const [trainPct, setTrainPct] = useState(70);
  const [optimize, setOptimize] = useState(false);
  const [grid, setGrid] = useState<Record<string, string>>(() => suggest(strategy));
  const [res, setRes] = useState<OosResponse>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const parsed: Record<string, number[]> = {};
      if (optimize) {
        for (const p of strategy.params) {
          const nums = (grid[p.name] ?? "")
            .split(/[,\s;]+/)
            .filter(Boolean)
            .map(Number);
          if (nums.length === 0 || nums.some((n) => !Number.isInteger(n))) {
            throw new Error(`Параметр «${p.label}»: перечислите целые числа через запятую`);
          }
          parsed[p.name] = nums;
        }
      }
      setRes(await api.validate({ ...request, train_pct: trainPct, grid: parsed }));
    } catch (e) {
      setRes(undefined);
      setError(e instanceof Error ? e.message : "Ошибка проверки");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ borderTop: "1px solid var(--border)", marginTop: 8 }}>
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Проверка на отдельном периоде
        <button className="btn small" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Скрыть" : "Открыть"}
        </button>
      </div>
      {!open && (
        <p className="caveat" style={{ marginTop: 0 }}>
          Единственный способ понять, не подогнан ли результат под историю: подобрать параметры на первой части
          и посмотреть, что вышло на второй, которую подбор не видел.
        </p>
      )}
      {open && (
        <>
          <div className="form-grid" style={{ paddingTop: 6 }}>
            <label className="field">
              <span>Доля истории на подбор</span>
              <select value={trainPct} onChange={(e) => setTrainPct(Number(e.target.value))}>
                {[60, 70, 80].map((n) => (
                  <option key={n} value={n}>
                    {n}% подбор / {100 - n}% проверка
                  </option>
                ))}
              </select>
            </label>
            <label className="field full" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={optimize} onChange={(e) => setOptimize(e.target.checked)} />
              <span>Подобрать параметры перебором (иначе проверяются текущие)</span>
            </label>
            {optimize &&
              strategy.params.map((p) => (
                <label className="field full" key={p.name}>
                  <span>
                    {p.label} — значения через запятую ({p.min}–{p.max})
                  </span>
                  <input value={grid[p.name] ?? ""} onChange={(e) => setGrid({ ...grid, [p.name]: e.target.value })} />
                </label>
              ))}
            <button className="btn primary full" disabled={busy} onClick={run}>
              {busy ? "Считаем…" : optimize ? "Подобрать и проверить" : "Проверить на отдельном периоде"}
            </button>
          </div>
          {error && <div className="error">{error}</div>}
          {res && <OosResult res={res} />}
        </>
      )}
    </div>
  );
}

function Row({ label, a, b, cls = false }: { label: string; a: string; b: string; cls?: boolean }) {
  return (
    <tr>
      <td>{label}</td>
      <td className={`r ${cls ? pnlClass(parseFloat(a)) : ""}`}>{a}</td>
      <td className={`r ${cls ? pnlClass(parseFloat(b)) : ""}`}>{b}</td>
    </tr>
  );
}

const seg = (s: OosSegment | undefined) => ({
  ret: s ? fmtPct(s.total_return_pct) : "—",
  dd: s ? fmtPct(s.max_drawdown_pct, 2, false) : "—",
  n: s ? String(s.trades) : "—",
  bh: s ? fmtPct(s.buy_hold_return_pct) : "—",
  wr: s?.win_rate_pct == null ? "—" : fmtPct(s.win_rate_pct, 1, false),
});

function OosResult({ res }: { res: OosResponse }) {
  const v = VERDICT[res.verdict.status];
  const c = res.chosen;
  const tr = seg(c?.train);
  const te = seg(c?.test);
  const s = res.split;
  return (
    <>
      {res.trials.warning && <div className="error">{res.trials.warning}</div>}
      {res.warnings.map((w) => (
        <div className="notice" key={w}>
          {w}
        </div>
      ))}
      <div className={v.cls} role="status">
        <b>{v.title}.</b> {res.verdict.reasons.join(" ")}
      </div>
      <p className="caveat" style={{ marginTop: 6 }}>
        Подбор: {fmtDate(s.train_from)}–{fmtDate(s.train_to)} ({s.train_candles} свечей). Проверка:{" "}
        {fmtDate(s.test_from)}–{fmtDate(s.test_to)} ({s.test_candles} свечей). Вариантов перебрано в этом запуске:{" "}
        {res.optimization.variants}; всего по этому правилу на этом инструменте: {res.trials.total_variants}.
      </p>
      {c && (
        <>
          <p className="num" style={{ margin: "4px 12px" }}>
            {res.optimization.enabled ? "Выбрано на подборе" : "Проверяется"}:{" "}
            <b>{Object.entries(c.params).map(([k, x]) => `${k}=${x}`).join(", ")}</b>
          </p>
          <table className="num">
            <thead>
              <tr>
                <th />
                <th className="r">Подбор</th>
                <th className="r">Проверка</th>
              </tr>
            </thead>
            <tbody>
              <Row label="Доходность" a={tr.ret} b={te.ret} cls />
              <Row label="«Купил и держи»" a={tr.bh} b={te.bh} cls />
              <Row label="Макс. просадка" a={tr.dd} b={te.dd} />
              <Row label="Сделок" a={tr.n} b={te.n} />
              <Row label="Доля прибыльных" a={tr.wr} b={te.wr} />
            </tbody>
          </table>
        </>
      )}
      {res.baseline && (
        <p className="caveat">
          Без подбора ({Object.entries(res.baseline.params).map(([k, x]) => `${k}=${x}`).join(", ")}) на проверке:{" "}
          <b>{fmtPct(res.baseline.test.total_return_pct)}</b>, сделок {res.baseline.test.trades}.
        </p>
      )}
      {res.sensitivity.length > 0 && (
        <>
          <div className="panel-head" style={{ paddingBottom: 2 }}>
            Соседние значения параметров (на проверке)
          </div>
          <table className="num">
            <tbody>
              {res.sensitivity.map((x) => (
                <tr key={`${x.param}${x.value}`}>
                  <td>
                    {x.param}={x.value}
                  </td>
                  <td className={`r ${pnlClass(x.test_return_pct)}`}>{fmtPct(x.test_return_pct)}</td>
                  <td className="r muted">{x.test_trades} сд.</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="caveat" style={{ marginTop: 0 }}>
            Если соседние значения резко хуже выбранного, результат держится на одной точке и, скорее всего, случаен.
          </p>
        </>
      )}
      {res.cost_sensitivity.length > 0 && (
        <p className="caveat num">
          Если расходы (комиссия и проскальзывание) выше в 1/2/3 раза, доходность на проверке:{" "}
          {res.cost_sensitivity.map((x) => fmtPct(x.return_pct)).join(" / ")}.
        </p>
      )}
      {res.optimization.enabled && res.optimization.top.length > 0 && (
        <details style={{ padding: "0 12px" }}>
          <summary>Лучшие варианты на периоде подбора</summary>
          <table className="num">
            <tbody>
              {res.optimization.top.map((t) => (
                <tr key={JSON.stringify(t.params)}>
                  <td>{Object.entries(t.params).map(([k, x]) => `${k}=${x}`).join(", ")}</td>
                  <td className="r">{fmtNum(t.score)}</td>
                  <td className="r">{fmtPct(t.return_pct)}</td>
                  <td className="r muted">{t.trades} сд.</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <ul className="caveat" style={{ paddingLeft: 18 }}>
        {res.notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
        <li>Выбор по «доходности к просадке» на периоде подбора среди вариантов не менее чем с 10 сделками.</li>
      </ul>
    </>
  );
}
