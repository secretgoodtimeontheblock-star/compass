import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtDate, fmtNum, fmtPct, pnlClass } from "../lib/format";
import { atLeast, useLevel } from "../lib/experience";
import { parseGrid, suggestGrid } from "../lib/grid";
import type { BacktestRequestBody, LabCell, LabFinding, LabResponse, Strategy } from "../types";
import { Learn } from "./Learn";

interface Props {
  strategy: Strategy;
  request: BacktestRequestBody;
}

const STATUS: Record<LabResponse["verdict"]["status"], { cls: string; mark: string }> = {
  robust: { cls: "notice", mark: "✓" },
  mixed: { cls: "notice", mark: "≈" },
  fragile: { cls: "error", mark: "✕" },
  insufficient: { cls: "notice", mark: "?" },
};
const SEVERITY_MARK: Record<LabFinding["severity"], string> = { good: "✓", warn: "!", bad: "✕" };

/**
 * Лаборатория проверки. Новичку — одна кнопка и вывод простым языком («недостаточно данных», «результат
 * нестабилен», «пережил непросмотренные данные»…). Исследователю — ещё и окна walk-forward, карта параметров,
 * устойчивость, поправки на множественные проверки и расходы.
 */
export function LabPanel({ strategy, request }: Props) {
  const level = useLevel();
  const researcher = atLeast(level, "researcher");
  const [open, setOpen] = useState(true);
  const [optimize, setOptimize] = useState(false);
  const [folds, setFolds] = useState(4);
  const [mode, setMode] = useState<"rolling" | "anchored">("rolling");
  const [grid, setGrid] = useState<Record<string, string>>(() => suggestGrid(strategy));
  const [res, setRes] = useState<LabResponse>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const contextKey = JSON.stringify([request, optimize, folds, mode, grid]);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++;
    setRes(undefined);
    setError(undefined);
    setBusy(false);
    return () => { generation.current++; };
  }, [contextKey]);

  const run = async () => {
    const id = ++generation.current;
    setBusy(true);
    setError(undefined);
    try {
      // в сетке не больше двух параметров: так карта остаётся читаемой
      const names = optimize ? strategy.params.slice(0, 2).map((p) => p.name) : [];
      const result = await api.lab({ ...request, grid: parseGrid(strategy, grid, names), folds, mode });
      if (id === generation.current) setRes(result);
    } catch (e) {
      if (id !== generation.current) return;
      setRes(undefined);
      setError(e instanceof Error ? e.message : "Ошибка проверки");
    } finally {
      if (id === generation.current) setBusy(false);
    }
  };

  return (
    <div style={{ borderTop: "1px solid var(--border)", marginTop: 8 }} data-testid="lab">
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Насколько результату можно верить
        <button className="btn small" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? "Свернуть" : "Открыть"}
        </button>
      </div>
      {open && (
        <div style={{ padding: "0 12px 8px" }}>
          <p className="caveat" style={{ marginTop: 0 }}>
            {researcher
              ? "Параметры подбираются только на данных до окна и проверяются на следующем; окон несколько, поэтому видно, повторяется ли результат."
              : "Проверка делит историю на несколько независимых окон и сообщает вывод простым языком. Это описание прошлого, а не прогноз."}
          </p>
          {researcher && (
            <div className="form-grid" style={{ padding: 0 }}>
              <label className="field">
                <span>Окон проверки</span>
                <input type="number" min={3} max={8} value={folds} onChange={(e) => setFolds(Math.round(Number(e.target.value)))} />
              </label>
              <label className="field">
                <span>Обучение</span>
                <select value={mode} onChange={(e) => setMode(e.target.value as "rolling" | "anchored")} disabled={!optimize}>
                  <option value="rolling">скользящее</option>
                  <option value="anchored">растущее</option>
                </select>
              </label>
              <label className="field full" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
                <input type="checkbox" checked={optimize} onChange={(e) => setOptimize(e.target.checked)} />
                <span>Подбирать параметры в каждом окне (первые два параметра правила)</span>
              </label>
              {optimize &&
                strategy.params.slice(0, 2).map((p) => (
                  <label className="field" key={p.name}>
                    <span>{p.label}: значения</span>
                    <input value={grid[p.name] ?? ""} onChange={(e) => setGrid({ ...grid, [p.name]: e.target.value })} />
                  </label>
                ))}
            </div>
          )}
          <button className="btn primary" disabled={busy} onClick={run} style={{ marginTop: 6 }}>
            {busy ? "Считаем…" : researcher ? "Запустить лабораторию" : "Проверить надёжность"}
          </button>
          {error && <div className="error" style={{ marginTop: 6 }}>{error}</div>}
          {res && <LabResult res={res} researcher={researcher} />}
        </div>
      )}
    </div>
  );
}

