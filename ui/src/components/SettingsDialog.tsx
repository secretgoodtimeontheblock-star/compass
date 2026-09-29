import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Market, Settings } from "../types";

interface Props {
  settings: Settings;
  markets: Market[];
  onClose: () => void;
  onSaved: (s: Settings) => void;
}

export function SettingsDialog({ settings, markets, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState({
    capital: String(settings.capital),
    risk_pct: String(settings.risk_pct),
    scan_interval_min: String(settings.scan_interval_min),
    tf_moex: settings.tf_moex,
    tf_crypto: settings.tf_crypto,
  });
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const first = useRef<HTMLInputElement>(null);

  useEffect(() => {
    first.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const tfs = (id: string) => markets.find((m) => m.id === id)?.timeframes ?? [];

  const save = async () => {
    setBusy(true);
    setError(undefined);
    try {
      onSaved(
        await api.saveSettings({
          capital: Number(draft.capital),
          risk_pct: Number(draft.risk_pct),
          scan_interval_min: Number(draft.scan_interval_min),
          tf_moex: draft.tf_moex,
          tf_crypto: draft.tf_crypto,
        }),
      );
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-label="Настройки">
        <h2>Настройки</h2>
        <div className="form-grid" style={{ padding: 0 }}>
          <label className="field">
            <span>Капитал для расчёта позиции</span>
            <input
              ref={first}
              type="number"
              min={1}
              value={draft.capital}
              onChange={(e) => setDraft({ ...draft, capital: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Риск на сделку, % капитала</span>
            <input
              type="number"
              min={0.1}
              max={100}
              step={0.1}
              value={draft.risk_pct}
              onChange={(e) => setDraft({ ...draft, risk_pct: e.target.value })}
            />
          </label>
          <label className="field">
            <span>Таймфрейм сигналов: акции</span>
            <select value={draft.tf_moex} onChange={(e) => setDraft({ ...draft, tf_moex: e.target.value })}>
              {tfs("moex").map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Таймфрейм сигналов: крипта</span>
            <select value={draft.tf_crypto} onChange={(e) => setDraft({ ...draft, tf_crypto: e.target.value })}>
              {tfs("crypto").map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="field full">
            <span>Как часто искать сигналы, минут</span>
            <input
              type="number"
              min={1}
              max={1440}
              value={draft.scan_interval_min}
              onChange={(e) => setDraft({ ...draft, scan_interval_min: e.target.value })}
            />
          </label>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>
          Риск 1–2% на сделку — общепринятая осторожная планка: размер позиции подбирается так, чтобы срабатывание
          стопа стоило не больше этой доли капитала.
        </p>
        {error && <div className="error" style={{ margin: "8px 0 0" }}>{error}</div>}
        <div className="actions">
          <button className="btn" onClick={onClose}>
            Отмена
          </button>
          <button className="btn primary" disabled={busy} onClick={save}>
            Сохранить
          </button>
        </div>
      </div>
    </div>
  );
}
