// dashboard/components/LearningRecommendationPanel.tsx
'use client'

import { useState, useImperativeHandle } from 'react'
import type { Signal } from '@/lib/types'
import type { LearningOrder, LearningEvent } from '@/lib/learningTypes'
import { useLocalStorage } from '@/lib/useLocalStorage'
import { formatPrice, priceToInputValue } from '@/lib/formatPrice'

export interface LearningPanelHandle {
  /** Fill the focused custom-order field with a price picked off the chart.
   *  No-op when the custom form is closed or no field has focus. */
  applyChartPrice: (price: number) => void
}

interface Props {
  signal: Signal | null
  currentKlineClose: number
  onAccept: (signal: Signal) => void
  onReject: (signal: Signal, reason?: string) => void
  onPlaceCustom: (side: 'BUY' | 'SELL', entry: number, tp: number, sl: number, note?: string) => void
  /** Imperative handle so the page can push a chart-clicked price into the focused field. */
  ref?: React.Ref<LearningPanelHandle>
  /** Reports which custom-order field has focus (null when none) so the page can
   *  switch the chart into price-capture mode. */
  onCaptureFieldChange?: (field: 'entry' | 'tp' | 'sl' | null) => void
  /** All learning orders; the panel lists the still-open ones so they can be closed by hand. */
  orders?: LearningOrder[]
  onCloseOrder?: (orderId: string, note?: string) => void
  /** Record a standalone note on the current candle without placing an order.
   *  Same event as the floating "+ Note" button — this is just a second, in-context
   *  entry point next to the order controls. */
  onAddNote?: (text: string) => void
  /** Session events, used to render the collapsible log of notes and orders. */
  events?: LearningEvent[]
}

type PanelState = 'idle' | 'reject-form' | 'custom-form' | 'note-form'

