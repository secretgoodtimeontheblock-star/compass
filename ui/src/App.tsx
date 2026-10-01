import * as Tabs from "@radix-ui/react-tabs";
import { FocusScope } from "@radix-ui/react-focus-scope";
import { PersistentTab } from "./components/PersistentTab";
import { useDrawer } from "./lib/use-drawer";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, lazy } from "react";
import { Toaster, toast } from "sonner";
import { RequestStatus } from "./components/RequestStatus";
import { Modal } from "./components/Modal";
import { api } from "./api";
import { CommandPalette, type PaletteAction } from "./components/CommandPalette";
import { ConsentDialog } from "./components/ConsentDialog";
import { DataStatus } from "./components/DataStatus";
import { GettingStarted } from "./components/GettingStarted";
import { Icon } from "./components/Icon";
import { ScreenerDialog } from "./components/ScreenerDialog";
import { ChartExtras } from "./components/ChartExtras";
import { type ChartLine, type Overlays, PriceChart } from "./components/PriceChart";
import { SearchBox } from "./components/SearchBox";
import { SettingsDialog } from "./components/SettingsDialog";
import { SignalsPanel } from "./components/SignalsPanel";
import { Tip, TipProvider } from "./components/Tip";
import { Watchlist } from "./components/Watchlist";
import { AiContext } from "./lib/ai-context";
import { ExperienceContext } from "./lib/experience";
import { fmtPct, fmtPrice, pnlClass } from "./lib/format";
import { loadPref, savePref, useApi } from "./lib/use-api";
import { useLiveCandles } from "./lib/use-live-candles";
import { hasGap, mergeLiveHistory } from "./lib/live-candles";
import { TF_MS } from "./lib/indicators";
import type { AiStatus, Instrument, JournalDraft, JournalMode, MarketId, Settings, Signal } from "./types";

const AiPanel = lazy(() => import("./components/AiPanel").then((m) => ({ default: m.AiPanel })));

const BacktestPanel = lazy(() => import("./components/BacktestPanel").then((m) => ({ default: m.BacktestPanel })));

const JournalPanel = lazy(() => import("./components/JournalPanel").then((m) => ({ default: m.JournalPanel })));

const ReplayPanel = lazy(() => import("./components/ReplayPanel").then((m) => ({ default: m.ReplayPanel })));

