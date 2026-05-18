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
  symbol_weights: Record<string, number>
  max_leverage_level: number
  use_allocation_weighting: boolean
  min_balance_pct: number
  scenario?: string
}

export interface PerSymbol {
  allocation_usdt: number
  leverage: number
  performance_score: number
}

export interface RiskState {
  generated_at: string
  mode: string
  balance: number
  peak_balance: number
  drawdown_pct: number
  warning_active: boolean
  hard_stop_active: boolean
  active_tier: BalanceTier
  last_event: string
  last_event_time: string
  per_symbol: Record<string, PerSymbol>
}