export default function LearningRecommendationPanel({
  signal,
  currentKlineClose,
  onAccept,
  onReject,
  onPlaceCustom,
  ref,
  onCaptureFieldChange,
  orders,
  onCloseOrder,
  onAddNote,
  events,
}: Props) {
  const [panelState, setPanelState] = useState<PanelState>('idle')
  const [rejectReason, setRejectReason] = useState('')
  const [customSide, setCustomSide] = useState<'BUY' | 'SELL'>('BUY')
  const [customEntry, setCustomEntry] = useState('')
  const [customTp, setCustomTp] = useState('')
  const [customSl, setCustomSl] = useState('')
  const [customNote, setCustomNote] = useState('')
  const [closingOrderId, setClosingOrderId] = useState<string | null>(null)
  const [closeNote, setCloseNote] = useState('')
  const [standaloneNote, setStandaloneNote] = useState('')
  // Collapsed by default; the preference persists like the page's other sections.
  const [logOpen, setLogOpen] = useLocalStorage<boolean>('db:learning:logOpen', false)

  // Which custom-order field is focused. The page mirrors this to the chart so a
  // click can be routed into the right input.
  const [captureField, setCaptureField] = useState<'entry' | 'tp' | 'sl' | null>(null)

  function focusField(f: 'entry' | 'tp' | 'sl') {
    setCaptureField(f)
    onCaptureFieldChange?.(f)
  }
  function blurField() {
    // Deliberately does NOT clear captureField: clicking the chart blurs the input,
    // and clearing here would drop the target before the click is delivered. The
    // field is cleared when the form closes or another field takes focus.
  }

  // Exposed to the page so a chart click can fill the focused field. Event-driven
  // rather than prop+effect: setting state in an effect in response to a changing
  // prop causes cascading renders (react-hooks/set-state-in-effect).
  useImperativeHandle(ref, () => ({
    applyChartPrice(price: number) {
      if (panelState !== 'custom-form' || !captureField) return
      const v = priceToInputValue(price)
      if (captureField === 'entry') setCustomEntry(v)
      else if (captureField === 'tp') setCustomTp(v)
      else setCustomSl(v)
    },
  }), [panelState, captureField])

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
    onPlaceCustom(customSide, entry, tp, sl, customNote.trim() || undefined)
    setCustomEntry('')
    setCustomTp('')
    setCustomSl('')
    setCustomNote('')
    setCaptureField(null)
    onCaptureFieldChange?.(null)
    setPanelState('idle')
  }

  function openCustomForm() {
    setCustomEntry(String(currentKlineClose))
    setPanelState('custom-form')
  }

  function openNoteForm() {
    setStandaloneNote('')
    setPanelState('note-form')
  }

  function handleNoteSubmit() {
    const t = standaloneNote.trim()
    if (t) onAddNote?.(t)
    setStandaloneNote('')
    setPanelState('idle')
  }

  function closeCustomForm() {
    setCustomNote('')
    setCaptureField(null)
    onCaptureFieldChange?.(null)
    setPanelState('idle')
  }

  const cardCls = 'rounded-lg border border-gray-800 bg-gray-900 p-4 space-y-3'
  const labelCls = 'text-xs text-gray-500 uppercase tracking-wider'
  const inputCls = 'w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white focus:outline-none focus:border-amber-500'
  const btnPrimary = 'px-3 py-1.5 text-xs font-semibold rounded bg-emerald-700 text-white hover:bg-emerald-600 transition-colors'
  const btnDanger  = 'px-3 py-1.5 text-xs font-semibold rounded bg-red-900 text-white hover:bg-red-800 transition-colors border border-red-700'
  const btnNeutral = 'px-3 py-1.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  // ── Session log: notes + orders, collapsed by default ────────────────────
  // Built from the event stream rather than the orders array so notes and orders
  // interleave in the order they actually happened. Close outcomes are folded onto
  // their originating order row instead of appearing as separate lines.
  type LogRow = {
    key: string
    time: string
    kind: 'order' | 'note'
    side?: 'BUY' | 'SELL'
    entry?: number
    tp?: number
    sl?: number
    text?: string
    outcome?: 'tp_hit' | 'sl_hit' | 'manual_close'
    pnlPct?: number
    closeNote?: string
  }

  const logRows: LogRow[] = (() => {
    const rows: LogRow[] = []
    const byOrderId = new Map<string, LogRow>()
    for (const e of events ?? []) {
      if (e.type === 'custom_order_placed') {
        const row: LogRow = {
          key: e.order.id,
          time: e.candle?.time ?? e.timestamp,
          kind: 'order',
          side: e.order.side,
          entry: e.order.entryPrice,
          tp: e.order.tpPrice,
          sl: e.order.slPrice,
          text: e.order.note,
        }
        byOrderId.set(e.order.id, row)
        rows.push(row)
      } else if (e.type === 'note_added') {
        rows.push({
          key: `n${rows.length}-${e.timestamp}`,
          time: e.candle?.time ?? e.timestamp,
          kind: 'note',
          text: e.text,
        })
      } else if (e.type === 'order_closed') {
        const row = byOrderId.get(e.order_id)
        if (row) {
          row.outcome = e.market_outcome
          row.pnlPct = e.pnl_pct
          row.closeNote = e.note
        }
      }
    }
    return rows
  })()

  const outcomeLabel = (o?: LogRow['outcome']) =>
    o === 'tp_hit' ? 'TP' : o === 'sl_hit' ? 'SL' : o === 'manual_close' ? 'manual' : 'open'

  const sessionLog = (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-3 space-y-2">
      <button
        onClick={() => setLogOpen(v => !v)}
        className="w-full flex items-center justify-between text-xs text-gray-500 uppercase tracking-wider hover:text-gray-300 transition-colors"
      >
        <span>Session log ({logRows.length})</span>
        <span className="text-gray-600">{logOpen ? '▾' : '▸'}</span>
      </button>

      {logOpen && (
        logRows.length === 0 ? (
          <p className="text-xs text-gray-600">Nothing recorded yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-gray-600 text-left">
                  <th className="py-1 pr-2 font-normal">Candle</th>
                  <th className="py-1 pr-2 font-normal">Type</th>
                  <th className="py-1 pr-2 font-normal">Detail</th>
                  <th className="py-1 font-normal">Note</th>
                </tr>
              </thead>
              <tbody>
                {logRows.map(r => (
                  <tr key={r.key} className="border-t border-gray-800 align-top">
                    <td className="py-1 pr-2 text-gray-500 whitespace-nowrap">
                      {new Date(r.time).toLocaleString(undefined, {
                        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                      })}
                    </td>
                    <td className="py-1 pr-2 whitespace-nowrap">
                      {r.kind === 'note' ? (
                        <span className="text-amber-500">NOTE</span>
                      ) : (
                        <span className={r.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}>{r.side}</span>
                      )}
                    </td>
                    <td className="py-1 pr-2 text-gray-400 whitespace-nowrap">
                      {r.kind === 'order' ? (
                        <>
                          {formatPrice(r.entry!)}
                          <span className="text-gray-600"> tp </span>{formatPrice(r.tp!)}
                          <span className="text-gray-600"> sl </span>{formatPrice(r.sl!)}
                          <span className={
                            r.outcome === undefined ? ' text-gray-500'
                            : (r.pnlPct ?? 0) >= 0 ? ' text-emerald-400' : ' text-red-400'
                          }>
                            {' · '}{outcomeLabel(r.outcome)}
                            {r.pnlPct !== undefined && ` ${r.pnlPct >= 0 ? '+' : ''}${r.pnlPct.toFixed(2)}%`}
                          </span>
                        </>
                      ) : '—'}
                    </td>
                    <td className="py-1 text-gray-300 whitespace-pre-wrap break-words">
                      {[r.text, r.closeNote && `exit: ${r.closeNote}`].filter(Boolean).join('\n') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  )

  const openOrders = (orders ?? []).filter(o => o.closedAtCandleIndex === undefined)

  // Rendered above every panel state so an open position can always be closed,
  // including while the custom-order or reject form is on screen.
  const openOrdersBlock = openOrders.length > 0 ? (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-3 space-y-2">
      <p className={labelCls}>Open orders ({openOrders.length})</p>
      {openOrders.map(o => (
        <div key={o.id} className="space-y-1">
          <div className="flex items-center gap-2 text-xs font-mono">
            <span className={o.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}>{o.side}</span>
            <span className="text-gray-500">entry</span><span>{formatPrice(o.entryPrice)}</span>
            <span className="text-gray-500">tp</span><span>{formatPrice(o.tpPrice)}</span>
            <span className="text-gray-500">sl</span><span>{formatPrice(o.slPrice)}</span>
            {closingOrderId !== o.id && (
              <button
                onClick={() => { setClosingOrderId(o.id); setCloseNote('') }}
                className="ml-auto px-2 py-0.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors"
              >
                Close
              </button>
            )}
          </div>
          {closingOrderId === o.id && (
            <div className="space-y-2 pl-1 border-l border-gray-700">
              <p className="text-xs text-gray-600">
                Closes at this candle&apos;s close ({formatPrice(currentKlineClose)}).
              </p>
              <textarea
                autoFocus
                value={closeNote}
                onChange={e => setCloseNote(e.target.value)}
                placeholder="Why close here? (optional)"
                className={`${inputCls} resize-none h-14`}
              />
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    onCloseOrder?.(o.id, closeNote.trim() || undefined)
                    setClosingOrderId(null)
                    setCloseNote('')
                  }}
                  className={btnDanger}
                >
                  Confirm Close
                </button>
                <button onClick={() => { setClosingOrderId(null); setCloseNote('') }} className={btnNeutral}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  ) : null

  if (panelState === 'reject-form' && signal) {
    return (
      <>
        {openOrdersBlock}
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
        {sessionLog}
      </>
    )
  }

  if (panelState === 'note-form') {
    return (
      <>
        {openOrdersBlock}
        <div className={cardCls}>
          <p className={labelCls}>Note on this candle</p>
          <p className="text-xs text-gray-600">
            Recorded against the current candle. No order is placed.
          </p>
          <textarea
            autoFocus
            value={standaloneNote}
            onChange={e => setStandaloneNote(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleNoteSubmit() }}
            placeholder="What are you seeing here? Why no trade?"
            className={`${inputCls} resize-none h-20`}
          />
          <div className="flex gap-2">
            <button onClick={handleNoteSubmit} className={btnPrimary}>Save Note</button>
            <button onClick={() => { setStandaloneNote(''); setPanelState('idle') }} className={btnNeutral}>Cancel</button>
          </div>
        </div>
        {sessionLog}
      </>
    )
  }

  if (panelState === 'custom-form') {
    return (
      <>
        {openOrdersBlock}
        <div className={cardCls}>
          <p className={labelCls}>Place custom order</p>
          <p className="text-xs text-gray-600">
            Focus a field, then click the chart to fill it with the price at your cursor.
          </p>
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
              <input type="number" value={customEntry} onChange={e => setCustomEntry(e.target.value)}
                     onFocus={() => focusField('entry')} onBlur={blurField}
                     className={`${inputCls} ${captureField === 'entry' ? 'border-amber-500 ring-1 ring-amber-500/40' : ''}`} />
            </div>
            <div className="space-y-1">
              <label className={labelCls}>TP</label>
              <input type="number" value={customTp} onChange={e => setCustomTp(e.target.value)}
                     onFocus={() => focusField('tp')} onBlur={blurField}
                     className={`${inputCls} ${captureField === 'tp' ? 'border-amber-500 ring-1 ring-amber-500/40' : ''}`} />
            </div>
            <div className="space-y-1">
              <label className={labelCls}>SL</label>
              <input type="number" value={customSl} onChange={e => setCustomSl(e.target.value)}
                     onFocus={() => focusField('sl')} onBlur={blurField}
                     className={`${inputCls} ${captureField === 'sl' ? 'border-amber-500 ring-1 ring-amber-500/40' : ''}`} />
            </div>
          </div>
          <div className="space-y-1">
            <label className={labelCls}>Note (optional)</label>
            <textarea
              value={customNote}
              onChange={e => setCustomNote(e.target.value)}
              placeholder="Why this entry? What are you seeing on the chart?"
              className={`${inputCls} resize-none h-16`}
            />
          </div>
          <div className="flex gap-2">
            <button onClick={handleCustomSubmit} className={btnPrimary}>Place Order</button>
            <button onClick={closeCustomForm} className={btnNeutral}>Cancel</button>
          </div>
        </div>
        {sessionLog}
      </>
    )
  }

  // Default: signal display or no-signal state
  return (
    <>
      {openOrdersBlock}
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
              <div><span className="text-gray-500">RR</span><br />{signal.rr != null ? `${signal.rr.toFixed(2)}x` : '—'}</div>
            </div>
            <div className="flex gap-2 flex-wrap">
              <button onClick={handleAccept} className={btnPrimary}>Accept ✓</button>
              <button onClick={() => setPanelState('reject-form')} className={btnDanger}>Reject ✗</button>
              <button onClick={openCustomForm} className={btnNeutral}>Place Custom</button>
              <button onClick={openNoteForm} className={btnNeutral}>Place Custom Note</button>
            </div>
          </>
        ) : (
          <>
            <p className={labelCls}>Bot Recommendation</p>
            <p className="text-gray-600 text-sm">No signal this candle.</p>
            <div className="flex gap-2 flex-wrap">
              <button onClick={openCustomForm} className={btnNeutral}>Place Custom Order</button>
              <button onClick={openNoteForm} className={btnNeutral}>Place Custom Note</button>
            </div>
          </>
        )}
      </div>
      {sessionLog}
    </>
  )
}
