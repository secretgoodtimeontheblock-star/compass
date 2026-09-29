// Зеркало DTO локального API (src/compass/api/app.py). Время везде — мс UTC.

export type MarketId = "moex" | "crypto";

export interface Market {
  id: MarketId;
  name: string;
  timeframes: string[];
  source: string;
  delay_seconds: number | null;
  live_supported: boolean;
  instruments: number;
}

export interface Instrument {
  market: MarketId;
  symbol: string;
  name: string;
  kind?: string;
}

export interface Candle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface CandlesResponse {
  stale: boolean;
  source: string;
  fetched_at: number | null;
  candles: Candle[];
}

export type LiveState = "connecting" | "restoring" | "live" | "reconnecting" | "unavailable";

export interface Signal {
  id: number;
  market: MarketId;
  symbol: string;
  tf: string;
  strategy: string;
  side: "buy" | "exit";
  candle_ts: number;
  price: number;
  stop: number | null;
  created_at: number; // секунды
  seen: boolean;
}

export interface StrategyParam {
  name: string;
  label: string;
  default: number;
  min: number;
  max: number;
}

export interface Strategy {
  id: string;
  name: string;
  description: string;
  params: StrategyParam[];
}

export interface Trade {
  entry_ts: number;
  entry_price: number;
  exit_ts: number | null;
  exit_price: number | null;
  pnl_pct: number;
}

export interface BacktestMetrics {
  total_return_pct: number;
  buy_hold_return_pct: number;
  max_drawdown_pct: number;
  trades: number;
  open_trade: number;
  win_rate_pct: number | null;
  avg_trade_pct: number | null;
  profit_factor: number | null;
  exposure_pct: number;
  candles: number;
  sharpe: number | null;
  sortino: number | null;
  calmar: number | null;
  cagr_pct: number | null;
  max_consecutive_losses: number;
  payoff_ratio: number | null;
  best_trade_pct: number | null;
  worst_trade_pct: number | null;
}

export interface WalkForwardWindow {
  start: number;
  end: number;
  return_pct: number | null;
  buy_hold_pct: number | null;
  max_drawdown_pct: number | null;
  trades: number;
}

export interface WalkForward {
  windows: WalkForwardWindow[];
  profitable_windows: number;
  n_windows: number;
  return_std_pct: number | null;
}

export interface Resampling {
  trades: number;
  simulations: number;
  profitable_share_pct: number | null;
  return_p5_pct: number | null;
  return_p50_pct: number | null;
  return_p95_pct: number | null;
  drawdown_actual_pct: number | null;
  drawdown_shuffled_p95_pct: number | null;
  drawdown_bootstrap_p95_pct: number | null;
}

export interface RunCard {
  engine_version: string;
  candles: number;
  start_ts: number;
  end_ts: number;
  data_hash: string;
  created_at: string;
}

export interface BacktestResponse {
  strategy: string;
  params: Record<string, number>;
  stale: boolean;
  metrics: BacktestMetrics;
  warnings: string[];
  validation: { walk_forward: WalkForward | null; resampling: Resampling | null };
  run_card: RunCard;
  trades: Trade[];
  equity: { t: number; v: number }[];
}

export interface RiskResponse {
  qty: number;
  lots: number;
  cost: number;
  risk_amount: number;
  capped: boolean;
  lot_size: number;
}

export type AiProviderId = "off" | "cursor" | "claude" | "ollama";

export interface Settings {
  capital: number;
  risk_pct: number;
  scan_interval_min: number;
  tf_moex: string;
  tf_crypto: string;
  ai_provider: AiProviderId;
  ai_models: Partial<Record<AiProviderId, string>>;
  ai_consent: string;
  instrument_strategies: Record<string, { strategy: string; params: Record<string, number> }>;
}

export interface JournalDraft {
  market: MarketId;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  price: string;
  plannedStop: string;
  reason: string;
  note: string;
  signalId: number;
}

export interface AiStatus {
  provider: AiProviderId;
  model: string | null;
  cloud: boolean | null;
  available: boolean;
  reason: string;
  consent: boolean;
}

export interface AiProviderInfo {
  id: Exclude<AiProviderId, "off">;
  name: string;
  cloud: boolean;
  default_model: string;
}

export interface AiModel {
  id: string;
  label: string;
}

export interface AiResult {
  text: string;
  provider: string;
  model: string;
  cached: boolean;
  warnings: string[];
}

export interface JournalEntry {
  id: number;
  market: MarketId;
  symbol: string;
  side: "buy" | "sell";
  qty: number;
  price: number;
  ts: number;
  fee: number;
  note: string;
  signal_id: number | null;
  planned_stop: number | null;
  reason: string;
}

export interface Position {
  market: MarketId;
  symbol: string;
  qty: number;
  avg_price: number | null;
  realized_pnl: number;
  fees: number;
  trades: number;
}

export interface ScanResponse {
  new: Signal[];
  errors: string[];
}
