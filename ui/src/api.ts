import type {
  AiModel,
  AiProviderInfo,
  AiResult,
  AiStatus,
  AccountDto,
  BacktestRequestBody,
  BacktestResponse,
  CandlesResponse,
  DayResponse,
  Instrument,
  JournalEntry,
  JournalMode,
  Market,
  MarketId,
  OosResponse,
  PlanDto,
  PlanReview,
  Position,
  RiskResponse,
  ScanResponse,
  Settings,
  Signal,
  Strategy,
  WatchStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** машинный код ошибки AI: off | consent | busy | auth | ai_error … */
    readonly code?: string,
  ) {
    super(message);
  }
}

// FastAPI отдаёт detail строкой (наши ошибки) или списком (валидация pydantic).
function detailText(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg ?? "ошибка"}`)
      .join("; ");
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError("Нет связи с движком Compass. Он запущен?", 0);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const code = typeof (body as { code?: unknown } | null)?.code === "string" ? (body as { code: string }).code : undefined;
    throw new ApiError(detailText(body, `Ошибка ${res.status}`), res.status, code);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });
const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) p.set(k, String(v));
  return p.toString();
};

export const api = {
  markets: () => request<Market[]>("/api/markets"),
  search: (market: MarketId, q: string) =>
    request<Instrument[]>(`/api/markets/${market}/search?${qs({ q })}`),
  candles: (market: MarketId, symbol: string, tf: string, limit = 500) =>
    request<CandlesResponse>(`/api/candles?${qs({ market, symbol, tf, limit })}`),

  watchlist: () => request<Instrument[]>("/api/watchlist"),
  addWatch: (i: Instrument) => request<Instrument>("/api/watchlist", json("POST", i)),
  // слэш в тикере (BTC/USDT) — часть пути, кодировать его нельзя
  removeWatch: (i: Instrument) => request<void>(`/api/watchlist/${i.market}/${i.symbol}`, { method: "DELETE" }),

  strategies: () => request<Strategy[]>("/api/strategies"),
  backtest: (body: BacktestRequestBody) => request<BacktestResponse>("/api/backtest", json("POST", body)),
  validate: (body: BacktestRequestBody & { train_pct: number; grid: Record<string, number[]> }) =>
    request<OosResponse>("/api/validate", json("POST", body)),

  signals: (p: { limit?: number; unseen?: boolean; market?: MarketId; symbol?: string } = {}) =>
    request<Signal[]>(`/api/signals?${qs(p)}`),
  watch: () => request<WatchStatus>("/api/watch"),
  dismissSignal: (id: number) => request<Signal>(`/api/signals/${id}/dismiss`, { method: "POST" }),
  restoreSignal: (id: number) => request<Signal>(`/api/signals/${id}/restore`, { method: "POST" }),
  markSeen: () => request<{ marked: number }>("/api/signals/seen", { method: "POST" }),
  scan: () => request<ScanResponse>("/api/scan", { method: "POST" }),

  risk: (b: { market: MarketId; symbol: string; entry: number; stop: number }) =>
    request<RiskResponse>("/api/risk", json("POST", b)),
  accounts: () => request<AccountDto[]>("/api/accounts"),
  updateAccount: (market: string, changes: Partial<Omit<AccountDto, "market" | "currency">>) =>
    request<AccountDto>(`/api/accounts/${market}`, json("PUT", changes)),
  day: () => request<DayResponse>("/api/day"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => request<Settings>("/api/settings", json("PUT", s)),

  aiStatus: () => request<AiStatus>("/api/ai/status"),
  aiProviders: () => request<AiProviderInfo[]>("/api/ai/providers"),
  aiModels: (provider: string) => request<AiModel[]>(`/api/ai/providers/${provider}/models`),
  explainSignal: (signalId: number, refresh = false) =>
    request<AiResult>("/api/ai/explain-signal", json("POST", { signal_id: signalId, refresh })),
  reviewJournal: (p: { market?: MarketId; symbol?: string }, refresh = false) =>
    request<AiResult>("/api/ai/review-journal", json("POST", { ...p, refresh })),
  aiAsk: (p: { question: string; market?: MarketId; symbol?: string; tf?: string }, refresh = false) =>
    request<AiResult>("/api/ai/ask", json("POST", { ...p, refresh })),

  journal: (p: { market?: MarketId; symbol?: string; mode?: JournalMode } = {}) =>
    request<JournalEntry[]>(`/api/journal?${qs(p)}`),
  positions: (mode: JournalMode = "real") => request<Position[]>(`/api/journal/positions?${qs({ mode })}`),
  deletedJournal: () => request<JournalEntry[]>("/api/journal/deleted"),
  restoreJournalEntry: (id: number) => request<{ restored: number }>(`/api/journal/${id}/restore`, { method: "POST" }),
  restoreJournalBackup: (payload: unknown) =>
    request<{ added: number; skipped: number }>("/api/journal/restore", json("POST", payload)),
  addJournal: (
    e: Omit<JournalEntry, "id" | "signal_id" | "planned_stop" | "reason" | "mode" | "uid" | "deleted_at" | "plan_uid"> & {
      mode?: JournalMode;
      plan_uid?: string | null;
      signal_id?: number | null;
      planned_stop?: number | null;
      reason?: string;
    },
  ) =>
    request<JournalEntry>("/api/journal", json("POST", e)),
  createPlan: (b: {
    market: MarketId;
    symbol: string;
    entry: number;
    stop: number;
    target?: number | null;
    reason: string;
    signal_id?: number | null;
  }) => request<PlanDto>("/api/plans", json("POST", b)),
  plans: (p: { market?: MarketId; symbol?: string } = {}) => request<PlanDto[]>(`/api/plans?${qs(p)}`),
  planReview: (id: number) => request<PlanReview>(`/api/plans/${id}/review`),
  removeJournal: (id: number) => request<void>(`/api/journal/${id}`, { method: "DELETE" }),
};
