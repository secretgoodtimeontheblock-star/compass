import type {
  BacktestResponse,
  CandlesResponse,
  Instrument,
  JournalEntry,
  Market,
  MarketId,
  Position,
  RiskResponse,
  ScanResponse,
  Settings,
  Signal,
  Strategy,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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
    throw new ApiError(detailText(body, `Ошибка ${res.status}`), res.status);
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
  backtest: (body: {
    market: MarketId;
    symbol: string;
    tf: string;
    strategy: string;
    params: Record<string, number>;
    limit: number;
    capital: number;
    fee_pct: number;
    slippage_pct: number;
  }) => request<BacktestResponse>("/api/backtest", json("POST", body)),

  signals: (p: { limit?: number; unseen?: boolean; market?: MarketId; symbol?: string } = {}) =>
    request<Signal[]>(`/api/signals?${qs(p)}`),
  markSeen: () => request<{ marked: number }>("/api/signals/seen", { method: "POST" }),
  scan: () => request<ScanResponse>("/api/scan", { method: "POST" }),

  risk: (b: { market: MarketId; symbol: string; entry: number; stop: number }) =>
    request<RiskResponse>("/api/risk", json("POST", b)),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => request<Settings>("/api/settings", json("PUT", s)),

  journal: (p: { market?: MarketId; symbol?: string } = {}) =>
    request<JournalEntry[]>(`/api/journal?${qs(p)}`),
  positions: () => request<Position[]>("/api/journal/positions"),
  addJournal: (e: Omit<JournalEntry, "id" | "signal_id"> & { signal_id?: number | null }) =>
    request<JournalEntry>("/api/journal", json("POST", e)),
  removeJournal: (id: number) => request<void>(`/api/journal/${id}`, { method: "DELETE" }),
};
