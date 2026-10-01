// Зеркало DTO локального API (src/compass/api/app.py). Время везде — мс UTC.

export type MarketId = "moex" | "crypto";

export interface Market {
  id: MarketId;
  name: string;
  timeframes: string[];
  source: string;
  delay_seconds: number | null;
  live_supported: boolean;
  live_kind?: "stream" | "poll" | null;
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

export interface DataIssue {
  code: string;
  severity: "error" | "warning";
  count: number;
  message: string;
}

export interface DataQuality {
  status: "ok" | "warning" | "error";
  candles: number;
  issues: DataIssue[];
}

export interface CandlesResponse {
  stale: boolean;
  quality?: DataQuality;
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
  status: "active" | "expired" | "acted" | "dismissed";
  expires_at: number; // мс
  delay_seconds: number;
  late: boolean;
  notified_at: number | null;
  dismissed_at: number | null;
}

export interface WatchInstrument {
  market: MarketId;
  symbol: string;
  paused: boolean;
  status: "ok" | "stale" | "error" | "short" | "paused" | "unknown";
  message: string;
  last_scan_at: number | null;
  last_ok_at: number | null;
}

export interface WatchStatus {
  background_scanner: boolean;
  interval_min: number;
  last_scan_at: number | null;
  next_scan_at: number | null;
  quiet_hours: { enabled: boolean; from: string; to: string; active_now: boolean };
  warnings: string[];
  signal_valid_bars: number;
  instruments: WatchInstrument[];
  notes: string[];
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
  exit_reason: "signal" | "stop" | "gap_stop" | "target" | "gap_target" | "eod" | "open";
  qty: number;
  stop: number | null;
  target: number | null;
  risk_amount: number | null;
  pnl_amount: number;
  mae_pct: number;
  mfe_pct: number;
  bars: number;
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
  avg_r: number | null;
  avg_mae_pct: number | null;
  avg_mfe_pct: number | null;
  stops: number;
  targets: number;
  gap_exits: number;
  ambiguous_bars: number;
  skipped_entries: number;
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
  strategy_version: string | null;
  assumptions: string[];
  rules: { stop_atr_mult: number | null; target_r: number | null; risk_pct: number | null } | null;
}

export interface BacktestResponse {
  strategy: string;
  params: Record<string, number>;
  stale: boolean;
  metrics: BacktestMetrics;
  validation: { walk_forward: WalkForward | null; resampling: Resampling | null };
  run_card: RunCard;
  data_quality: DataQuality;
  warnings: string[];
  intraday: {
    tf: string;
    trading_days: number;
    trades_per_day: number | null;
    round_trip_cost_pct: number;
    feed_delay_seconds: number;
    cost_stress: { multiplier: number; fee_pct: number; slippage_pct: number; return_pct: number; trades: number }[];
  } | null;
  coverage: { requested: number; candles: number; first_ts: number; last_ts: number; exhausted: boolean };
  execution: ExecutionBlock;
  integrity: IntegrityReport;
  weaknesses: Weakness[];
  trials: { prior_variants: number; this_run_variants: number; total_variants: number; warning: string | null };
  trades: Trade[];
  equity: { t: number; v: number }[];
}

export interface ExecutionBlock {
  model: {
    fee_pct: number;
    slippage_pct: number;
    spread_pct: number;
    max_participation_pct: number | null;
    round_trip_cost_pct: number;
  };
  cost_stress: { multiplier: number; fee_pct: number; slippage_pct: number; spread_pct: number; return_pct: number; trades: number }[];
  story: string;
  max_participation_pct: number | null;
}

export interface IntegrityReport {
  passed: boolean;
  summary: string;
  checks: { id: string; title: string; status: "pass" | "fail" | "info" | "skipped"; detail: string }[];
}

export interface Weakness {
  code: string;
  severity: "bad" | "warn" | "info";
  text: string;
  learn: string[];
}

export interface GlossaryTerm {
  id: string;
  term: string;
  short: string;
  why: string;
}

export interface RiskResponse {
  qty: number;
  lots: number;
  cost: number;
  risk_amount: number;
  risk_amount_worse: number;
  budget: number;
  capped: boolean;
  warning: string | null;
  warnings: string[];
  currency: string | null;
  lot_size: number;
  capital: number;
  risk_pct: number;
  portfolio: { heat_before_pct: number | null; heat_after_pct: number | null; warnings: string[] };
  fee_pct: number;
  slippage_pct: number;
  assumptions: string[];
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
  signal_valid_bars: number;
  quiet_hours: { enabled: boolean; from: string; to: string };
  paused_instruments: string[];
  experience_level: ExperienceLevel;
}

export type ExperienceLevel = "beginner" | "trader" | "researcher";

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
  planUid?: string;
  planId?: number;
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
  mode: JournalMode;
  uid: string;
  deleted_at: number | null;
  plan_uid: string | null;
}

