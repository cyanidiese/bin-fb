// dashboard/components/LearningStartModal.tsx
'use client'

import { useState } from 'react'
import type { LearningSession } from '@/lib/learningTypes'

interface Props {
  isOpen: boolean
  currentCandleIndex: number
  totalCandles: number
  existingSession: LearningSession | null
  onStart: (startCandleIndex: number) => void
  onResume: () => void
  onDiscard: () => void
  onClose: () => void
}

export default function LearningStartModal({
  isOpen,
  currentCandleIndex,
  totalCandles,
  existingSession,
  onStart,
  onResume,
  onDiscard,
  onClose,
}: Props) {
  const [candleInput, setCandleInput] = useState(String(currentCandleIndex))

  if (!isOpen) return null

  const overlayClass =
    'fixed inset-0 z-50 flex items-center justify-center bg-black/60'

  const cardClass =
    'bg-gray-900 border border-gray-700 rounded-lg p-6 w-80 space-y-4 shadow-xl'

  const btnPrimary =
    'w-full px-4 py-2 text-sm font-semibold rounded bg-amber-500 text-black hover:bg-amber-400 transition-colors'

  const btnSecondary =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-gray-600 text-gray-300 hover:text-white hover:border-gray-400 transition-colors'

  const btnDanger =
    'w-full px-4 py-2 text-sm font-semibold rounded border border-red-700 text-red-400 hover:text-red-300 hover:border-red-500 transition-colors'

  if (existingSession) {
    return (
      <div className={overlayClass} onClick={onClose}>
        <div className={cardClass} onClick={e => e.stopPropagation()}>
          <h2 className="text-white font-semibold text-base">Resume Session?</h2>
          <p className="text-gray-400 text-sm">
            You have an in-progress session for{' '}
            <span className="text-white font-mono">{existingSession.symbol}</span> from
            candle {existingSession.startCandleIndex} ({existingSession.events.length} events recorded).
          </p>
          <button onClick={onResume} className={btnPrimary}>Resume</button>
          <button onClick={onDiscard} className={btnDanger}>Discard &amp; Start New</button>
          <button onClick={onClose} className={btnSecondary}>Cancel</button>
        </div>
      </div>
    )
  }

  function handleStart() {
    const idx = Math.max(0, Math.min(totalCandles - 1, Number(candleInput) || 0))
    onStart(idx)
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
            Start from candle index
          </label>
          <input
            type="number"
            min={0}
            max={totalCandles - 1}
            value={candleInput}
            onChange={e => setCandleInput(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-white focus:outline-none focus:border-amber-500"
          />
          <p className="text-xs text-gray-600">0 – {totalCandles - 1} available</p>
        </div>
        <button onClick={handleStart} className={btnPrimary}>Start</button>
        <button onClick={onClose} className={btnSecondary}>Cancel</button>
      </div>
    </div>
  )
}
