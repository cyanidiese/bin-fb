// dashboard/lib/useLearningSession.ts
'use client'

import { useState, useRef, useCallback } from 'react'
import type { Signal, Kline } from '@/lib/types'
import type {
  LearningSession,
  LearningOrder,
  LearningEvent,
  TrendStateSnapshot,
  LearningSessionFile,
  CandleContext,
} from '@/lib/learningTypes'

function uuid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function computePnlPct(order: LearningOrder, closePrice: number): number {
  const sideSign = order.side === 'BUY' ? 1 : -1
  return ((closePrice - order.entryPrice) / order.entryPrice) * 100 * sideSign
}

export interface LearningSessionHandle {
  session: LearningSession | null
  isActive: boolean
  orders: LearningOrder[]
  startSession: (symbol: string, startCandleIndex: number) => void
  discardSession: () => void
  onReplayResult: (candleIndex: number, kline: Kline, trendState: TrendStateSnapshot) => void
  acceptSignal: (signal: Signal) => void
  rejectSignal: (signal: Signal, reason?: string) => void
  placeCustomOrder: (side: 'BUY' | 'SELL', entry: number, tp: number, sl: number, note?: string) => void
  closeOrderManually: (orderId: string, note?: string) => void
  addNote: (text: string) => void
  saveAndExit: () => void
}

