import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Instrument, MarketId } from "../types";

interface Props {
  market: MarketId;
  catalogSize?: number;
  onPick: (i: Instrument) => void;
}

const KIND: Record<string, string> = { share: "акция или фонд", bond: "облигация", fx: "валюта" };

export function SearchBox({ market, catalogSize, onPick }: Props) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // поиск с паузой 300 мс; устаревший ответ (пользователь уже дописал) отбрасываем
  useEffect(() => {
    const text = q.trim();
    if (!text) {
      setResults([]);
      setError(undefined);
      return;
    }
    let stale = false;
    const id = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await api.search(market, text);
        if (!stale) {
          setResults(r);
          setError(undefined);
        }
      } catch (e) {
        if (!stale) setError(e instanceof Error ? e.message : "Ошибка поиска");
      } finally {
        if (!stale) setBusy(false);
      }
    }, 300);
    return () => {
      stale = true;
      clearTimeout(id);
    };
  }, [q, market]);

  useEffect(() => {
    setQ("");
    setResults([]);
  }, [market]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const pick = (i: Instrument) => {
    onPick(i);
    setQ("");
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="search" ref={root}>
      <input
        type="search"
        value={q}
        placeholder={
          market === "moex"
            ? `Акция, фонд, облигация или валюта${catalogSize ? ` · ${catalogSize}` : ""}`
            : `Спотовая пара OKX${catalogSize ? ` · ${catalogSize}` : ""}`
        }
        aria-label="Поиск инструмента"
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && results[0]) pick(results[0]);
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && q.trim() && (
        <div className="search-results" role="listbox">
          {busy && <div className="empty">Ищем…</div>}
          {error && <div className="empty down">{error}</div>}
          {!busy && !error && results.length === 0 && <div className="empty">Ничего не найдено</div>}
          {results.map((r) => (
            <button key={r.symbol} onClick={() => pick(r)} role="option">
              <b>{r.symbol}</b>
              <span className="muted">
                {[KIND[r.kind ?? ""], r.name !== r.symbol ? r.name : ""].filter(Boolean).join(" · ")}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