type Tab = "signals" | "backtest" | "journal" | "replay" | "ai";
const TABS = [
  ["signals", "Сигналы", "signals"],
  ["backtest", "Проверка", "backtest"],
  ["journal", "Журнал", "journal"],
  ["replay", "Тренировка", "replay"],
  ["ai", "AI", "ai"],
] as const;

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
  const [journalDraft, setJournalDraft] = useState<JournalDraft | null>(null);
  const [theme, setTheme] = useState(loadPref("theme") === "light" ? "light" : "dark");
  const [market, setMarket] = useState<MarketId>(loadPref("market") === "crypto" ? "crypto" : "moex");
  const [preview, setPreview] = useState<Instrument>();
  const [selSymbol, setSelSymbol] = useState<Record<string, string>>(() => readJson("selected", {}));
  const [tfOverride, setTfOverride] = useState<Record<string, string>>({});
  const [overlays, setOverlays] = useState<Overlays>(() =>
    readJson("overlays", { sma20: false, sma50: false, volume: true }),
  );
  const [tab, setTab] = useState<Tab>("signals");
  const [rightOpen, setRightOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(loadPref("guide-dismissed") !== "yes");
  const [screenerOpen, setScreenerOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
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
    toast(title, { description: text, duration: 7000 });
  }, []);

  const drawer = useDrawer(rightOpen, () => setRightOpen(false));

  // Ctrl/⌘ + K — палитра команд
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // ---- данные ----
  const marketsApi = useApi(() => api.markets(), []);
  const settingsApi = useApi(() => api.settings(), []);
  const strategiesApi = useApi(() => api.strategies(), []);
  const watchApi = useApi(() => api.watchlist(), []);
  const signalsApi = useApi(() => api.signals({ limit: 200 }), [], 30_000);
  const [journalMode, setJournalMode] = useState<JournalMode>("real");
  const positionsApi = useApi(() => api.positions(journalMode), [journalMode]);
  const aiStatusApi = useApi(() => api.aiStatus(), []);

  // AI: согласие на отправку данных запрашивается в момент первого обращения, а не «на всякий случай»
  const [consent, setConsent] = useState<{ status: AiStatus; resolve: (ok: boolean) => void }>();
  const aiCtx = useMemo(
    () => ({
      requestConsent: async () => {
        const status = await api.aiStatus();
        return new Promise<boolean>((resolve) => setConsent({ status, resolve }));
      },
      openSettings: () => setSettingsOpen(true),
    }),
    [],
  );

  const markets = marketsApi.data ?? [];
  const settings = settingsOverride ?? settingsApi.data;

  const togglePause = async (i: Instrument) => {
    if (!settings) return;
    const key = `${i.market}|${i.symbol}`;
    const cur = new Set(settings.paused_instruments ?? []);
    if (cur.has(key)) cur.delete(key);
    else cur.add(key);
    try {
      setSettingsOverride(await api.saveSettings({ paused_instruments: [...cur] }));
    } catch (e) {
      pushToast("Не удалось изменить наблюдение", e instanceof Error ? e.message : "Повторите попытку");
    }
  };
  const marketInfo = markets.find((m) => m.id === market);
  const items = useMemo(() => (watchApi.data ?? []).filter((i) => i.market === market), [watchApi.data, market]);

  const selected: Instrument | undefined = useMemo(() => {
    const want = selSymbol[market];
    return items.find((i) => i.symbol === want) ?? (preview?.market === market && preview.symbol === want ? preview : items[0]);
  }, [items, selSymbol, market, preview]);

  const defaultTf = settings ? settings[market === "moex" ? "tf_moex" : "tf_crypto"] : "1d";
  const tf = tfOverride[market] && marketInfo?.timeframes.includes(tfOverride[market]) ? tfOverride[market] : defaultTf;

  const candlesApi = useApi(
    selected ? () => api.candles(selected.market, selected.symbol, tf, 500) : null,
    [selected?.market, selected?.symbol, tf],
    60_000,
  );
  const live = useLiveCandles(market, selected?.symbol, tf, !!marketInfo?.live_supported, marketInfo?.live_kind === "poll");
  // Обычный REST-опрос тоже может восстановить историю, пока поток остаётся подключён.
  const streamedHistory = live.feed?.history;
  const history = streamedHistory && (!candlesApi.data || (streamedHistory.fetched_at ?? 0) > (candlesApi.data.fetched_at ?? 0))
    ? streamedHistory : candlesApi.data;
  const candles = useMemo(() => mergeLiveHistory(history, live.feed?.updates ?? []), [history, live.feed?.updates]);
  const gap = market === "crypto" && hasGap(candles, TF_MS[tf]);

  const levelsApi = useApi(
    selected ? () => api.levels(selected.market, selected.symbol) : null,
    [selected?.market, selected?.symbol],
  );
  const plansActiveApi = useApi(
    selected ? () => api.plansActive(selected.market, selected.symbol) : null,
    [selected?.market, selected?.symbol],
    60_000,
  );
  const chartLines = useMemo<ChartLine[]>(
    () => [
      ...(levelsApi.data ?? []).map((l) => ({ price: l.price, label: l.label || "уровень", kind: "level" as const })),
      ...(plansActiveApi.data ?? []).flatMap((p) => [
        { price: p.entry, label: `План №${p.id}: вход`, kind: "entry" as const },
        { price: p.stop, label: `План №${p.id}: стоп`, kind: "stop" as const },
        ...(p.target != null ? [{ price: p.target, label: `План №${p.id}: цель`, kind: "target" as const }] : []),
      ]),
    ],
    [levelsApi.data, plansActiveApi.data],
  );

  const journalApi = useApi(
    selected ? () => api.journal({ market: selected.market, symbol: selected.symbol, mode: journalMode }) : null,
    [selected?.market, selected?.symbol, journalMode],
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
    setPreview(i);
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
      if (preview?.market === i.market && preview.symbol === i.symbol) setPreview(undefined);
      setSelSymbol((old) => ({ ...old, [i.market]: old[i.market] === i.symbol ? "" : old[i.market] }));
      watchApi.reload();
    } catch (e) {
      pushToast("Не удалось убрать", e instanceof Error ? e.message : "Ошибка");
    }
  };

  const scan = async () => {
    if (scanBusy) return;
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

  const openTab = (t: Tab) => {
    setTab(t);
    setRightOpen(true);
  };
  const paletteActions: PaletteAction[] = [
    { id: "scan", label: "Проверить сигналы", icon: "scan", run: () => void scan() },
    { id: "signals", label: "Открыть сигналы", icon: "signals", run: () => openTab("signals") },
    { id: "backtest", label: "Проверить стратегию на истории", icon: "backtest", run: () => openTab("backtest") },
    { id: "replay", label: "Тренироваться без реальных денег", icon: "replay", run: () => openTab("replay") },
    { id: "screener", label: "Открыть скринер избранного", icon: "chart", run: () => setScreenerOpen(true) },
    { id: "journal", label: "Открыть журнал сделок", icon: "journal", run: () => openTab("journal") },
    { id: "ai", label: "Спросить AI-наставника", icon: "ai", run: () => openTab("ai") },
    { id: "moex", label: "Рынок: акции МосБиржи", icon: "chart", run: () => setMarket("moex") },
    { id: "crypto", label: "Рынок: крипта", icon: "chart", run: () => setMarket("crypto") },
    {
      id: "theme",
      label: theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему",
      icon: theme === "dark" ? "sun" : "moon",
      run: () => setTheme(theme === "dark" ? "light" : "dark"),
    },
    { id: "guide", label: guideOpen ? "Скрыть «С чего начать»" : "Показать «С чего начать»", icon: "help", run: () => setGuideOpen((v) => !v) },
    { id: "settings", label: "Настройки", icon: "settings", run: () => setSettingsOpen(true) },
  ];

  if (marketsApi.error && !marketsApi.data) {
    return (
      <div className="fatal" role="alert">
        <div className="fatal-card">
          <h1>Не удалось связаться с движком Compass</h1>
          <p className="muted">{marketsApi.error}</p>
          <p className="muted small-text">Проверьте, что приложение запущено, и повторите попытку.</p>
          <button className="btn primary" onClick={marketsApi.reload}>
            <Icon name="scan" /> Повторить
          </button>
        </div>
      </div>
    );
  }

  const last = candles.at(-1);
  const prev = candles.at(-2);
  const change = last && prev ? ((last.c / prev.c - 1) * 100) : undefined;

  return (
    <AiContext.Provider value={aiCtx}>
    <ExperienceContext.Provider value={settings?.experience_level ?? "beginner"}>
    <TipProvider delayDuration={350}>
    <div className="app">
      <a className="skip-link" href="#chart">К графику</a>
      <header className="topbar">
        <div className="brand">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <defs>
              <linearGradient id="brand-g" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
                <stop stopColor="#6ea0ff" />
                <stop offset="1" stopColor="#7c5cff" />
              </linearGradient>
            </defs>
            <circle cx="16" cy="16" r="14" fill="url(#brand-g)" />
            <path d="M16 6l4 10-4 10-4-10z" fill="#fff" />
          </svg>
          <span className="brand-name">Compass</span>
        </div>
        <div className="seg" role="group" aria-label="Рынок">
          {(Object.keys(MARKET_LABEL) as MarketId[]).map((m) => (
            <button key={m} aria-pressed={market === m} onClick={() => setMarket(m)}>
              {MARKET_LABEL[m]}
            </button>
          ))}
        </div>
        <SearchBox market={market} catalogSize={marketInfo?.instruments} onPick={addToWatch} />
        <div className="spacer" />
        <button className="btn" aria-label="С чего начать" title="С чего начать" aria-expanded={guideOpen} onClick={() => setGuideOpen((v) => !v)}>
          <Icon name="help" /> <span className="btn-label">С чего начать</span>
        </button>
        <button className="btn primary" aria-label={scanBusy ? "Проверяем сигналы" : "Проверить сигналы"} title="Проверить сигналы" onClick={scan} disabled={scanBusy || items.length === 0 && (watchApi.data ?? []).length === 0}>
          <span className={scanBusy ? "spin" : undefined}><Icon name="scan" /></span>
          <span className="btn-label">{scanBusy ? "Проверяем…" : "Проверить сигналы"}</span>
        </button>
        <button className="btn panel-toggle" aria-label="Открыть панель анализа" title="Панель анализа" onClick={() => setRightOpen((o) => !o)} aria-expanded={rightOpen} aria-controls="right-panel">
          <Icon name="panel" /> <span className="btn-label">Панель</span> {unseen.length > 0 && <span className="badge" aria-label={`${unseen.length} непрочитанных`}>{unseen.length}</span>}
        </button>
        <Tip label="Команды (Ctrl K)">
          <button className="icon-btn" onClick={() => setPaletteOpen(true)} aria-label="Открыть палитру команд">
            <Icon name="command" size={18} />
          </button>
        </Tip>
        <Tip label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}>
          <button className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={theme === "dark" ? "Включить светлую тему" : "Включить тёмную тему"}>
            <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
          </button>
        </Tip>
        <Tip label="Настройки">
          <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="Настройки">
            <Icon name="settings" size={18} />
          </button>
        </Tip>
      </header>

      <div className="main">
        <aside className="side" aria-label="Избранное">
          <RequestStatus state={watchApi} label="Не удалось загрузить избранное" />
          <Watchlist
            market={market}
            items={items}
            selected={selected?.symbol}
            onSelect={selectInstrument}
            onRemove={removeFromWatch}
            onQuickAdd={addToWatch}
            paused={new Set(settings?.paused_instruments ?? [])}
            onTogglePause={togglePause}
            onScreener={() => setScreenerOpen(true)}
          />
        </aside>

        <main className="center">
            {guideOpen && (
              <div style={{ overflow: "hidden", flexShrink: 0 }}>
            <GettingStarted
                  selected={selected}
                  onPick={async (i) => {
                    await api.addWatch(i);
                    watchApi.reload();
                    selectInstrument(i);
                  }}
                  onBacktest={() => { setTab("backtest"); setRightOpen(true); }}
                  onLearn={() => { setTab("ai"); setRightOpen(true); }}
                  onTrain={() => { setTab("replay"); setRightOpen(true); }}
                  onClose={() => { setGuideOpen(false); savePref("guide-dismissed", "yes"); }}
                />
              </div>
            )}
          <div className="chart-head">
            {selected ? (
              <>
                <span className="title">{selected.symbol}</span>
                {selected.name !== selected.symbol && <span className="muted">{selected.name}</span>}
                {last && <span className="price num">{fmtPrice(last.c)}</span>}
                {change !== undefined && <span className={`change-pill num ${pnlClass(change)}`}>{fmtPct(change)}</span>}
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
          <DataStatus
            market={marketInfo} state={live.feed?.state} message={live.feed?.message}
            receivedAt={live.feed?.receivedAt} fetchedAt={history?.fetched_at}
            stale={!!history?.stale} hasData={candles.length > 0} gap={gap} quality={candlesApi.data?.quality}
            onRetry={() => { live.retry(); candlesApi.reload(); }}
          />
          <div className="chart-wrap" id="chart" tabIndex={-1}>
            {candlesApi.loading && <div className="loading-bar" />}
            {candlesApi.loading && candles.length === 0 && !candlesApi.error && selected && (
              <div className="chart-skeleton" aria-hidden="true">
                {Array.from({ length: 28 }, (_, i) => (
                  <span key={i} style={{ height: `${28 + ((i * 37) % 46)}%` }} />
                ))}
              </div>
            )}
            {selected ? (
              <PriceChart
                candles={candles}
                tf={tf}
                signals={chartSignals}
                trades={journalApi.data ?? []}
                overlays={overlays}
                theme={theme}
                resetKey={`${selected.market}:${selected.symbol}:${tf}`}
                lines={chartLines}
              />
            ) : (
              <div className="chart-empty">
                <div className="empty-hero">
                  <span className="empty-hero-icon"><Icon name="chart" size={26} /></span>
                  <b>Выберите тикер</b>
                  <p>Найдите акцию или криптопару через поиск сверху либо добавьте популярный тикер слева.</p>
                  <button className="btn" onClick={() => setPaletteOpen(true)}>
                    <Icon name="command" size={14} /> Открыть палитру команд
                  </button>
                </div>
              </div>
            )}
            {candlesApi.error && selected && candles.length === 0 && (
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
            {selected && !candlesApi.loading && !candlesApi.error && candles.length === 0 && candlesApi.data && (
              <div className="chart-empty">
                <div>
                  <b>Нет свечей за выбранный период</b>
                  <p>Источник не вернул историю {selected.symbol}. Попробуйте другой таймфрейм или выберите инструмент через поиск.</p>
                  <button className="btn" onClick={candlesApi.reload}>Проверить снова</button>
                </div>
              </div>
            )}
          </div>
          {selected && (
            <ChartExtras key={`${selected.market}:${selected.symbol}`}
              instrument={selected}
              marketInfo={marketInfo}
              tf={tf}
              lastPrice={last?.c}
              levels={levelsApi.data ?? []}
              onLevelsChanged={() => levelsApi.reload()}
              theme={theme}
            />
          )}
        </main>

        {rightOpen && <div className="scrim" onClick={() => setRightOpen(false)} aria-hidden="true" />}
        <FocusScope asChild trapped={drawer.active} loop={drawer.active} onMountAutoFocus={(e) => e.preventDefault()} onUnmountAutoFocus={(e) => e.preventDefault()}>
        <aside ref={drawer.panel} role={drawer.active ? "dialog" : undefined} aria-modal={drawer.active || undefined} id="right-panel" className={`right${rightOpen ? " open" : ""}`} aria-label="Сигналы, проверка стратегий, журнал">
          <Tabs.Root className="tabs-root" value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <div className="tabs-row">
              <Tabs.List className="tabs" aria-label="Разделы панели">
                {TABS.map(([k, label, icon]) => (
                  <Tabs.Trigger key={k} value={k} title={label} aria-label={k === "signals" && unseen.length > 0 ? `${label}, непрочитанных: ${unseen.length}` : label}>
                    <Icon name={icon} size={15} />
                    <span className="tab-label">{label}</span>
                    {k === "signals" && unseen.length > 0 && <span className="badge">{unseen.length}</span>}
                  </Tabs.Trigger>
                ))}
              </Tabs.List>
              <button className="icon-btn drawer-close" onClick={() => setRightOpen(false)} aria-label="Закрыть панель">
                <Icon name="close" size={18} />
              </button>
            </div>
            <PersistentTab value="signals" active={tab}>
            <RequestStatus state={signalsApi} label="Не удалось обновить сигналы" />
            <SignalsPanel
              signals={allSignals}
              selected={selected}
              unseenCount={unseen.length}
              onSelect={selectInstrument}
              onMarkSeen={markSeen}
              onChanged={() => signalsApi.reload()}
              onRecord={(draft) => {
                selectInstrument({ market: draft.market, symbol: draft.symbol, name: draft.symbol });
                setJournalDraft(draft);
                setTab("journal");
                setRightOpen(true);
              }}
            />
            </PersistentTab>
            <PersistentTab value="backtest" active={tab}>
            <RequestStatus state={strategiesApi} label="Не удалось загрузить стратегии" />
            <BacktestPanel key={`${selected?.market}:${selected?.symbol}:${tf}`}
              instrument={selected}
              tf={tf}
              strategies={strategiesApi.data ?? []}
              settings={settings}
              theme={theme}
              onStrategiesSaved={() => { setSettingsOverride(undefined); settingsApi.reload(); }}
            />
            </PersistentTab>
            <PersistentTab value="journal" active={tab}>
            <RequestStatus state={journalApi} label="Не удалось загрузить журнал" />
            <JournalPanel
              instrument={selected}
              entries={journalApi.data ?? []}
              positions={positionsApi.data ?? []}
              mode={journalMode}
              onModeChange={setJournalMode}
              lastPrice={last?.c}
              onChanged={() => {
                journalApi.reload();
                positionsApi.reload();
              }}
              onSelect={selectInstrument}
              onDraftConsumed={() => setJournalDraft(null)}
              draft={
                journalDraft &&
                selected &&
                journalDraft.market === selected.market &&
                journalDraft.symbol === selected.symbol
                  ? journalDraft
                  : null
              }
            />
            </PersistentTab>
            <PersistentTab value="replay" active={tab}>
              <ReplayPanel instrument={selected} tf={tf} theme={theme} />
            </PersistentTab>
            <PersistentTab value="ai" active={tab}>
              <AiPanel instrument={selected} tf={tf} status={aiStatusApi.data} />
            </PersistentTab>
          </Tabs.Root>
        </aside>
        </FocusScope>
      </div>

      <footer className="footer">
        <span>Не инвестиционная рекомендация. Приложение не совершает сделок — решение и риск за вами.</span>
        <span className="spacer" />
        <span>{marketInfo?.name}</span>
      </footer>

      {settingsOpen && !settings && <Modal title="Настройки" onClose={() => setSettingsOpen(false)}><RequestStatus state={settingsApi} label="Не удалось загрузить настройки" /></Modal>}
      {settingsOpen && settings && (
        <SettingsDialog
          settings={settings}
          markets={markets}
          onClose={() => setSettingsOpen(false)}
          onSaved={(s) => {
            setSettingsOverride(s);
            settingsApi.reload();
            aiStatusApi.reload();
          }}
        />
      )}

      {consent && (
        <ConsentDialog
          status={consent.status}
          onDecline={() => {
            consent.resolve(false);
            setConsent(undefined);
          }}
          onAccept={async () => {
            try {
              setSettingsOverride(await api.saveSettings({ ai_consent: consent.status.provider }));
              aiStatusApi.reload();
              consent.resolve(true);
            } catch (e) {
              pushToast("Не удалось сохранить согласие", e instanceof Error ? e.message : "Ошибка");
              consent.resolve(false);
            }
            setConsent(undefined);
          }}
        />
      )}

      {screenerOpen && <ScreenerDialog onClose={() => setScreenerOpen(false)} onSelect={selectInstrument} />}
      <Toaster
        theme={theme as "dark" | "light"}
        position="bottom-right"
        closeButton
        offset={{ bottom: 40, right: 14 }}
        style={
          {
            "--normal-bg": "var(--panel)",
            "--normal-text": "var(--text)",
            "--normal-border": "var(--border)",
            fontFamily: "inherit",
          } as React.CSSProperties
        }
      />

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        actions={paletteActions}
        instruments={watchApi.data ?? []}
        onPick={selectInstrument}
      />
    </div>
    </TipProvider>
    </ExperienceContext.Provider>
    </AiContext.Provider>
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
