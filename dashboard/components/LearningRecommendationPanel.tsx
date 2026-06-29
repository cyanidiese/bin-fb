// dashboard/components/LearningRecommendationPanel.tsx
'use client'

import { useState } from 'react'
import type { Signal } from '@/lib/types'
import { formatPrice } from '@/lib/formatPrice'

interface Props {
  signal: Signal | null
  currentKlineClose: number
  onAccept: (signal: Signal) => void
  onReject: (signal: Signal, reason?: string) => void
  onPlaceCustom: (side: 'BUY' | 'SELL', entry: number, tp: number, sl: number) => void
}

type PanelState = 'idle' | 'reject-form' | 'custom-form'

export default function LearningRecommendationPanel({
  signal,
  currentKlineClose,
  onAccept,
  onReject,
  onPlaceCustom,
}: Props) {
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [rejectReason, setRejectReason] = useState('')
  const [customSide, setCustomSide] = useState<'BUY' | 'SELL'>('BUY')
  const [customEntry, setCustomEntry] = useState('')
  const [customTp, setCustomTp] = useState('')
  const [customSl, setCustomSl] = useState('')

  function handleAccept() {
    if (!signal) return
    onAccept(signal)
    setPanelState('idle')
  }

  function handleRejectSubmit() {
    if (!signal) return
    onReject(signal, rejectReason.trim() || undefined)
    setRejectReason('')
    setPanelState('idle')
  }

  function handleCustomSubmit() {
    const entry = Number(customEntry)
    const tp    = Number(customTp)
    const sl    = Number(customSl)
    if (!entry || !tp || !sl) return
    onPlaceCustom(customSide, entry, tp, sl)
    setCustomEntry('')
    setCustomTp('')
    setCustomSl('')
    setPanelState('idle')
  }

  function openCustomForm() {
    setCustomEntry(String(currentKlineClose))
    setPanelState('custom-form')
  }

  const cardCls = 'rounded-lg border border-gray-800 bg-gray-900 p-4 space-y-3'
  const labelCls = 'text-xs text-gray-500 uppercase tracking-wider'
  const inputCls = 'w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-amber-500'
  const btnPrimary = 'px-3 py-1.5 text-xs font-semibold rounded bg-emerald-700 text-white hover:bg-emerald-600 transition-colors'
  const btnDanger  = 'px-3 py-1.5 text-xs font-semibold rounded bg-red-900 text-white hover:bg-red-800 transition-colors border border-red-700'
  const btnNeutral = 'px-3 py-1.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  if (panelState === 'reject-form' && signal) {
    return (
      <div className={cardCls}>
        <p className={labelCls}>Reject reason (optional)</p>
        <textarea
          autoFocus
          value={rejectReason}
          onChange={e => setRejectReason(e.target.value)}
          placeholder="Why are you rejecting this signal? (leave blank to skip)"
          className={`${inputCls} resize-none h-16`}
        />
        <div className="flex gap-2">
          <button onClick={handleRejectSubmit} className={btnDanger}>Confirm Reject</button>
          <button onClick={() => setPanelState('idle')} className={btnNeutral}>Cancel</button>
        </div>
      </div>
    )
  }

  if (panelState === 'custom-form') {
    return (
      <div className={cardCls}>
        <p className={labelCls}>Place custom order</p>
        <div className="flex gap-2">
          {(['BUY', 'SELL'] as const).map(s => (
            <button
              key={s}
              onClick={() => setCustomSide(s)}
              className={`flex-1 py-1 text-xs font-semibold rounded border transition-colors ${
                customSide === s
                  ? s === 'BUY'
                    ? 'bg-emerald-800 border-emerald-600 text-white'
                    : 'bg-red-900 border-red-700 text-white'
                  : 'border-gray-700 text-gray-400 hover:text-white'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="space-y-1">
            <label className={labelCls}>Entry</label>
            <input type="number" value={customEntry} onChange={e => setCustomEntry(e.target.value)} className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>TP</label>
            <input type="number" value={customTp} onChange={e => setCustomTp(e.target.value)} className={inputCls} />
          </div>
          <div className="space-y-1">
            <label className={labelCls}>SL</label>
            <input type="number" value={customSl} onChange={e => setCustomSl(e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={handleCustomSubmit} className={btnPrimary}>Place Order</button>
          <button onClick={() => setPanelState('idle')} className={btnNeutral}>Cancel</button>
        </div>
      </div>
    )
  }

  // Default: signal display or no-signal state
  return (
    <div className={cardCls}>
      {signal ? (
        <>
          <div className="flex items-center gap-2">
            <p className={labelCls}>Bot Recommendation</p>
            <span className={`text-xs font-mono font-semibold px-1.5 py-0.5 rounded ${
              signal.side === 'BUY' ? 'bg-emerald-900 text-emerald-300' : 'bg-red-900 text-red-300'
            }`}>
              {signal.side}
            </span>
            <span className="text-xs text-gray-500 font-mono">{signal.signal_type}</span>
          </div>
          <div className="grid grid-cols-4 gap-3 text-xs font-mono">
            <div><span className="text-gray-500">Entry</span><br />{formatPrice(signal.entry)}</div>
            <div><span className="text-gray-500">TP</span><br />{formatPrice(signal.target)}</div>
            <div><span className="text-gray-500">SL</span><br />{signal.stop ? formatPrice(signal.stop) : '—'}</div>
            <div><span className="text-gray-500">RR</span><br />{signal.rr.toFixed(2)}x</div>
          </div>
          <div className="flex gap-2 flex-wrap">
            <button onClick={handleAccept} className={btnPrimary}>Accept ✓</button>
            <button onClick={() => setPanelState('reject-form')} className={btnDanger}>Reject ✗</button>
            <button onClick={openCustomForm} className={btnNeutral}>Place Custom</button>
          </div>
        </>
      ) : (
        <>
          <p className={labelCls}>Bot Recommendation</p>
          <p className="text-gray-600 text-sm">No signal this candle.</p>
          <button onClick={openCustomForm} className={btnNeutral}>Place Custom Order</button>
        </>
      )}
    </div>
  )
}