export type JournalMode = "real" | "paper" | "historical";

export interface Position {
  market: MarketId;
  symbol: string;
  qty: number;
  avg_price: number | null;
  realized_pnl: number;
  fees: number;
  trades: number;
  mode: JournalMode;
}

export interface ScanResponse {
  new: Signal[];
  errors: string[];
}

export interface PlanDto {
  id: number;
  uid: string;
  created_at: number;
  market: MarketId;
  symbol: string;
  source: string;
  strategy: string | null;
  strategy_version: string | null;
  params: Record<string, number> | null;
  signal_id: number | null;
  entry: number;
  stop: number;
  target: number | null;
  qty: number;
  lots: number;
  cost: number;
  risk_amount: number;
  risk_amount_worse: number;
  budget: number;
  currency: string | null;
  reward_risk: number | null;
  reason: string;
  warnings: string[];
  assumptions?: string[];
  capital: number;
  risk_pct: number;
}

export interface PlanDeviation {
  code: "entry" | "qty" | "stop";
  label: string;
  planned: number;
  actual: number | null;
  diff_pct: number | null;
  worse: boolean;
}

export interface PlanReview {
  plan_id: number;
  status: "open" | "entered" | "closed";
  bought_qty: number;
  sold_qty: number;
  fees: number;
  entries: number;
  deviations: PlanDeviation[];
  avg_entry?: number;
  avg_exit?: number;
  actual_risk_at_stop?: number;
  risk_budget?: number;
  risk_exceeded?: boolean;
  result_pct?: number;
  r_multiple?: number | null;
  notes?: string[];
}

export interface BacktestRequestBody {
  market: MarketId;
  symbol: string;
  tf: string;
  strategy: string;
  params: Record<string, number>;
  limit: number;
  capital: number;
  fee_pct: number;
  slippage_pct: number;
  spread_pct?: number;
  max_volume_pct?: number;
  stop_atr_mult?: number;
  target_r?: number;
  risk_pct?: number;
  close_eod?: boolean;
}

export interface OosSegment {
  total_return_pct: number;
  buy_hold_return_pct: number;
  max_drawdown_pct: number;
  trades: number;
  win_rate_pct: number | null;
  profit_factor: number | null;
}

export interface OosResponse {
  split: {
    train_pct: number;
    train_candles: number;
    test_candles: number;
    train_from: number;
    train_to: number;
    test_from: number;
    test_to: number;
  };
  optimization: {
    enabled: boolean;
    objective: string;
    variants: number;
    dropped_invalid: number;
    eligible: number;
    top: { params: Record<string, number>; score: number; return_pct: number; trades: number }[];
  };
  chosen: { params: Record<string, number>; train: OosSegment; test: OosSegment; train_score: number | null } | null;
  baseline: { params: Record<string, number>; train: OosSegment; test: OosSegment } | null;
  sensitivity: {
    param: string;
    value: number;
    train_score: number | null;
    train_return_pct: number;
    test_return_pct: number;
    test_trades: number;
  }[];
  cost_sensitivity: { multiplier: number; fee_pct: number; slippage_pct: number; return_pct: number; trades: number }[];
  verdict: { status: "held" | "degraded" | "inconclusive"; reasons: string[] };
  notes: string[];
  strategy: string;
  strategy_version: string;
  warnings: string[];
  trials: { prior_variants: number; this_run_variants: number; total_variants: number; warning: string | null };
}

