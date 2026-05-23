export interface BalanceTier {
  min_balance_usdt: number
  max_deploy_pct: number
  max_leverage_ceiling: number
}

export interface RiskConfig {
  balance_tiers: BalanceTier[]
  base_leverage: number
  max_leverage: number
  min_profit_factor: number
  drawdown_warning_pct: number
  drawdown_hard_stop_pct: number
  backtest_initial_balance_usdt: number
  backtest_klines: number
  bgf_top_n: number
  symbol_weights: Record<string, number>
  max_leverage_level: number
  use_allocation_weighting: boolean
  min_balance_pct: number
  scenario?: string
  symbol_leverage?: Record<string, number>
  weight_rebalancer?: WeightRebalancerConfig
}

export interface WeightRebalancerConfig {
  enabled: boolean
  rebalance_candles: number
  backtest_window_candles: number
  real_pnl_alpha: number
  blend_rate: number
  weight_floor_ratio: number
}

export interface WeightRebalanceSymbolEntry {
  backtest_pct: number
  real_pnl_usdt: number
  score: number
  old_weight: number
  new_weight: number
}

export interface WeightRebalanceLogEntry {
  ts: number
  symbols: Record<string, WeightRebalanceSymbolEntry>
}

export interface PerSymbol {
  allocation_usdt: number
  leverage: number
  performance_score: number | null  // null = no backtest data yet
}

export interface RiskState {
  generated_at: string
  mode: string
  balance: number
  peak_balance: number | null
  drawdown_pct: number
  warning_active: boolean
  hard_stop_active: boolean
  active_tier: BalanceTier
  last_event: string
  last_event_time: string
  per_symbol: Record<string, PerSymbol>
}
