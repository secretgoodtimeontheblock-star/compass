import { useEffect, useState } from "react";
import { api } from "../api";
import type { AccountDto } from "../types";

type Draft = { name: string; capital: string; risk_pct: string; daily_loss_limit_pct: string; max_open_risk_pct: string };

const toDraft = (a: AccountDto): Draft => ({
  name: a.name,
  capital: a.capital == null ? "" : String(a.capital),
  risk_pct: String(a.risk_pct),
  daily_loss_limit_pct: String(a.daily_loss_limit_pct),
  max_open_risk_pct: String(a.max_open_risk_pct),
});

/** Счета по валютам: у рублей и USDT свой капитал и свои лимиты, общего итога нет. */
export function AccountsSection({ onSaved }: { onSaved?: () => void }) {
  const [accounts, setAccounts] = useState<AccountDto[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [error, setError] = useState<string>();
  const [note, setNote] = useState<string>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .accounts()
      .then((list) => {
        setAccounts(list);
        setDrafts(Object.fromEntries(list.map((a) => [a.market, toDraft(a)])));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить счета"));
  }, []);

  const set = (market: string, patch: Partial<Draft>) =>
    setDrafts((d) => ({ ...d, [market]: { ...d[market], ...patch } }));

  const save = async () => {
    setBusy(true);
    setError(undefined);
    setNote(undefined);
    try {
      for (const a of accounts) {
        const d = drafts[a.market];
        await api.updateAccount(a.market, {
          name: d.name,
          capital: d.capital.trim() === "" ? null : Number(d.capital),
          risk_pct: Number(d.risk_pct),
          daily_loss_limit_pct: Number(d.daily_loss_limit_pct),
          max_open_risk_pct: Number(d.max_open_risk_pct),
        });
      }
      setNote("Счета сохранены.");
      onSaved?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить счета");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h3 className="dialog-section">Счета и лимиты</h3>
      <p className="muted small-text">
        Рубли и USDT не складываются: у каждого счёта свой капитал и свои лимиты. Лимиты — предупреждения: Compass
        ничего не исполняет и не блокирует действия у брокера.
      </p>
      {accounts.map((a) => {
        const d = drafts[a.market];
        if (!d) return null;
        return (
          <fieldset key={a.market} className="form-grid" style={{ padding: 0, border: 0, marginBottom: 10 }}>
            <legend style={{ fontWeight: 600, marginBottom: 4 }}>
              {a.name} · {a.currency}
            </legend>
            <label className="field">
              <span>Капитал, {a.currency}</span>
              <input
                type="number"
                min={0}
                step="any"
                value={d.capital}
                placeholder="не задан"
                onChange={(e) => set(a.market, { capital: e.target.value })}
              />
            </label>
            <label className="field">
              <span>Риск на сделку, %</span>
              <input type="number" min={0.01} max={100} step="any" value={d.risk_pct} onChange={(e) => set(a.market, { risk_pct: e.target.value })} />
            </label>
            <label className="field">
              <span>Дневной лимит убытка, %</span>
              <input type="number" min={0.01} max={100} step="any" value={d.daily_loss_limit_pct} onChange={(e) => set(a.market, { daily_loss_limit_pct: e.target.value })} />
            </label>
            <label className="field">
              <span>Суммарный риск позиций, %</span>
              <input type="number" min={0.01} max={100} step="any" value={d.max_open_risk_pct} onChange={(e) => set(a.market, { max_open_risk_pct: e.target.value })} />
            </label>
          </fieldset>
        );
      })}
      {error && <div className="error">{error}</div>}
      {note && <div className="notice">{note}</div>}
      <button className="btn small" disabled={busy || accounts.length === 0} onClick={() => void save()}>
        {busy ? "Сохраняем…" : "Сохранить счета"}
      </button>
    </>
  );
}
