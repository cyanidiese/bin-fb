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
