// dashboard/components/LearningNoteOverlay.tsx
'use client'

import { useState } from 'react'
import type { Kline } from '@/lib/types'
import { formatPrice } from '@/lib/formatPrice'

interface Props {
  onAddNote: (text: string) => void
  /** Candle the note will be attached to — shown so it is unambiguous which bar is
   *  being annotated. Notes are recorded against the current candle whether or not
   *  an order exists on it. */
  currentKline?: Kline | null
  currentCandleIndex?: number
}

export default function LearningNoteOverlay({ onAddNote, currentKline, currentCandleIndex }: Props) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')

  function handleSubmit() {
    const trimmed = text.trim()
    if (!trimmed) { setOpen(false); return }
    onAddNote(trimmed)
    setText('')
    setOpen(false)
  }

  return (
    <>
      {/* Sticky trigger button — always visible, fixed position */}
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 px-3 py-2 text-xs font-semibold rounded-full bg-amber-600 text-black hover:bg-amber-500 shadow-lg transition-colors"
      >
        + Note
      </button>

      {/* Floating overlay */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div
            className="bg-gray-900 border border-gray-700 rounded-lg p-4 w-80 space-y-3 shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <p className="text-xs text-gray-500 uppercase tracking-wider">Add note on this candle</p>
            {currentKline && (
              <p className="text-xs text-gray-600 font-mono">
                {new Date(currentKline.time * 1000).toLocaleString()}
                {currentCandleIndex !== undefined && <span className="text-gray-700"> · #{currentCandleIndex}</span>}
                <br />
                <span className="text-gray-500">
                  O {formatPrice(currentKline.open)} · H {formatPrice(currentKline.high)} · L {formatPrice(currentKline.low)} · C {formatPrice(currentKline.close)}
                </span>
              </p>
            )}
            <textarea
              autoFocus
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) handleSubmit() }}
              placeholder="Anything you observe at this candle…"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white resize-none h-24 focus:outline-none focus:border-amber-500"
            />
            <p className="text-xs text-gray-600">Ctrl+Enter to submit</p>
            <div className="flex gap-2">
              <button
                onClick={handleSubmit}
                className="flex-1 py-1.5 text-xs font-semibold rounded bg-amber-600 text-black hover:bg-amber-500 transition-colors"
              >
                Save Note
              </button>
              <button
                onClick={() => { setText(''); setOpen(false) }}
                className="flex-1 py-1.5 text-xs font-semibold rounded border border-gray-600 text-gray-300 hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
