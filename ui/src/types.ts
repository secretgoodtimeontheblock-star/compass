// Зеркало DTO локального API (src/compass/api/app.py). Время везде — мс UTC.

export type MarketId = "moex" | "crypto";

export interface Market {
  id: MarketId;
  name: string;
  timeframes: string[];
}

export interface Instrument {
  market: MarketId;
  symbol: string;
  name: string;
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
  candles: Candle[];
}

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
}

export interface BacktestResponse {
  strategy: string;
  params: Record<string, number>;
  stale: boolean;
  metrics: BacktestMetrics;
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

export interface Settings {
  capital: number;
  risk_pct: number;
  scan_interval_min: number;
  tf_moex: string;
  tf_crypto: string;
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