export function useLearningSession(): LearningSessionHandle {
  const [session, setSession] = useState<LearningSession | null>(null)
  const [orders, setOrders] = useState<LearningOrder[]>([])

  // Ref mirrors orders state so callbacks that close over stale state can read current value
  const ordersRef = useRef<LearningOrder[]>([])
  ordersRef.current = orders

  // Ref mirrors session state so placeCustomOrder can read currentCandleIndex
  // without nesting setOrders inside a setSession updater (which runs twice in Strict Mode)
  const sessionRef = useRef<LearningSession | null>(null)
  sessionRef.current = session

  // Latest kline handed to onReplayResult. Notes and orders embed this so each event
  // is self-describing — a bare candle_index cannot be interpreted later without the
  // exact kline file the session was recorded against.
  const currentKlineRef = useRef<Kline | null>(null)

  function candleContext(): CandleContext | undefined {
    const k = currentKlineRef.current
    if (!k) return undefined
    return {
      time: new Date(k.time * 1000).toISOString(),
      open: k.open, high: k.high, low: k.low, close: k.close,
    }
  }

  const isActive = session !== null

  const startSession = useCallback((symbol: string, startCandleIndex: number) => {
    const newSession: LearningSession = {
      id: uuid(),
      symbol,
      startCandleIndex,
      currentCandleIndex: startCandleIndex,
      events: [],
      startedAt: new Date().toISOString(),
    }
    setSession(newSession)
    setOrders([])
  }, [])

  const discardSession = useCallback(() => {
    setSession(null)
    setOrders([])
  }, [])

  // Called from page.tsx after each successful replay API response in learning mode.
  // Evaluates open orders against the new kline, appends candle_advanced + any order_closed events.
  const onReplayResult = useCallback((candleIndex: number, kline: Kline, trendState: TrendStateSnapshot) => {
    const now = new Date().toISOString()
    currentKlineRef.current = kline
    const currentOrders = ordersRef.current

    type Closure = { orderId: string; outcome: 'tp_hit' | 'sl_hit'; closePrice: number; pnlPct: number }
    const closures: Closure[] = []

    const updatedOrders = currentOrders.map(order => {
      if (order.closedAtCandleIndex !== undefined) return order

      const isBuy = order.side === 'BUY'
      const tpHit = isBuy ? kline.high >= order.tpPrice : kline.low <= order.tpPrice
      const slHit = isBuy ? kline.low <= order.slPrice  : kline.high >= order.slPrice

      if (tpHit) {
        const closed = { ...order, closedAtCandleIndex: candleIndex, closePrice: order.tpPrice }
        closures.push({ orderId: order.id, outcome: 'tp_hit', closePrice: order.tpPrice, pnlPct: computePnlPct(order, order.tpPrice) })
        return closed
      }
      if (slHit) {
        const closed = { ...order, closedAtCandleIndex: candleIndex, closePrice: order.slPrice }
        closures.push({ orderId: order.id, outcome: 'sl_hit', closePrice: order.slPrice, pnlPct: computePnlPct(order, order.slPrice) })
        return closed
      }
      return order
    })

    setOrders(updatedOrders)
    ordersRef.current = updatedOrders

    const newEvents: LearningEvent[] = [
      { type: 'candle_advanced', candle_index: candleIndex, timestamp: now, trend_state: trendState },
      ...closures.map((c): LearningEvent => ({
        type: 'order_closed',
        order_id: c.orderId,
        candle_index: candleIndex,
        market_outcome: c.outcome,
        close_price: c.closePrice,
        pnl_pct: c.pnlPct,
        candle: {
          time: new Date(kline.time * 1000).toISOString(),
          open: kline.open, high: kline.high, low: kline.low, close: kline.close,
        },
        timestamp: now,
      })),
    ]

    setSession(prev => {
      if (!prev) return prev
      return { ...prev, currentCandleIndex: candleIndex, events: [...prev.events, ...newEvents] }
    })
  }, [])

  const acceptSignal = useCallback((signal: Signal) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'signal_accepted',
        candle_index: prev.currentCandleIndex,
        signal,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const rejectSignal = useCallback((signal: Signal, reason?: string) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'signal_rejected',
        candle_index: prev.currentCandleIndex,
        signal,
        reason,
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const placeCustomOrder = useCallback((side: 'BUY' | 'SELL', entry: number, tp: number, sl: number, note?: string) => {
    const prev = sessionRef.current
    if (!prev) return
    const order: LearningOrder = {
      id: uuid(),
      placedAtCandleIndex: prev.currentCandleIndex,
      side,
      entryPrice: entry,
      tpPrice: tp,
      slPrice: sl,
      ...(note ? { note } : {}),
    }
    const event: LearningEvent = {
      type: 'custom_order_placed',
      candle_index: prev.currentCandleIndex,
      order,
      candle: candleContext(),
      timestamp: new Date().toISOString(),
    }
    setOrders(o => [...o, order])
    ordersRef.current = [...ordersRef.current, order]
    setSession(s => s ? { ...s, events: [...s.events, event] } : s)
  }, [])

  /**
   * Close an open order by hand at the current candle's close, with an optional
   * rationale. TP/SL closes happen automatically in onReplayResult; this covers
   * "I would have got out here" for reasons the geometry does not capture.
   */
  const closeOrderManually = useCallback((orderId: string, note?: string) => {
    const prev = sessionRef.current
    const kline = currentKlineRef.current
    if (!prev || !kline) return
    const order = ordersRef.current.find(o => o.id === orderId)
    if (!order || order.closedAtCandleIndex !== undefined) return

    const closePrice = kline.close
    const closed = { ...order, closedAtCandleIndex: prev.currentCandleIndex, closePrice }
    const updated = ordersRef.current.map(o => (o.id === orderId ? closed : o))
    setOrders(updated)
    ordersRef.current = updated

    const event: LearningEvent = {
      type: 'order_closed',
      order_id: orderId,
      candle_index: prev.currentCandleIndex,
      market_outcome: 'manual_close',
      close_price: closePrice,
      pnl_pct: computePnlPct(order, closePrice),
      ...(note ? { note } : {}),
      candle: candleContext(),
      timestamp: new Date().toISOString(),
    }
    setSession(sPrev => (sPrev ? { ...sPrev, events: [...sPrev.events, event] } : sPrev))
  }, [])

  const addNote = useCallback((text: string) => {
    setSession(prev => {
      if (!prev) return prev
      const event: LearningEvent = {
        type: 'note_added',
        candle_index: prev.currentCandleIndex,
        text,
        candle: candleContext(),
        timestamp: new Date().toISOString(),
      }
      return { ...prev, events: [...prev.events, event] }
    })
  }, [])

  const saveAndExit = useCallback(() => {
    if (!session) return
    const file: LearningSessionFile = {
      ...session,
      ended_at: new Date().toISOString(),
    }
    const json = JSON.stringify(file, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const dateStr = new Date().toISOString().slice(0, 10)
    const idPrefix = session.id.slice(0, 8)
    const a = document.createElement('a')
    a.href = url
    a.download = `learning-${session.symbol}-${dateStr}-${idPrefix}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 100)
    setSession(null)
    setOrders([])
  }, [session])

  return {
    session,
    isActive,
    orders,
    startSession,
    discardSession,
    onReplayResult,
    acceptSignal,
    rejectSignal,
    placeCustomOrder,
    closeOrderManually,
    addNote,
    saveAndExit,
  }
}
