import { useEffect, useState } from "react";
import { api } from "../api";
import { fmtDate, fmtNum, fmtPct, fmtPrice, pnlClass } from "../lib/format";
import type { BacktestRequestBody, BacktestResponse, Instrument, Settings, Strategy, Trade } from "../types";
import { EquityChart } from "./EquityChart";
import { OosPanel } from "./OosPanel";

interface Props {
  instrument: Instrument | undefined;
  tf: string;
  strategies: Strategy[];
  settings: Settings | undefined;
  theme: string;
  onStrategiesSaved: () => void;
}

export function BacktestPanel({ instrument, tf, strategies, settings, theme, onStrategiesSaved }: Props) {
  const [sid, setSid] = useState("");
  const [params, setParams] = useState<Record<string, number>>({});
  const [capital, setCapital] = useState(100_000);
  const [depth, setDepth] = useState(1000);
  const [useStop, setUseStop] = useState(false);
  const [stopMult, setStopMult] = useState("2");
  const [targetR, setTargetR] = useState("");
  const [riskPct, setRiskPct] = useState("1");
  const [fee, setFee] = useState(0.05);
  const [slip, setSlip] = useState(0.05);
  const [closeEod, setCloseEod] = useState(false);
  const intraday = INTRADAY.includes(tf);
  const [res, setRes] = useState<BacktestResponse>();
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [savedNote, setSavedNote] = useState<string>();

  const strat = strategies.find((s) => s.id === sid) ?? strategies[0];

  useEffect(() => {
    if (!sid && strategies[0]) setSid(strategies[0].id);
  }, [strategies, sid]);

  useEffect(() => {
    if (settings) setCapital(settings.capital);
  }, [settings]);

  // комиссия по умолчанию: акции у брокера ~0,05%, крипто-спот ~0,1%
  useEffect(() => {
    if (instrument) setFee(instrument.market === "moex" ? 0.05 : 0.1);
  }, [instrument?.market]); // eslint-disable-line react-hooks/exhaustive-deps

  // результат относится к конкретной связке — при смене тикера/стратегии/таймфрейма он устарел
  useEffect(() => {
    setRes(undefined);
    setError(undefined);
  }, [instrument?.symbol, instrument?.market, tf, sid]);

  const profileKey = instrument ? `${instrument.market}|${instrument.symbol}` : "";
  const savedProfile = settings?.instrument_strategies?.[profileKey];

  useEffect(() => {
    setParams({});
    setSavedNote(undefined);
    if (!savedProfile?.strategy && strategies[0]) setSid(strategies[0].id);
    // Сброс только при смене тикера.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument?.market, instrument?.symbol]);

  useEffect(() => {
    if (!savedProfile?.strategy) return;
    setSid(savedProfile.strategy);
    setParams(savedProfile.params);
  }, [profileKey, savedProfile]);

  if (!instrument || !strat) {
    return <div className="empty">Выберите тикер, чтобы проверить на нём стратегию.</div>;
  }

  const requestBody = (): BacktestRequestBody => ({
    market: instrument.market,
    symbol: instrument.symbol,
    tf,
    strategy: strat.id,
    params,
    limit: depth,
    capital,
    fee_pct: fee,
    slippage_pct: slip,
    ...(closeEod && intraday ? { close_eod: true } : {}),
    ...(useStop
      ? {
          stop_atr_mult: Number(stopMult),
          target_r: targetR ? Number(targetR) : undefined,
          risk_pct: riskPct ? Number(riskPct) : undefined,
        }
      : {}),
  });

  const run = async () => {
    setBusy(true);
    setError(undefined);
    try {
      setRes(
        await api.backtest({
          market: instrument.market,
          symbol: instrument.symbol,
          tf,
          strategy: strat.id,
          params,
          limit: depth,
          capital,
          fee_pct: fee,
          slippage_pct: slip,
          ...(useStop
            ? {
                stop_atr_mult: Number(stopMult),
                target_r: targetR ? Number(targetR) : undefined,
                risk_pct: riskPct ? Number(riskPct) : undefined,
              }
            : {}),
        }),
      );
    } catch (e) {
      setRes(undefined);
      setError(e instanceof Error ? e.message : "Ошибка бэктеста");
    } finally {
      setBusy(false);
    }
  };

  const m = res?.metrics;
  const beat = m ? m.total_return_pct - m.buy_hold_return_pct : 0;

  return (
    <div className="scroll">
      <div className="form-grid" style={{ paddingTop: 10 }}>
        <label className="field full">
          <span>Стратегия</span>
          <select
            value={strat.id}
            onChange={(e) => {
              setSid(e.target.value);
              setParams({});
              setSavedNote(undefined);
            }}
          >
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <p className="muted full" style={{ margin: 0, fontSize: 12 }}>
          {strat.description}
        </p>
        {strat.params.map((p) => (
          <label className="field" key={p.name}>
            <span>{p.label}</span>
            <input
              type="number"
              min={p.min}
              max={p.max}
              step={1}
              value={params[p.name] ?? p.default}
              onChange={(e) => setParams({ ...params, [p.name]: Math.round(Number(e.target.value)) })}
            />
          </label>
        ))}
        <label className="field">
          <span>Стартовый капитал</span>
          <input type="number" min={1} value={capital} onChange={(e) => setCapital(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Комиссия, % за сделку</span>
          <input type="number" min={0} step={0.01} value={fee} onChange={(e) => setFee(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Проскальзывание, %</span>
          <input type="number" min={0} step={0.01} value={slip} onChange={(e) => setSlip(Number(e.target.value))} />
        </label>
        <p className="muted full" style={{ margin: 0, fontSize: 12 }}>
          {savedProfile
            ? "Поиск сигналов по этому тикеру использует эту стратегию и эти параметры."
            : "Поиск сигналов по этому тикеру проверяет все стратегии с параметрами по умолчанию."}
        </p>
        <button
          className="btn full"
          disabled={busy}
          onClick={async () => {
            if (!settings) return;
            setBusy(true);
            setError(undefined);
            try {
              const next = { ...settings.instrument_strategies };
              next[profileKey] = { strategy: strat.id, params };
              await api.saveSettings({ instrument_strategies: next });
              setSavedNote("Сохранено: сканер будет искать сигналы по этим настройкам.");
              onStrategiesSaved();
            } catch (e) {
              setError(e instanceof Error ? e.message : "Не удалось сохранить настройки");
            } finally {
              setBusy(false);
            }
          }}
        >
          Искать сигналы по этим настройкам
        </button>
        {savedProfile && (
          <button
            className="btn ghost full"
            disabled={busy}
            onClick={async () => {
              if (!settings) return;
              setBusy(true);
              setError(undefined);
              try {
                const next = { ...settings.instrument_strategies };
                delete next[profileKey];
                await api.saveSettings({ instrument_strategies: next });
                setSavedNote("Сканер снова проверяет все стратегии с параметрами по умолчанию.");
                onStrategiesSaved();
              } catch (e) {
                setError(e instanceof Error ? e.message : "Не удалось сохранить настройки");
              } finally {
                setBusy(false);
              }
            }}
          >
            Снова проверять все стратегии
          </button>
        )}
        {savedNote && <p className="notice full">{savedNote}</p>}
        {intraday && (
          <>
            <label className="field full" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
              <input type="checkbox" checked={closeEod} onChange={(e) => setCloseEod(e.target.checked)} />
              <span>Закрывать позицию к концу дня, не держать через ночь</span>
            </label>
            <p className="notice full">
              Внутридневная проверка: расходы и проскальзывание решают всё, а в свечах не видны спред и очередь заявок.
              Комиссия и слипаж здесь стоит проверить с запасом. История на минутных свечах — это дни, а не годы.
            </p>
          </>
        )}
        <label className="field full" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={useStop} onChange={(e) => setUseStop(e.target.checked)} />
          <span>Со стопом и размером по риску (иначе весь капитал, выход по сигналу)</span>
        </label>
        {useStop && (
          <>
            <label className="field">
              <span>Стоп (× ATR)</span>
              <input type="number" min={0.1} max={20} step="any" value={stopMult} onChange={(e) => setStopMult(e.target.value)} />
            </label>
            <label className="field">
              <span>Цель (× риск, необязательно)</span>
              <input type="number" min={0.1} max={50} step="any" value={targetR} onChange={(e) => setTargetR(e.target.value)} />
            </label>
            <label className="field">
              <span>Риск на сделку, %</span>
              <input type="number" min={0.01} max={100} step="any" value={riskPct} onChange={(e) => setRiskPct(e.target.value)} />
            </label>
          </>
        )}
        <label className="field full">
          <span>Глубина истории (свечей)</span>
          <select value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
            {(intraday ? [1000, 5000, 10000, 20000] : [500, 1000, 2500, 5000]).map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
        </label>
        <button className="btn primary full" disabled={busy} onClick={run}>
          {busy ? "Считаем…" : `Проверить на ${instrument.symbol} (${tf})`}
        </button>
      </div>

      {error && <div className="error">{error}</div>}
      <OosPanel key={`${instrument.market}:${instrument.symbol}:${strat.id}`} strategy={strat} request={requestBody()} />
      {res?.stale && <div className="notice">Источник данных недоступен — расчёт по сохранённым данным.</div>}
      {res && res.data_quality.status !== "ok" && (
        <div className={res.data_quality.status === "error" ? "error" : "notice"}>
          {res.data_quality.status === "error" ? "В данных есть ошибки — результату верить нельзя. " : "Проверка данных: "}
          {res.data_quality.issues.map((i) => i.message).join(" ")}
        </div>
      )}

      {res && (
        <div className="notice">
          История: {res.coverage.candles} свечей, {fmtDate(res.coverage.first_ts)}–{fmtDate(res.coverage.last_ts)}.{" "}
          {res.coverage.exhausted
            ? "Это всё, что есть у источника."
            : "Запрошенная глубина набрана целиком: более ранняя история может существовать, выберите глубже."}
        </div>
      )}
      {res?.intraday && <IntradayBlock ib={res.intraday} />}
      {res?.warnings.map((w) => (
        <div key={w} className="notice">{w}</div>
      ))}

      {m && (
        <>
          <div className="metrics num">
            <Metric label="Доходность стратегии" value={fmtPct(m.total_return_pct)} cls={pnlClass(m.total_return_pct)} />
            <Metric label="«Купил и держи»" value={fmtPct(m.buy_hold_return_pct)} cls={pnlClass(m.buy_hold_return_pct)} />
            <Metric label="Макс. просадка" value={fmtPct(m.max_drawdown_pct, 2, false)} cls="down" />
            <Metric label="Сделок закрыто" value={String(m.trades) + (m.open_trade ? " + 1 открытая" : "")} />
            <Metric label="Доля прибыльных" value={m.win_rate_pct == null ? "—" : fmtPct(m.win_rate_pct, 1, false)} />
            <Metric label="Средняя сделка" value={fmtPct(m.avg_trade_pct)} cls={pnlClass(m.avg_trade_pct)} />
            <Metric label="Профит-фактор" value={m.profit_factor == null ? "—" : fmtNum(m.profit_factor)} />
            <Metric label="Время в рынке" value={fmtPct(m.exposure_pct, 1, false)} />
            <Metric label="Sharpe" value={m.sharpe == null ? "—" : fmtNum(m.sharpe)} />
            <Metric label="Sortino" value={m.sortino == null ? "—" : fmtNum(m.sortino)} />
            <Metric label="Calmar" value={m.calmar == null ? "—" : fmtNum(m.calmar)} />
            <Metric label="Серия убытков подряд" value={String(m.max_consecutive_losses)} />
            {res!.run_card.rules && (
              <>
                <Metric label="Средняя сделка, R" value={m.avg_r == null ? "—" : fmtNum(m.avg_r)} cls={pnlClass(m.avg_r)} />
                <Metric label="Выходов по стопу / цели" value={`${m.stops} / ${m.targets}`} />
                <Metric label="Худший ход против позиции" value={m.avg_mae_pct == null ? "—" : fmtPct(m.avg_mae_pct, 2, false)} />
                <Metric label="Лучший ход в пользу" value={m.avg_mfe_pct == null ? "—" : fmtPct(m.avg_mfe_pct, 2, false)} />
              </>
            )}
          </div>
          <p className="caveat" style={{ marginBottom: 0 }}>
            {beat >= 0 ? "Стратегия обошла" : "Стратегия уступила"} «купил и держи» на{" "}
            <b>{fmtPct(Math.abs(beat), 1, false)}</b> за {m.candles} свечей.
          </p>
          <EquityChart points={res!.equity} theme={theme} />
          <Robustness res={res!} />

          <div className="panel-head" style={{ paddingBottom: 2 }}>
            Последние сделки
          </div>
          <table className="num">
            <thead>
              <tr>
                <th>Вход</th>
                <th className="r">Цена</th>
                <th>Выход</th>
                <th className="r">Цена</th>
                <th className="r">Итог</th>
                <th>Как вышли</th>
              </tr>
            </thead>
            <tbody>
              {res!.trades
                .slice(-15)
                .reverse()
                .map((t) => (
                  <tr key={t.entry_ts}>
                    <td>{fmtDate(t.entry_ts)}</td>
                    <td className="r">{fmtPrice(t.entry_price)}</td>
                    <td>{t.exit_ts ? fmtDate(t.exit_ts) : <span className="muted">открыта</span>}</td>
                    <td className="r">{fmtPrice(t.exit_price)}</td>
                    <td className={`r ${pnlClass(t.pnl_pct)}`}>{fmtPct(t.pnl_pct * 100)}</td>
                    <td className="muted">{EXIT_LABEL[t.exit_reason]}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
      <p className="caveat">
        В этом расчёте комиссия {fee}% и проскальзывание {slip}% на каждую сторону сделки. Проверка на
        истории показывает, как правило работало раньше, и не гарантирует будущий результат.
      </p>
      {res && (
        <ul className="caveat" style={{ paddingLeft: 18 }}>
          {res.run_card.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const INTRADAY = ["1m", "5m", "10m", "15m"];

const EXIT_LABEL: Record<Trade["exit_reason"], string> = {
  eod: "конец дня",
  signal: "по сигналу",
  stop: "стоп",
  gap_stop: "стоп с гэпом",
  target: "цель",
  gap_target: "цель с гэпом",
  open: "открыта",
};

function Metric({ label, value, cls = "" }: { label: string; value: string; cls?: string }) {
  return (
    <div className="metric">
      <div className="lbl">{label}</div>
      <div className={`val ${cls}`}>{value}</div>
    </div>
  );
}

function Robustness({ res }: { res: BacktestResponse }) {
  const wf = res.validation.walk_forward;
  const rs = res.validation.resampling;
  const card = res.run_card;
  return (
    <>
      <div className="panel-head" style={{ paddingBottom: 2 }}>
        Устойчивость результата
      </div>
      {wf ? (
        <>
          <table className="num">
            <thead>
              <tr>
                <th>Период</th>
                <th className="r">Стратегия</th>
                <th className="r">Держать</th>
                <th className="r">Просадка</th>
                <th className="r">Сд.</th>
              </tr>
            </thead>
            <tbody>
              {wf.windows.map((w) => (
                <tr key={w.start}>
                  <td>
                    {fmtDate(w.start)}–{fmtDate(w.end)}
                  </td>
                  <td className={`r ${pnlClass(w.return_pct)}`}>{fmtPct(w.return_pct)}</td>
                  <td className={`r ${pnlClass(w.buy_hold_pct)}`}>{fmtPct(w.buy_hold_pct)}</td>
                  <td className="r down">{fmtPct(w.max_drawdown_pct, 2, false)}</td>
                  <td className="r">{w.trades}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="caveat" style={{ marginBottom: 0 }}>
            Прибыльных периодов: <b>{wf.profitable_windows} из {wf.n_windows}</b>. Если прибыль только в одном
            окне, результат держится на одной удачной полосе рынка.
          </p>
        </>
      ) : (
        <p className="caveat">Слишком мало свечей, чтобы разбить историю на периоды.</p>
      )}
      {rs ? (
        <p className="caveat" style={{ marginBottom: 0 }}>
          Ресэмплинг {rs.trades} сделок ({rs.simulations} прогонов): в прибыли{" "}
          <b>{fmtPct(rs.profitable_share_pct, 0, false)}</b> прогонов; типичный итог{" "}
          {fmtPct(rs.return_p50_pct)}, плохой сценарий (5%) {fmtPct(rs.return_p5_pct)}, при неудачном порядке
          сделок просадка по сделкам может дойти до {fmtPct(rs.drawdown_shuffled_p95_pct, 1, false)} (в плитках выше
          просадка считается по кривой капитала, поэтому числа отличаются).
        </p>
      ) : (
        <p className="caveat">Сделок меньше пяти — ресэмплинг ничего не покажет.</p>
      )}
      <p className="caveat muted">
        Sharpe/Sortino/Calmar — при безрисковой ставке 0; на истории короче двух месяцев не считаются. Расчёт:
        версия движка {card.engine_version}, {card.candles} свечей, отпечаток данных {card.data_hash}.
      </p>
    </>
  );
}

function IntradayBlock({ ib }: { ib: NonNullable<BacktestResponse["intraday"]> }) {
  return (
    <div className="card" style={{ margin: "6px 12px" }}>
      <b>Внутридневной режим</b>
      <div className="muted num">
        Торговых дней в истории: {ib.trading_days}
        {ib.trades_per_day != null ? ` · сделок в день: ${ib.trades_per_day}` : ""} · расходы на круг сделки:{" "}
        {fmtNum(ib.round_trip_cost_pct, 2)}%
      </div>
      <table className="num">
        <thead>
          <tr>
            <th>Расходы</th>
            <th className="r">Доходность</th>
            <th className="r">Сделок</th>
          </tr>
        </thead>
        <tbody>
          {ib.cost_stress.map((x) => (
            <tr key={x.multiplier}>
              <td>
                ×{x.multiplier} (комиссия {fmtNum(x.fee_pct, 3)}%, слипаж {fmtNum(x.slippage_pct, 3)}%)
              </td>
              <td className={`r ${pnlClass(x.return_pct)}`}>{fmtPct(x.return_pct)}</td>
              <td className="r">{x.trades}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
