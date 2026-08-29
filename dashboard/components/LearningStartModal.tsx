// dashboard/components/LearningStartModal.tsx
'use client'

import { useState, useEffect } from 'react'
import type { LearningSession } from '@/lib/learningTypes'
import type { Kline } from '@/lib/types'
import { tsToDatetimeLocal, snapTo15Min, datetimeLocalToCandleIndex } from '@/lib/datetime'

interface Props {
  isOpen: boolean
  currentCandleIndex: number
  klines: Kline[]
  existingSession: LearningSession | null
  onStart: (startCandleIndex: number) => void
  onResume: () => void
  onDiscard: () => void
  onClose: () => void
}

export default function LearningStartModal({
  isOpen,
  currentCandleIndex,
  klines,
  existingSession,
  onStart,
  onResume,
  onDiscard,
  onClose,
}: Props) {
  const totalCandles = klines.length
  const defaultDate =
    totalCandles > 0 && currentCandleIndex >= 0 && currentCandleIndex < totalCandles
      ? tsToDatetimeLocal(klines[currentCandleIndex].time)
      : ''

  const [startDate, setStartDate] = useState(defaultDate)

  // Re-seed the picker whenever the scrubber moves or a new symbol's klines load.
  useEffect(() => {
    setStartDate(defaultDate)
  }, [defaultDate])

  if (!isOpen) return null

  const minDate = totalCandles > 0 ? tsToDatetimeLocal(klines[0].time) : ''
  const maxDate = totalCandles > 0 ? tsToDatetimeLocal(klines[totalCandles - 1].time) : ''

  // Resolved candle for the chosen datetime — shown back to the user so it is
  // obvious which candle the session will actually open on.
  const resolvedIdx = datetimeLocalToCandleIndex(startDate, klines)
  const resolvedKline = resolvedIdx >= 0 ? klines[resolvedIdx] : null
  const remaining = resolvedIdx >= 0 ? totalCandles - 1 - resolvedIdx : 0

  const overlayClass =
    'fixed inset-0 z-50 flex items-center justify-center bg-black/60'

  const cardClass =
    'bg-gray-900 border border-gray-700 rounded-lg p-6 w-96 space-y-4 shadow-xl'

  const btnPrimary =
    'w-full px-4 py-2 text-sm font-semibold rounded bg-amber-500 text-black hover:bg-amber-400 transition-colors disabled:opacity-40 disabled:cursor-not-allowed'

  const btnSecondary =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  const btnDanger =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-red-700 text-red-400 hover:text-red-300 hover:border-red-500 transition-colors'

  if (existingSession) {
    const startedKline = klines[existingSession.startCandleIndex]
    return (
      <div className={overlayClass} onClick={onClose}>
        <div className={cardClass} onClick={e => e.stopPropagation()}>
          <h2 className="text-white font-semibold text-base">Resume Session?</h2>
          <p className="text-gray-400 text-sm">
            You have an in-progress session for{' '}
            <span className="text-white font-mono">{existingSession.symbol}</span>
            {startedKline
              ? <> started at{' '}
                  <span className="text-white font-mono">
                    {new Date(startedKline.time * 1000).toLocaleString()}
                  </span></>
              : <> from candle {existingSession.startCandleIndex}</>}
            {' '}({existingSession.events.length} events recorded).
          </p>
          <button onClick={onResume} className={btnPrimary}>Resume</button>
          <button onClick={onDiscard} className={btnDanger}>Discard &amp; Start New</button>
          <button onClick={onClose} className={btnSecondary}>Cancel</button>
        </div>
      </div>
    )
  }

  function handleStart() {
    if (resolvedIdx < 0) return
    onStart(resolvedIdx)
  }

  return (
    <div className={overlayClass} onClick={onClose}>
      <div className={cardClass} onClick={e => e.stopPropagation()}>
        <h2 className="text-white font-semibold text-base">Start Learning Mode</h2>
        <p className="text-gray-400 text-sm">
          Navigate candle-by-candle, accept/reject signals, place custom orders, and add notes.
          Save the session to analyze with Claude.
        </p>
        <div className="space-y-1">
          <label className="text-xs text-gray-500 uppercase tracking-wider">
            Start from date &amp; time
          </label>
          <input
            type="datetime-local"
            step={900}
            value={startDate}
            min={minDate}
            max={maxDate}
            onChange={e => setStartDate(snapTo15Min(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
          />
          {resolvedKline ? (
            <p className="text-xs text-gray-600">
              Opens on the candle of{' '}
              <span className="text-gray-400 font-mono">
                {new Date(resolvedKline.time * 1000).toLocaleString()}
              </span>
              {' '}— {remaining.toLocaleString()} candles ahead to step through.
            </p>
          ) : (
            <p className="text-xs text-red-500">
              Pick a time within the loaded range.
            </p>
          )}
          {minDate && maxDate && (
            <p className="text-xs text-gray-700">
              Data available {minDate.replace('T', ' ')} → {maxDate.replace('T', ' ')}
            </p>
          )}
        </div>
        <button onClick={handleStart} disabled={resolvedIdx < 0} className={btnPrimary}>Start</button>
        <button onClick={onClose} className={btnSecondary}>Cancel</button>
      </div>
    </div>
  )
}