export interface AccountDto {
  market: MarketId;
  name: string;
  currency: string;
  capital: number | null;
  risk_pct: number;
  daily_loss_limit_pct: number;
  max_open_risk_pct: number;
  max_trades_per_day: number | null;
}

export interface AccountPosition {
  symbol: string;
  qty: number;
  avg_price: number;
  cost: number;
  stop: number | null;
  risk_at_stop: number | null;
  risk: number | null;
  risk_basis: "mark" | "cost";
  mark_price: number | null;
  mark_status: "fresh" | "stale" | "missing";
  mark_age_s: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  unrealized_pct: number | null;
  stop_breached: boolean;
  weight_pct: number | null;
}

export interface PortfolioTruth {
  status: "ok" | "partial" | "unmarked";
  sources: {
    journal: { entries: number; last_entry_ts: number | null };
    market: { marked: number; stale: number; missing: number };
    broker: { connected: boolean };
  };
  reconciliation: { status: "not_connected" | "match" | "mismatch"; differences: { symbol: string; kind: string; message: string }[] };
}

export interface AccountSnapshot {
  market: MarketId;
  name: string;
  currency: string;
  capital: number | null;
  equity: number | null;
  equity_complete: boolean;
  free: number | null;
  realized_total: number;
  unrealized_total: number;
  exposure_cost: number;
  truth: PortfolioTruth;
  daily_pnl: number;
  daily_limit: number | null;
  daily_limit_breached: boolean;
  exposure: number;
  exposure_pct: number | null;
  open_risk: number;
  heat_pct: number | null;
  max_open_risk_pct: number;
  positions: AccountPosition[];
  unprotected: string[];
  warnings: string[];
}

export interface DayResponse {
  accounts: AccountSnapshot[];
  unseen_signals: number;
  problem_sources: { market: MarketId; symbol: string; status: string; message: string }[];
  notes: string[];
}

export interface ReplayFill {
  ts: number;
  side: "buy" | "sell";
  qty: number;
  price: number;
  fee: number;
  reason: string;
  stop: number | null;
  risk_pct: number | null;
}

export interface ReplayResult {
  equity: number;
  return_pct: number;
  buy_hold_pct: number;
  max_drawdown_pct: number;
  fills: number;
  closed_trades: number;
  win_rate_pct: number | null;
  realized: number;
  fees: number;
  entries_without_stop: number;
  max_entry_risk_pct: number;
  note: string | null;
}

export interface ReplaySession {
  id: number;
  market: MarketId;
  symbol: string;
  tf: string;
  source: string;
  status: "active" | "finished";
  capital: number;
  cursor_ts: number;
  replayed: number;
  remaining: number;
  last_close: number;
  cash: number;
  qty: number;
  avg_price: number | null;
  stop: number | null;
  pending: { side: "buy" | "sell"; qty: number; stop: number | null } | null;
  fills: ReplayFill[];
  result: ReplayResult;
  notes: string[];
  events?: string[];
}

export interface LevelDto {
  id: number;
  uid: string;
  market: MarketId;
  symbol: string;
  price: number;
  label: string;
  created_at: number;
}

export interface ScreenerRow {
  market: MarketId;
  symbol: string;
  name: string;
  tf: string;
  paused: boolean;
  status: "ok" | "stale" | "error" | "short";
  message: string;
  signals: { strategy: string; side: "buy" | "exit"; id: number }[];
  last?: number;
  change_bar_pct?: number | null;
  change_20_pct?: number | null;
  above_sma20?: boolean | null;
  above_sma50?: boolean | null;
  rsi14?: number | null;
  atr_pct?: number | null;
  volume_ratio?: number | null;
  nearest_level?: { price: number; distance_pct: number } | null;
  rule_state?: Record<string, number>;
}

