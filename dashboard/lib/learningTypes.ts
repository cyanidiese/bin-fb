import type { Signal, TrendLevel, SwingPoint, Kline } from '@/lib/types'

export interface LearningOrder {
  id: string
  placedAtCandleIndex: number
  side: 'BUY' | 'SELL'
  entryPrice: number
  tpPrice: number
  slPrice: number
  closedAtCandleIndex?: number
  closePrice?: number
}

export interface TrendStateSnapshot {
  trend_levels: TrendLevel[]
  all_points: SwingPoint[]
}

export type LearningEvent =
  | { type: 'candle_advanced'; candle_index: number; timestamp: string; trend_state: TrendStateSnapshot }
  | { type: 'signal_accepted'; candle_index: number; signal: Signal; timestamp: string }
  | { type: 'signal_rejected'; candle_index: number; signal: Signal; reason?: string; timestamp: string }
  | { type: 'custom_order_placed'; candle_index: number; order: LearningOrder; timestamp: string }
  | { type: 'order_closed'; order_id: string; candle_index: number; market_outcome: 'tp_hit' | 'sl_hit'; pnl_pct: number; timestamp: string }
  | { type: 'note_added'; candle_index: number; text: string; timestamp: string }

export interface LearningSession {
  id: string
  symbol: string
  startCandleIndex: number
  currentCandleIndex: number
  events: LearningEvent[]
  startedAt: string
}

// Shape written to the download JSON (endedAt added at serialization)
export interface LearningSessionFile extends LearningSession {
  ended_at: string
}
