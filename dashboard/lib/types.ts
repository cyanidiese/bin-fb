// One trend level in the hierarchy (L1 = finest, L3 = coarsest).
// The bot promotes swing points upward through levels as structure forms.
export interface TrendLevel {
  level: number;               // 1 = fastest timeframe, higher = slower/broader
  direction: 'ASC' | 'DESC' | 'NONE'; // current trend direction at this level
  bos: number | null;          // Break of Structure price — the price that confirmed the current trend
  bos_since: string | null;    // ISO timestamp when the current BoS was established
  last_high: { price: number; time: string } | null; // most recent confirmed swing high
  last_low:  { price: number; time: string } | null; // most recent confirmed swing low
}

// A single confirmed swing point (high or low) at a given trend level.
export interface SwingPoint {
  time: string;          // ISO timestamp of the candle that closed at this swing
  level: number;         // trend hierarchy level this point belongs to
  type: 'high' | 'low'; // whether this is a swing high or swing low
  price: number;         // price value of the swing
  active: boolean;       // false if wiped from the live trend by a Break of Structure
}

// A trading signal emitted by the bot's strategy logic.
export interface Signal {
  level: number;         // trend level that triggered the signal
  side: 'BUY' | 'SELL';
  signal_type: string;   // human-readable strategy pattern name (e.g. "lowering_above_last_low")
  target: number;        // suggested take-profit price
  stop: number | null;   // suggested stop-loss price (null if not calculated)
}

// One 15-minute OHLCV candle exported from the bot's kline buffer.
export interface Kline {
  time: number;   // Unix timestamp in seconds (candle open time)
  open: number;
  high: number;
  low: number;
  close: number;
}

// ── Backtest types ─────────────────────────────────────────────────────────

export interface BacktestTrade {
  side: 'BUY' | 'SELL';
  level: number | null;
  signal_type: string;
  entry: number;
  tp: number;
  sl: number;
  partial_price: number | null;
  result: 'win' | 'partial' | 'trail' | 'loss';
  close_price: number;
  profit_pct: number;
  open_candle: number;
  close_candle: number | null;
  best_price: number;
  worst_price: number;
  max_tp_reach_pct: number;
  max_favorable_pct: number;
  max_adverse_pct: number;
}

export interface BacktestPreset {
  preset: string;
  total_trades: number;
  wins: number;
  partials: number;
  trails: number;
  losses: number;
  win_rate: number;
  total_profit_pct: number;
  avg_rr: number;
  max_consecutive_losses: number;
  total_profit_pts: number;
  potential_win_pts: number;
  potential_loss_pts: number;
  avg_max_tp_reach_pct: number;
  trades: BacktestTrade[];
  settings: Record<string, number | boolean>;
}

export interface BacktestResults {
  generated_at: string;
  symbol: string;
  timeframe: string;
  klines_file: string;
  total_klines: number;
  presets: Record<string, BacktestPreset>;
  locked_presets?: string[];
}

// ── Paper trading types ────────────────────────────────────────────────────

export interface PaperOpenOrder {
  side: 'BUY' | 'SELL';
  entry: number;
  tp: number;
  sl: number;
  partial_price: number | null;
  open_candle: number;
  best_price: number;
  worst_price: number;
  max_tp_reach_pct: number;
  unrealized_pct: number;
  armed: boolean;
}

export interface PaperPreset extends BacktestPreset {
  open_order: PaperOpenOrder | null;
}

export interface PaperResults {
  generated_at: string;
  started_at: string;
  symbol: string;
  timeframe: string;
  current_price: number;
  candle_index: number;
  presets: Record<string, PaperPreset>;
}

// ── Backtest API (single-preset run) ──────────────────────────────────────

export interface BacktestApiKline {
  index: number
  time: number
  open: number
  high: number
  low: number
  close: number
}

export interface BacktestApiResponse {
  klines: BacktestApiKline[]
  preset: string
  total_trades: number
  wins: number
  partials: number
  trails: number
  losses: number
  win_rate: number
  total_profit_pct: number
  avg_rr: number
  max_consecutive_losses: number
  total_profit_pts: number
  potential_win_pts: number
  potential_loss_pts: number
  avg_max_tp_reach_pct: number
  trades: BacktestTrade[]
}

// ── Strategy snapshot ──────────────────────────────────────────────────────

// The full snapshot written by bot/exporter.py after every candle close.
// The dashboard reads this file from /results.json on each page load.
export interface BotResults {
  symbol: string;           // e.g. "BTCUSDT"
  timeframe: string;        // e.g. "15m"
  mode: 'testnet' | 'live';
  generated_at: string;     // ISO timestamp of when this snapshot was written
  current_price: number;    // latest close price at export time
  trend_levels: TrendLevel[];
  all_points: SwingPoint[];  // all swing points across all levels, newest first
  klines: Kline[];           // last N candles (configured in exporter, default 200)
  signals: Signal[];         // active signals at export time
}

// ── Symbol config (written by bot at startup) ─────────────────────────────

export interface SymbolConfig {
  symbols: string[]
}

// ── Symbol Discovery ───────────────────────────────────────────────────────

export interface CandidateResult {
  symbol: string
  efficiency_score: number        // total_profit_pct_sum / total_order_count across fast presets
  total_net_profit: number        // sum of total_profit_pct across fast presets
  total_order_count: number
  profit_factor: number           // sum(pos_pcts) / sum(abs(neg_pcts))
  best_preset_id: string
  best_preset_profit_pct: number
  win_rate: number
  max_drawdown: number
  sharpe_ratio: number            // approx: mean(trade_pcts) / stdev(trade_pcts)
  baseline_efficiency: number
  vs_baseline_pct: number         // (efficiency / baseline - 1) × 100
  potential_gain_usdt: number     // (best_preset_profit_pct / 100) × position_size × leverage
}

export interface DiscoveryState {
  status: 'idle' | 'running' | 'complete' | 'cancelled' | 'error'
  pid: number | null
  total_precandidates: number
  processed_count: number
  in_progress: string[]
  last_run_timestamp: string | null
  error?: string
}

export interface DiscoveryCandidatesFile {
  generated_at: string
  candidates: CandidateResult[]
}

// ── Trades page types ──────────────────────────────────────────────────────

export interface RealOrder {
  preset_name: string;
  side: 'BUY' | 'SELL';
  entry_price: number;
  close_price: number;
  tp: number;
  sl: number;
  quantity: number;
  leverage: number;
  open_time: string | null;
  close_time: string;
  pnl_usdt: number;
  result: 'win' | 'loss' | 'partial' | 'trail' | 'closed_early';
}

export interface VirtualOrder {
  preset_name: string;
  side: 'BUY' | 'SELL';
  entry_price: number;
  tp: number;
  sl: number;
  quantity: number;
  leverage: number;
  open_time: string;
  status: 'open' | 'closed';
  close_price: number | null;
  close_time: string | null;
  pnl_usdt: number | null;
  result: 'win' | 'loss' | 'partial' | 'trail' | 'closed_early' | null;
}

export interface VirtualSummaryEntry {
  total_winning_usdt: number;
  trade_count: number;
}

export interface TradesData {
  symbol: string;
  mode: string;
  best_preset: string | null;
  real_orders: RealOrder[];
  virtual_summary: Record<string, VirtualSummaryEntry>;
  virtual_orders: VirtualOrder[];
}
