import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { BacktestPanel } from "./components/BacktestPanel";
import { JournalPanel } from "./components/JournalPanel";
import { type Overlays, PriceChart } from "./components/PriceChart";
import { SearchBox } from "./components/SearchBox";
import { SettingsDialog } from "./components/SettingsDialog";
import { SignalsPanel } from "./components/SignalsPanel";
import { Watchlist } from "./components/Watchlist";
import { fmtPct, fmtPrice, pnlClass } from "./lib/format";
import { loadPref, savePref, useApi } from "./lib/use-api";
import type { Instrument, MarketId, Settings, Signal } from "./types";

type Tab = "signals" | "backtest" | "journal";
type Toast = { id: number; title: string; text: string };

const MARKET_LABEL: Record<MarketId, string> = { moex: "Акции МосБиржи", crypto: "Крипта" };

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = loadPref(key);
    return raw ? { ...fallback, ...(JSON.parse(raw) as object) } : fallback;
  } catch {
    return fallback;
  }
}

export default function App() {
  const [theme, setTheme] = useState(loadPref("theme") === "light" ? "light" : "dark");
  const [market, setMarket] = useState<MarketId>(loadPref("market") === "crypto" ? "crypto" : "moex");
  const [selSymbol, setSelSymbol] = useState<Record<string, string>>(() => readJson("selected", {}));
  const [tfOverride, setTfOverride] = useState<Record<string, string>>({});
  const [overlays, setOverlays] = useState<Overlays>(() =>
    readJson("overlays", { sma20: false, sma50: false, volume: true }),
  );
  const [tab, setTab] = useState<Tab>("signals");
  const [rightOpen, setRightOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [scanBusy, setScanBusy] = useState(false);
  const [settingsOverride, setSettingsOverride] = useState<Settings>();

  // useLayoutEffect, а не useEffect: графики читают цвета из CSS-переменных в своих useEffect,
  // и тема на <html> должна успеть смениться раньше (иначе график красится цветами прошлой темы)
  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    savePref("theme", theme);
  }, [theme]);
  useEffect(() => savePref("market", market), [market]);
  useEffect(() => savePref("selected", JSON.stringify(selSymbol)), [selSymbol]);
  useEffect(() => savePref("overlays", JSON.stringify(overlays)), [overlays]);

  const pushToast = useCallback((title: string, text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, title, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000);
  }, []);

  // ---- данные ----
  const marketsApi = useApi(() => api.markets(), []);
  const settingsApi = useApi(() => api.settings(), []);
  const strategiesApi = useApi(() => api.strategies(), []);
  const watchApi = useApi(() => api.watchlist(), []);
  const signalsApi = useApi(() => api.signals({ limit: 200 }), [], 30_000);
  const positionsApi = useApi(() => api.positions(), []);

  const markets = marketsApi.data ?? [];
  const settings = settingsOverride ?? settingsApi.data;
  const marketInfo = markets.find((m) => m.id === market);
  const items = useMemo(() => (watchApi.data ?? []).filter((i) => i.market === market), [watchApi.data, market]);

  const selected: Instrument | undefined = useMemo(() => {
    const want = selSymbol[market];
    return items.find((i) => i.symbol === want) ?? items[0];
  }, [items, selSymbol, market]);

  const defaultTf = settings ? settings[market === "moex" ? "tf_moex" : "tf_crypto"] : "1d";
  const tf = tfOverride[market] && marketInfo?.timeframes.includes(tfOverride[market]) ? tfOverride[market] : defaultTf;

  const candlesApi = useApi(
    selected ? () => api.candles(selected.market, selected.symbol, tf, 500) : null,
    [selected?.market, selected?.symbol, tf],
    60_000,
  );
  const candles = candlesApi.data?.candles ?? [];

  const journalApi = useApi(
    selected ? () => api.journal({ market: selected.market, symbol: selected.symbol }) : null,
    [selected?.market, selected?.symbol],
  );

  const allSignals = signalsApi.data ?? [];
  const unseen = allSignals.filter((s) => !s.seen);
  const chartSignals = useMemo(
    () => allSignals.filter((s) => s.market === selected?.market && s.symbol === selected?.symbol),
    [allSignals, selected?.market, selected?.symbol],
  );

  // ---- уведомления о новых сигналах (первая загрузка — молча) ----
  const knownIds = useRef<Set<number> | null>(null);
  useEffect(() => {
    if (!signalsApi.data) return;
    if (knownIds.current === null) {
      knownIds.current = new Set(signalsApi.data.map((s) => s.id));
      return;
    }
    const fresh = signalsApi.data.filter((s) => !s.seen && !knownIds.current!.has(s.id));
    fresh.forEach((s) => knownIds.current!.add(s.id));
    for (const s of fresh) notifyNew(s, pushToast);
  }, [signalsApi.data, pushToast]);

  // ---- действия ----
  const selectInstrument = (i: Instrument) => {
    setMarket(i.market);
    setSelSymbol((m) => ({ ...m, [i.market]: i.symbol }));
  };

  const addToWatch = async (i: Instrument) => {
    try {
      await api.addWatch(i);
      watchApi.reload();
      selectInstrument(i);
    } catch (e) {
      pushToast("Не удалось добавить", e instanceof Error ? e.message : "Ошибка");
    }
  };

  const removeFromWatch = async (i: Instrument) => {
    try {
      await api.removeWatch(i);
      watchApi.reload();
    } catch (e) {
      pushToast("Не удалось убрать", e instanceof Error ? e.message : "Ошибка");
    }
  };

  const scan = async () => {
    setScanBusy(true);
    try {
      const r = await api.scan();
      knownIds.current ??= new Set();
      r.new.forEach((s) => knownIds.current!.add(s.id));
      signalsApi.reload();
      pushToast(
        r.new.length ? `Новых сигналов: ${r.new.length}` : "Новых сигналов нет",
        r.errors.length ? `Не удалось проверить: ${r.errors.join("; ")}` : "Все тикеры из избранного проверены.",
      );
      if (r.new.length) {
        setTab("signals");
        setRightOpen(true);
      }
    } catch (e) {
      pushToast("Проверка не удалась", e instanceof Error ? e.message : "Ошибка");
    } finally {
      setScanBusy(false);
    }
  };

  const markSeen = async () => {
    await api.markSeen().catch(() => undefined);
    signalsApi.reload();
  };

  if (marketsApi.error && !marketsApi.data) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <b>Не удалось связаться с движком Compass.</b>
        <p>{marketsApi.error}</p>
        <button className="btn primary" onClick={marketsApi.reload}>
          Повторить
        </button>
      </div>
    );
  }

  const last = candles.at(-1);
  const prev = candles.at(-2);
  const change = last && prev ? ((last.c / prev.c - 1) * 100) : undefined;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <circle cx="16" cy="16" r="14" fill="var(--accent)" />
            <path d="M16 6l4 10-4 10-4-10z" fill="#fff" />
          </svg>
          Compass
        </div>
        <div className="seg" role="group" aria-label="Рынок">
          {(Object.keys(MARKET_LABEL) as MarketId[]).map((m) => (
            <button key={m} aria-pressed={market === m} onClick={() => setMarket(m)}>
              {MARKET_LABEL[m]}
            </button>
          ))}
        </div>
        <SearchBox market={market} onPick={addToWatch} />
        <div className="spacer" />
        <button className="btn primary" onClick={scan} disabled={scanBusy || items.length === 0 && (watchApi.data ?? []).length === 0}>
          {scanBusy ? "Проверяем…" : "Проверить сигналы"}
        </button>
        <button className="btn" onClick={() => setRightOpen((o) => !o)} aria-label="Показать панель сигналов" style={{ display: "var(--narrow-btn, none)" }}>
          Панель {unseen.length > 0 && <span className="badge">{unseen.length}</span>}
        </button>
        <button className="btn ghost" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Сменить тему">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        <button className="btn ghost" onClick={() => setSettingsOpen(true)} aria-label="Настройки">
          ⚙
        </button>
      </header>

      <div className="main">
        <aside className="side" aria-label="Избранное">
          <Watchlist
            market={market}
            items={items}
            selected={selected?.symbol}
            onSelect={selectInstrument}
            onRemove={removeFromWatch}
            onQuickAdd={addToWatch}
          />
        </aside>

        <main className="center">
          <div className="chart-head">
            {selected ? (
              <>
                <span className="title">{selected.symbol}</span>
                {selected.name !== selected.symbol && <span className="muted">{selected.name}</span>}
                {last && <span className="price num">{fmtPrice(last.c)}</span>}
                {change !== undefined && <span className={`num ${pnlClass(change)}`}>{fmtPct(change)}</span>}
              </>
            ) : (
              <span className="muted">{MARKET_LABEL[market]}</span>
            )}
            <div className="spacer" />
            <div className="overlays">
              {(
                [
                  ["volume", "Объём"],
                  ["sma20", "SMA 20"],
                  ["sma50", "SMA 50"],
                ] as const
              ).map(([k, label]) => (
                <label key={k}>
                  <input
                    type="checkbox"
                    aria-label={label}
                    checked={overlays[k]}
                    onChange={(e) => setOverlays({ ...overlays, [k]: e.target.checked })}
                  />
                  {label}
                </label>
              ))}
            </div>
            <div className="seg" role="group" aria-label="Таймфрейм">
              {(marketInfo?.timeframes ?? []).map((t) => (
                <button key={t} aria-pressed={t === tf} onClick={() => setTfOverride({ ...tfOverride, [market]: t })}>
                  {t}
                </button>
              ))}
            </div>
          </div>
          {candlesApi.data?.stale && (
            <div className="notice">Источник данных сейчас недоступен — показаны сохранённые свечи.</div>
          )}
          <div className="chart-wrap">
            {candlesApi.loading && <div className="loading-bar" />}
            {selected ? (
              <PriceChart
                candles={candles}
                tf={tf}
                signals={chartSignals}
                trades={journalApi.data ?? []}
                overlays={overlays}
                theme={theme}
                resetKey={`${selected.market}:${selected.symbol}:${tf}`}
              />
            ) : (
              <div className="chart-empty">
                <div>
                  <b>Выберите тикер</b>
                  <p>Найдите акцию или криптопару через поиск сверху либо добавьте популярный тикер слева.</p>
                </div>
              </div>
            )}
            {candlesApi.error && selected && (
              <div className="chart-empty" style={{ background: "color-mix(in srgb, var(--bg) 85%, transparent)" }}>
                <div>
                  <b className="down">Не удалось загрузить свечи</b>
                  <p>{candlesApi.error}</p>
                  {market === "crypto" && (
                    <p className="muted">
                      Биржа может быть недоступна из вашей сети. Смените биржу или задайте прокси (COMPASS_CRYPTO_EXCHANGE,
                      COMPASS_PROXY).
                    </p>
                  )}
                  <button className="btn" onClick={candlesApi.reload}>
                    Повторить
                  </button>
                </div>
              </div>
            )}
          </div>
        </main>

        <aside className={`right${rightOpen ? " open" : ""}`} aria-label="Сигналы, проверка стратегий, журнал">
          <div className="tabs" role="tablist">
            {(
              [
                ["signals", "Сигналы"],
                ["backtest", "Проверка"],
                ["journal", "Журнал"],
              ] as const
            ).map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                {label} {k === "signals" && unseen.length > 0 && <span className="badge">{unseen.length}</span>}
              </button>
            ))}
          </div>
          {tab === "signals" && (
            <SignalsPanel
              signals={allSignals}
              selected={selected}
              unseenCount={unseen.length}
              onSelect={selectInstrument}
              onMarkSeen={markSeen}
            />
          )}
          {tab === "backtest" && (
            <BacktestPanel
              instrument={selected}
              tf={tf}
              strategies={strategiesApi.data ?? []}
              settings={settings}
              theme={theme}
            />
          )}
          {tab === "journal" && (
            <JournalPanel
              instrument={selected}
              entries={journalApi.data ?? []}
              positions={positionsApi.data ?? []}
              lastPrice={last?.c}
              onChanged={() => {
                journalApi.reload();
                positionsApi.reload();
              }}
              onSelect={selectInstrument}
            />
          )}
        </aside>
      </div>

      <footer className="footer">
        <span>Не инвестиционная рекомендация. Приложение не совершает сделок — решение и риск за вами.</span>
        <span className="spacer" />
        <span>{marketInfo?.name}</span>
      </footer>

      {settingsOpen && settings && (
        <SettingsDialog
          settings={settings}
          markets={markets}
          onClose={() => setSettingsOpen(false)}
          onSaved={(s) => {
            setSettingsOverride(s);
            settingsApi.reload();
          }}
        />
      )}

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            <b>{t.title}</b>
            {t.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function notifyNew(s: Signal, push: (title: string, text: string) => void) {
  const title = `${s.side === "buy" ? "Сигнал на вход" : "Сигнал на выход"}: ${s.symbol}`;
  const text = `${s.tf}, цена закрытия ${fmtPrice(s.price)}`;
  push(title, text);
  // системное уведомление — только если пользователь уже разрешил (мы разрешение не выпрашиваем)
  try {
    if ("Notification" in window && Notification.permission === "granted") new Notification(title, { body: text });
  } catch {
    /* встроенное окно может не поддерживать уведомления — тост в приложении уже показан */
  }
}