export interface WeekAccount {
  market: MarketId;
  name: string;
  currency: string;
  days: number;
  closed_trades: number;
  entries: number;
  fees: number;
  realized: number;
  win_rate_pct: number | null;
  best: { symbol: string; pnl: number } | null;
  worst: { symbol: string; pnl: number } | null;
  entries_without_stop: number;
  entries_with_plan: number;
  entries_without_plan: number;
  plan_deviations: { plans_reviewed: number; worse_entry: number; bigger_qty: number; risk_exceeded: number; no_stop: number };
  days_over_daily_limit: string[];
  days_over_trades_limit: string[];
  max_trades_per_day: number | null;
  notes: string[];
}


export interface CockpitQuestion {
  id: "now" | "positions" | "risk" | "plans" | "signals" | "rules" | "data";
  question: string;
  answer: string;
  status: "ok" | "attention" | "bad";
  items: Record<string, unknown>[];
}

export interface CockpitAttention {
  severity: "stop" | "bad" | "attention" | "info";
  section: string;
  text: string;
  hint: string;
  code: string;
  learn: string[];
}

export interface CockpitResponse {
  generated_at: number;
  status: "ok" | "attention" | "stop";
  headline: string;
  questions: CockpitQuestion[];
  attention: CockpitAttention[];
  notes: string[];
}

export interface LabFinding {
  code: string;
  severity: "good" | "warn" | "bad";
  title: string;
  text: string;
  learn: string[];
}

export interface LabFold {
  index: number;
  train_from: number | null;
  train_to: number | null;
  test_from: number;
  test_to: number;
  params: Record<string, number> | null;
  train_score: number | null;
  train_return_pct: number | null;
  test_return_pct: number;
  test_trades: number;
  test_max_drawdown_pct: number;
  buy_hold_pct: number | null;
  status: "ok" | "no_choice";
}

export interface LabCell {
  valid: boolean;
  return_pct?: number;
  max_drawdown_pct?: number;
  trades?: number;
  score?: number | null;
  thin?: boolean;
}

export interface LabResponse {
  verdict: {
    status: "robust" | "mixed" | "fragile" | "insufficient";
    headline: string;
    findings: LabFinding[];
    disclaimer: string;
  };
  walk_forward: {
    summary: {
      mode: string;
      optimized: boolean;
      folds: number;
      usable_folds: number;
      profitable_folds: number;
      stitched_return_pct: number | null;
      mean_return_pct: number | null;
      worst_fold_pct: number | null;
      trades: number;
      param_consistency: number | null;
      efficiency: number | null;
    };
    folds: LabFold[];
  };
  parameter_map: {
    available: boolean;
    reason?: string;
    params?: string[];
    axes?: number[][];
    matrix?: LabCell[][];
    variants?: number;
    positive_share_pct?: number | null;
    stability?: { verdict: string; text: string };
  };
  robustness: {
    bootstrap: { trades: number; mean_trade_pct: number; mean_p5_pct: number; mean_p95_pct: number; prob_no_edge_pct: number } | null;
    concentration: { top3_profit_share_pct: number | null; return_without_best_pct: number; total_return_pct: number } | null;
    drawdown: {
      max_drawdown_pct: number;
      peak_to_trough_days: number;
      recovered: boolean;
      recovery_days: number | null;
      longest_underwater_days: number;
      current_drawdown_pct: number;
    } | null;
    tail: { var95_bar_pct: number; cvar95_bar_pct: number | null; worst_bar_pct: number } | null;
    regimes: { regimes: { id: string; label: string; bars: number; return_pct: number | null; buy_hold_pct: number | null; thin: boolean }[]; dominant: string | null } | null;
    cost_stress: { multiplier: number; fee_pct: number; slippage_pct: number; spread_pct: number; return_pct: number; trades: number }[];
  };
  multiple_testing: {
    variants_this_run: number;
    prior_variants: number;
    pbo: { available: boolean; value: number | null; reason?: string };
    dsr: { available: boolean; value: number | null; trials?: number };
  };
  research: Record<string, number | null> | null;
  notes: string[];
  warnings: string[];
  trials: { prior_variants: number; this_run_variants: number; total_variants: number; warning: string | null };
}

export interface ChangedResponse {
  hours: number;
  empty: boolean;
  text: string;
}

export interface ViolationsResponse {
  violations: string[];
  status: string;
  text: string;
}
