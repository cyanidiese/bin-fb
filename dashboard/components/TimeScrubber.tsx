'use client'

const TICK = 1  // klines per step button press

interface Props {
  klines: { time: number }[]
  scrubberIdx: number | null   // null means live (at the most recent candle)
  isLoading: boolean
  onScrub: (idx: number | null) => void
}

function fmtTime(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function TimeScrubber({ klines, scrubberIdx, isLoading, onScrub }: Props) {
  if (klines.length === 0) return null

  const maxIdx    = klines.length - 1
  const isLive    = scrubberIdx === null
  const displayIdx = scrubberIdx ?? maxIdx

  function handleSlider(e: React.ChangeEvent<HTMLInputElement>) {
    const v = Number(e.target.value)
    onScrub(v >= maxIdx ? null : v)
  }

  function stepBack() {
    const current = scrubberIdx ?? maxIdx
    const next = Math.max(0, current - TICK)
    onScrub(next >= maxIdx ? null : next)
  }

  function stepForward() {
    if (isLive) return
    const next = (scrubberIdx ?? maxIdx) + TICK
    onScrub(next >= maxIdx ? null : next)
  }

  const btnCls =
    'px-2 py-1 text-xs rounded border border-gray-700 bg-gray-900 text-gray-400 ' +
    'hover:text-white hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'

  return (
    <div className="flex items-center gap-2">
      <button onClick={stepBack} disabled={isLoading || displayIdx <= 0} className={btnCls}>
        ◀
      </button>

      <input
        type="range"
        min={0}
        max={maxIdx}
        value={displayIdx}
        onChange={handleSlider}
        disabled={isLoading}
        className="w-48 accent-indigo-500 disabled:opacity-40 cursor-pointer"
      />

      <button onClick={stepForward} disabled={isLoading || isLive} className={btnCls}>
        ▶
      </button>

      <span className="text-xs font-mono min-w-[108px]">
        {isLive ? (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-400 inline-block animate-pulse" />
            <span className="text-emerald-400 font-semibold">LIVE</span>
          </span>
        ) : (
          <span className="text-gray-300">{fmtTime(klines[displayIdx].time)}</span>
        )}
      </span>

      {isLoading && (
        <span className="text-xs text-gray-600">updating…</span>
      )}

    </div>
  )
}
