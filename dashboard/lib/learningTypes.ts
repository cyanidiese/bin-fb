import type { Signal, TrendLevel, SwingPoint } from '@/lib/types'

export interface LearningOrder {
  id: string
  placedAtCandleIndex: number
  side: 'BUY' | 'SELL'
  entryPrice: number
  tpPrice: number
  slPrice: number
  closedAtCandleIndex?: number
  closePrice?: number
  /** Free-text rationale captured with the order ("why I'd take this"). */
  note?: string
}

/**
 * OHLC + timestamp of the candle an event happened on, embedded in the event itself.
 * Without this a note carries only a candle_index, which cannot be interpreted later
 * without also having the exact kline file the session was recorded against.
 */
export interface CandleContext {
  time: string      // ISO timestamp of the candle open
  open: number
  high: number
  low: number
  close: number
}

export interface TrendStateSnapshot {
  trend_levels: TrendLevel[]
  all_points: SwingPoint[]
}

export type LearningEvent =
  | { type: 'candle_advanced'; candle_index: number; timestamp: string; trend_state: TrendStateSnapshot }
  | { type: 'signal_accepted'; candle_index: number; signal: Signal; timestamp: string }
  | { type: 'signal_rejected'; candle_index: number; signal: Signal; reason?: string; timestamp: string }
  | { type: 'custom_order_placed'; candle_index: number; order: LearningOrder; candle?: CandleContext; timestamp: string }
  | { type: 'order_closed'; order_id: string; candle_index: number; market_outcome: 'tp_hit' | 'sl_hit'; pnl_pct: number; timestamp: string }
  | { type: 'note_added'; candle_index: number; text: string; candle?: CandleContext; timestamp: string }

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