function LabResult({ res, researcher }: { res: LabResponse; researcher: boolean }) {
  const v = res.verdict;
  const st = STATUS[v.status];
  return (
    <div style={{ marginTop: 8 }}>
      <div className={st.cls} role="status">
        <b>{st.mark} {v.headline}</b>
      </div>
      {v.findings.map((f) => (
        <div key={f.code} className={f.severity === "bad" ? "error" : "notice"} style={{ marginTop: 4 }}>
          <b>{SEVERITY_MARK[f.severity]} {f.title}.</b> {f.text}
          <Learn ids={f.learn} />
        </div>
      ))}
      {res.warnings.map((w) => <div key={w} className="notice" style={{ marginTop: 4 }}>{w}</div>)}
      {res.trials.warning && <div className="notice" style={{ marginTop: 4 }}>{res.trials.warning}</div>}
      <p className="caveat">{v.disclaimer}</p>
      {researcher && <ResearchLayer res={res} />}
    </div>
  );
}

function cellStyle(c: LabCell) {
  if (!c.valid || c.return_pct == null) return { opacity: 0.4 };
  const g = Math.log(Math.max(1 + c.return_pct / 100, 0.01));
  const a = Math.min(0.55, Math.abs(g) * 0.9);
  return { background: g >= 0 ? `rgba(46,160,67,${a})` : `rgba(218,54,51,${a})`, opacity: c.thin ? 0.65 : 1 };
}

