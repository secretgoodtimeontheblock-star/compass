import { useMemo, useState } from "react";
import { api } from "../api";
import { fmtNum, fmtPct, fmtPrice, pnlClass, STRATEGY_SHORT } from "../lib/format";
import { useApi } from "../lib/use-api";
import type { Instrument, ScreenerRow } from "../types";
import { Modal } from "./Modal";

type SortKey = "symbol" | "change_bar_pct" | "change_20_pct" | "rsi14" | "atr_pct" | "volume_ratio" | "level";

const num = (v: number | null | undefined) => v ?? Number.NEGATIVE_INFINITY;
const SORTERS: Record<SortKey, (r: ScreenerRow) => number | string> = {
  symbol: (r) => r.symbol,
  change_bar_pct: (r) => num(r.change_bar_pct),
  change_20_pct: (r) => num(r.change_20_pct),
  rsi14: (r) => num(r.rsi14),
  atr_pct: (r) => num(r.atr_pct),
  volume_ratio: (r) => num(r.volume_ratio),
  level: (r) => (r.nearest_level ? -Math.abs(r.nearest_level.distance_pct) : Number.NEGATIVE_INFINITY),
};

/** Таблица фактов по избранному. Только факты по закрытым свечам: фильтр внимания, а не рекомендация. */
export function ScreenerDialog({ onClose, onSelect }: { onClose: () => void; onSelect: (i: Instrument) => void }) {
  const data = useApi(() => api.screener(), [], 60_000);
  const [sort, setSort] = useState<SortKey>("volume_ratio");
  const [desc, setDesc] = useState(true);
  const rows = useMemo(() => {
    const list = [...(data.data?.rows ?? [])];
    const f = SORTERS[sort];
    list.sort((a, b) => {
      const x = f(a);
      const y = f(b);
      const c = typeof x === "string" ? x.localeCompare(String(y)) : (x as number) - (y as number);
      return desc ? -c : c;
    });
    return list;
  }, [data.data, sort, desc]);

  const th = (key: SortKey, label: string) => (
    <th className="r" aria-sort={sort === key ? (desc ? "descending" : "ascending") : "none"}>
      <button
        className="btn ghost small"
        onClick={() => {
          if (sort === key) setDesc(!desc);
          else {
            setSort(key);
            setDesc(true);
          }
        }}
      >
        {label}
        {sort === key ? (desc ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );

  return (
    <Modal title="Скринер избранного" onClose={onClose}>
      {data.loading && !data.data && <div className="muted">Считаем…</div>}
      {data.error && <div className="error">{data.error}</div>}
      {data.data && rows.length === 0 && <div className="empty">В избранном пока нет инструментов.</div>}
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="num">
            <thead>
              <tr>
                {th("symbol", "Тикер")}
                <th className="r">Цена</th>
                {th("change_bar_pct", "За свечу")}
                {th("change_20_pct", "За 20")}
                {th("rsi14", "RSI")}
                {th("atr_pct", "ATR %")}
                {th("volume_ratio", "Объём ×")}
                {th("level", "До уровня")}
                <th>SMA 20/50</th>
                <th>Правило в рынке</th>
                <th>Сигнал</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={`${r.market}${r.symbol}`} style={r.paused ? { opacity: 0.6 } : undefined}>
                  <td>
                    <button
                      className="btn ghost small"
                      style={{ padding: 0, fontWeight: 600, color: "var(--text)" }}
                      onClick={() => {
                        onSelect({ market: r.market, symbol: r.symbol, name: r.name });
                        onClose();
                      }}
                    >
                      {r.symbol}
                    </button>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {r.tf}
                      {r.paused ? " · пауза" : ""}
                    </div>
                  </td>
                  {r.status === "error" || r.status === "short" ? (
                    <td colSpan={9} className="down">
                      {r.message}
                    </td>
                  ) : (
                    <>
                      <td className="r">
                        {fmtPrice(r.last ?? 0)}
                        {r.status === "stale" && <div className="down" style={{ fontSize: 11 }}>кэш</div>}
                      </td>
                      <td className={`r ${pnlClass(r.change_bar_pct ?? 0)}`}>{r.change_bar_pct == null ? "—" : fmtPct(r.change_bar_pct)}</td>
                      <td className={`r ${pnlClass(r.change_20_pct ?? 0)}`}>{r.change_20_pct == null ? "—" : fmtPct(r.change_20_pct)}</td>
                      <td className="r">{r.rsi14 == null ? "—" : fmtNum(r.rsi14, 1)}</td>
                      <td className="r">{r.atr_pct == null ? "—" : fmtNum(r.atr_pct)}</td>
                      <td className="r">{r.volume_ratio == null ? "—" : fmtNum(r.volume_ratio)}</td>
                      <td className="r">
                        {r.nearest_level ? `${fmtPct(r.nearest_level.distance_pct)} (${fmtPrice(r.nearest_level.price)})` : "—"}
                      </td>
                      <td>
                        {r.above_sma20 == null ? "—" : r.above_sma20 ? "↑" : "↓"} / {r.above_sma50 == null ? "—" : r.above_sma50 ? "↑" : "↓"}
                      </td>
                      <td>
                        {Object.entries(r.rule_state ?? {})
                          .filter(([, v]) => v === 1)
                          .map(([k]) => STRATEGY_SHORT[k] ?? k)
                          .join(", ") || "—"}
                      </td>
                      <td>
                        {r.signals.length === 0
                          ? "—"
                          : r.signals.map((s) => `${s.side === "buy" ? "вход" : "выход"} · ${STRATEGY_SHORT[s.strategy] ?? s.strategy}`).join("; ")}
                      </td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data.data?.notes.map((n) => (
        <p className="caveat" key={n}>
          {n}
        </p>
      ))}
    </Modal>
  );
}
