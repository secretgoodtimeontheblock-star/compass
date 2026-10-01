import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { Instrument, MarketId } from "../types";
import { Icon } from "./Icon";

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
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();

  // поиск с паузой 300 мс; устаревший ответ (пользователь уже дописал) отбрасываем
  useEffect(() => {
    const text = q.trim();
    setResults([]);
    setError(undefined);
    setBusy(!!text);
    if (!text) {
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

  useEffect(() => setActive(0), [q, results]);

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

  // «/» — быстрый переход к поиску, как в большинстве веб-приложений; в полях ввода не срабатывает
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.querySelector('[role="dialog"]') || t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      input.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const pick = (i: Instrument) => {
    onPick(i);
    setQ("");
    setResults([]);
    setOpen(false);
  };

  return (
    <div className="search" ref={root}>
      <span className="search-icon"><Icon name="search" size={15} /></span>
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-expanded={open && !!q.trim()}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && results[active] ? `${listId}-${active}` : undefined}
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
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((i) => Math.min(i + 1, Math.max(results.length - 1, 0)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter" && open && !busy && results[active]) pick(results[active]);
          else if (e.key === "Escape") {
            if (open) e.stopPropagation();
            setOpen(false);
          }
        }}
      />
      {open && q.trim() && (
        <div className="search-results" role="listbox" id={listId}>
          {busy && <div className="empty" role="status">Ищем…</div>}
          {error && <div className="empty down">{error}</div>}
          {!busy && !error && results.length === 0 && <div className="empty">Ничего не найдено</div>}
          {results.map((r, index) => (
            <button
              key={r.symbol}
              id={`${listId}-${index}`}
              tabIndex={-1}
              className={index === active ? "active" : undefined}
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(r)}
              role="option"
              aria-selected={index === active}
            >
              <b>{r.symbol}</b>
              <span className="muted">
                {[KIND[r.kind ?? ""], r.name !== r.symbol ? r.name : ""].filter(Boolean).join(" · ")}
              </span>
            </button>
          ))}
        </div>
      )}
      {!q && <kbd className="search-kbd" aria-hidden="true">/</kbd>}
    </div>
  );
}