function ResearchLayer({ res }: { res: LabResponse }) {
  const wf = res.walk_forward;
  const pm = res.parameter_map;
  const rb = res.robustness;
  const mt = res.multiple_testing;
  return (
    <details open style={{ marginTop: 6 }}>
      <summary><b>Слой исследователя</b></summary>
      <div className="panel-head" style={{ paddingBottom: 2 }}>Окна проверки (walk-forward)</div>
      <table className="num" aria-label="Окна walk-forward">
        <thead>
          <tr><th>Окно проверки</th><th>Параметры</th><th className="r">Результат</th><th className="r">Держать</th><th className="r">Просадка</th><th className="r">Сд.</th></tr>
        </thead>
        <tbody>
          {wf.folds.map((f) => (
            <tr key={f.index}>
              <td>{fmtDate(f.test_from)}–{fmtDate(f.test_to)}</td>
              <td className="muted">{f.params ? Object.values(f.params).join("/") : "нет выбора"}</td>
              <td className={`r ${pnlClass(f.test_return_pct)}`}>{fmtPct(f.test_return_pct)}</td>
              <td className={`r ${pnlClass(f.buy_hold_pct)}`}>{fmtPct(f.buy_hold_pct)}</td>
              <td className="r down">{fmtPct(f.test_max_drawdown_pct, 2, false)}</td>
              <td className="r">{f.test_trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="caveat" style={{ marginBottom: 0 }}>
        В плюсе {wf.summary.profitable_folds} из {wf.summary.usable_folds} окон, суммарно {fmtPct(wf.summary.stitched_return_pct)}
        {wf.summary.param_consistency != null ? `; один и тот же набор параметров выбран в ${fmtPct(wf.summary.param_consistency * 100, 0, false)} окон` : ""}
        {wf.summary.efficiency != null ? `; эффективность (скорость роста вне выборки к скорости на подборе) ${fmtNum(wf.summary.efficiency)}` : ""}.
      </p>

      {pm.available && pm.matrix && pm.axes && pm.params && (
        <>
          <div className="panel-head" style={{ paddingBottom: 2 }}>Карта устойчивости параметров</div>
          <table className="num" aria-label="Карта параметров" style={{ textAlign: "center" }}>
            <thead>
              <tr>
                <th>{pm.params[0]}{pm.params[1] ? ` \\ ${pm.params[1]}` : ""}</th>
                {(pm.params[1] ? pm.axes[1] : [""]).map((b) => <th key={b}>{b}</th>)}
              </tr>
            </thead>
            <tbody>
              {pm.matrix.map((row, i) => (
                <tr key={pm.axes![0][i]}>
                  <th>{pm.axes![0][i]}</th>
                  {row.map((c, j) => (
                    <td key={j} style={cellStyle(c)} title={c.valid ? `сделок ${c.trades}` : "сочетание недопустимо"}>
                      {c.valid && c.return_pct != null ? fmtNum(c.return_pct, 0) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="caveat" style={{ marginBottom: 0 }}>
            Доходность на всей истории, %. {pm.stability?.text} Карта описывает устойчивость и не предназначена для выбора параметров.
          </p>
        </>
      )}

      <div className="panel-head" style={{ paddingBottom: 2 }}>Распределение результата</div>
      <dl className="kv num">
        {rb.bootstrap && (
          <>
            <dt>Средняя сделка (интервал 5–95%)</dt>
            <dd>{fmtPct(rb.bootstrap.mean_p5_pct)} … {fmtPct(rb.bootstrap.mean_p95_pct)} · шанс «нет преимущества» {fmtPct(rb.bootstrap.prob_no_edge_pct, 0, false)}</dd>
          </>
        )}
        {rb.concentration && (
          <>
            <dt>Три лучшие сделки дают</dt>
            <dd>{rb.concentration.top3_profit_share_pct == null ? "—" : fmtPct(rb.concentration.top3_profit_share_pct, 0, false)} прибыли; без лучшей — {fmtPct(rb.concentration.return_without_best_pct)}</dd>
          </>
        )}
        {rb.drawdown && (
          <>
            <dt>Просадка</dt>
            <dd>
              {fmtPct(rb.drawdown.max_drawdown_pct, 2, false)} за {fmtNum(rb.drawdown.peak_to_trough_days, 0)} дн.;{" "}
              {rb.drawdown.recovered ? `восстановлена за ${fmtNum(rb.drawdown.recovery_days ?? 0, 0)} дн.` : "не восстановлена"}; дольше всего под водой {fmtNum(rb.drawdown.longest_underwater_days, 0)} дн.
            </dd>
          </>
        )}
        {rb.tail && (
          <>
            <dt>Худшие 5% свечей</dt>
            <dd>порог {fmtPct(rb.tail.var95_bar_pct, 2, false)}, в среднем {rb.tail.cvar95_bar_pct == null ? "—" : fmtPct(rb.tail.cvar95_bar_pct, 2, false)} (CVaR), худшая {fmtPct(rb.tail.worst_bar_pct, 2, false)}</dd>
          </>
        )}
      </dl>
      {rb.regimes && (
        <table className="num" aria-label="Режимы рынка">
          <thead><tr><th>Режим</th><th className="r">Свечей</th><th className="r">Правило</th><th className="r">Держать</th></tr></thead>
          <tbody>
            {rb.regimes.regimes.map((r) => (
              <tr key={r.id}>
                <td>{r.label}{rb.regimes?.dominant === r.id ? " ★" : ""}</td>
                <td className="r">{r.bars}</td>
                <td className={`r ${pnlClass(r.return_pct)}`}>{r.thin ? "мало" : fmtPct(r.return_pct)}</td>
                <td className={`r ${pnlClass(r.buy_hold_pct)}`}>{r.thin ? "мало" : fmtPct(r.buy_hold_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rb.cost_stress.length > 0 && (
        <table className="num" aria-label="Стресс расходов">
          <thead><tr><th>Расходы (комиссия+слипаж+спред)</th><th className="r">Доходность</th><th className="r">Сделок</th></tr></thead>
          <tbody>
            {rb.cost_stress.map((x) => (
              <tr key={x.multiplier}>
                <td>×{x.multiplier} ({fmtNum(x.fee_pct, 3)}% + {fmtNum(x.slippage_pct, 3)}% + {fmtNum(x.spread_pct, 3)}%)</td>
                <td className={`r ${pnlClass(x.return_pct)}`}>{fmtPct(x.return_pct)}</td>
                <td className="r">{x.trades}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="panel-head" style={{ paddingBottom: 2 }}>Множественные проверки</div>
      <p className="caveat" style={{ margin: 0 }}>
        Вариантов в этом запуске {mt.variants_this_run}, раньше {mt.prior_variants}.{" "}
        {mt.pbo.available && mt.pbo.value != null ? `Вероятность переобучения подбора (PBO): ${fmtPct(mt.pbo.value * 100, 0, false)}. ` : "PBO: нужна сетка параметров. "}
        {mt.dsr.available && mt.dsr.value != null ? `Deflated Sharpe: ${fmtPct(mt.dsr.value * 100, 0, false)} (нужно ≥ 95%).` : ""}
      </p>
      {res.research && (
        <p className="caveat num">
          Вне выборки: Sharpe {res.research.sharpe == null ? "—" : fmtNum(res.research.sharpe)}, Sortino{" "}
          {res.research.sortino == null ? "—" : fmtNum(res.research.sortino)}, Calmar {res.research.calmar == null ? "—" : fmtNum(res.research.calmar)}.
        </p>
      )}
      {res.notes.map((n) => <p key={n} className="caveat" style={{ margin: "2px 0" }}>{n}</p>)}
    </details>
  );
}
