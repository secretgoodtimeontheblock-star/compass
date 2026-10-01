import { useState } from "react";
import { api } from "../api";
import { fmtPrice } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { AlertDto, Instrument, LevelDto, Market } from "../types";
import { PriceChart } from "./PriceChart";

const OFF = { sma20: false, sma50: false, volume: false };

interface Props {
  instrument: Instrument;
  marketInfo: Market | undefined;
  tf: string;
  lastPrice: number | undefined;
  levels: LevelDto[];
  onLevelsChanged: () => void;
  theme: string;
}

/** Уровни пользователя и второй таймфрейм под основным графиком. */
export function ChartExtras({ instrument, marketInfo, tf, lastPrice, levels, onLevelsChanged, theme }: Props) {
  const [price, setPrice] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [removed, setRemoved] = useState<LevelDto>();
  const [second, setSecond] = useState(false);
  const [alertPrice, setAlertPrice] = useState("");
  const [alertNote, setAlertNote] = useState("");
  const [alertError, setAlertError] = useState<string>();
  const alertsApi = useApi(
    () => api.alerts({ status: "active", market: instrument.market, symbol: instrument.symbol }),
    [instrument.market, instrument.symbol],
    60_000,
  );
  const addAlert = async () => {
    setAlertError(undefined);
    try {
      await api.addAlert({ market: instrument.market, symbol: instrument.symbol, price: Number(alertPrice), note: alertNote });
      setAlertPrice("");
      setAlertNote("");
      alertsApi.reload();
    } catch (e) {
      setAlertError(e instanceof Error ? e.message : "Не удалось поставить оповещение");
    }
  };
  const cancelAlert = async (a: AlertDto) => {
    try {
      await api.cancelAlert(a.id);
      alertsApi.reload();
    } catch (e) {
      setAlertError(e instanceof Error ? e.message : "Не удалось отменить оповещение");
    }
  };
  const tfs = marketInfo?.timeframes ?? [];
  const [tf2, setTf2] = useState("");
  const alternatives = tfs.filter((t) => t !== tf);
  const secondTf = alternatives.includes(tf2) ? tf2 : (alternatives.find((t) => tfs.indexOf(t) > tfs.indexOf(tf)) ?? alternatives.at(-1) ?? tf);
  const candles2 = useApi(
    second ? () => api.candles(instrument.market, instrument.symbol, secondTf, 300) : null,
    [second, instrument.market, instrument.symbol, secondTf],
    60_000,
  );

  const add = async () => {
    if (busy) return;
    const p = Number(price || lastPrice || 0);
    setError(undefined);
    setBusy(true);
    try {
      await api.addLevel({ market: instrument.market, symbol: instrument.symbol, price: p, label });
      setPrice("");
      setLabel("");
      onLevelsChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить уровень");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (lv: LevelDto) => {
    try {
      await api.removeLevel(lv.id);
      setRemoved(lv);
      onLevelsChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить уровень");
    }
  };

  const restore = async () => {
    if (!removed) return;
    try {
      await api.restoreLevel(removed.id);
      setRemoved(undefined);
      onLevelsChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось вернуть уровень");
    }
  };

  return (
    <div className="chart-extras">
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span className="muted">Уровни:</span>
        {levels.map((lv) => (
          <span key={lv.id} className="badge num" title={lv.label || "уровень"}>
            {fmtPrice(lv.price)}
            {lv.label ? ` · ${lv.label}` : ""}
            <button className="icon-btn" aria-label={`Удалить уровень ${lv.price}`} onClick={() => void remove(lv)}>
              ×
            </button>
          </span>
        ))}
        <input
          type="number"
          min={0}
          step="any"
          value={price}
          placeholder={lastPrice ? String(lastPrice) : "цена"}
          aria-label="Цена уровня"
          style={{ width: 90 }}
          onChange={(e) => setPrice(e.target.value)}
        />
        <input type="text" value={label} maxLength={60} placeholder="подпись" aria-label="Подпись уровня" style={{ width: 120 }} onChange={(e) => setLabel(e.target.value)} />
        <button className="btn small" disabled={busy} onClick={() => void add()}>
          {busy ? "Сохраняем…" : "Добавить уровень"}
        </button>
        {removed && (
          <button className="btn ghost small" onClick={() => void restore()}>
            Вернуть {fmtPrice(removed.price)}
          </button>
        )}
        <span className="spacer" />
        <label style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={second} onChange={(e) => setSecond(e.target.checked)} />
          Второй таймфрейм
        </label>
        {second && (
          <select aria-label="Второй таймфрейм" value={secondTf} onChange={(e) => setTf2(e.target.value)}>
            {tfs.filter((t) => t !== tf).map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        )}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center", marginTop: 4 }} data-testid="alerts">
        <span className="muted">Оповестить, когда цена дойдёт до:</span>
        {(alertsApi.data ?? []).map((a) => (
          <span key={a.id} className="badge num" title={a.note || (a.kind === "above" ? "выше" : "ниже")}>
            {a.kind === "above" ? "↑" : "↓"} {fmtPrice(a.price)}
            {a.note ? ` · ${a.note}` : ""}
            <button className="icon-btn" aria-label={`Отменить оповещение ${a.price}`} onClick={() => void cancelAlert(a)}>×</button>
          </span>
        ))}
        <input type="number" min={0} step="any" value={alertPrice} placeholder="цена" aria-label="Цена оповещения" style={{ width: 90 }} onChange={(e) => setAlertPrice(e.target.value)} />
        <input type="text" value={alertNote} maxLength={80} placeholder="заметка" aria-label="Заметка оповещения" style={{ width: 120 }} onChange={(e) => setAlertNote(e.target.value)} />
        <button className="btn small" disabled={!alertPrice} onClick={() => void addAlert()}>Оповестить</button>
      </div>
      {alertError && <div className="error">{alertError}</div>}
      {error && <div className="error">{error}</div>}
      <p className="caveat" style={{ margin: "2px 0 0" }}>
        Оповещение придёт тостом и в Telegram (если настроен), пока приложение запущено; проверка раз в минуту. Уровни — ваши пометки на графике, приложение не оценивает, удержатся ли они.
      </p>
      {second && (
        <div style={{ position: "relative", height: 200, marginTop: 6 }}>
          {candles2.error ? (
            <div className="error">{candles2.error}</div>
          ) : (
            <PriceChart
              candles={candles2.data?.candles ?? []}
              tf={secondTf}
              signals={[]}
              trades={[]}
              overlays={OFF}
              theme={theme}
              resetKey={`${instrument.market}:${instrument.symbol}:${secondTf}:2`}
              lines={levels.map((l) => ({ price: l.price, label: l.label || "уровень", kind: "level" as const }))}
            />
          )}
        </div>
      )}
    </div>
  );
}
