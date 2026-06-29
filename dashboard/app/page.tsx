'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { BotResults } from '@/lib/types'
import Header from '@/components/Header'
import SwingPointsChart from '@/components/SwingPointsChart'
import TrendLevelsTable from '@/components/TrendLevelsTable'
import AllPointsTable from '@/components/AllPointsTable'
import SignalsPanel from '@/components/SignalsPanel'
import LevelFilter from '@/components/LevelFilter'
import CollapsibleSection from '@/components/CollapsibleSection'
import { useLocalStorage } from '@/lib/useLocalStorage'
import { useSymbolContext } from '@/lib/SymbolContext'
import TimeScrubber from '@/components/TimeScrubber'
import type { ReplayResult } from '@/lib/types'
import { useLearningSession } from '@/lib/useLearningSession'
import LearningStartModal from '@/components/LearningStartModal'
import LearningRecommendationPanel from '@/components/LearningRecommendationPanel'
import LearningNoteOverlay from '@/components/LearningNoteOverlay'

function tsToDatetimeLocal(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function snapTo15Min(dt: string): string {
  if (!dt) return dt
  const ms = new Date(dt).getTime()
  if (isNaN(ms)) return dt
  const snapped = Math.floor(ms / 900_000) * 900_000
  const d = new Date(snapped)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function PageContent({ symbol }: { symbol: string }) {
  // Raw snapshot loaded from /results_${symbol}.json (written by bot/exporter.py after each candle close)
  const [data, setData] = useState<BotResults | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [scrubberIdx, setScrubberIdx] = useState<number | null>(null)
  const [replayData,  setReplayData]  = useState<ReplayResult | null>(null)
  const [isReplaying, setIsReplaying] = useState(false)

  // Which trend level the user has selected in the filter control.
  // Selecting L2 means: show L1 and L2 data only (hide L3 and above).
  // Defaults to the highest available level (show everything) once data loads.
  const [selectedLevel, setSelectedLevel] = useLocalStorage<number | null>(`db:strategy:${symbol}:selectedLevel`, null)
  // datetime-local inputs use "YYYY-MM-DDTHH:mm" strings; empty string means no limit
  const [fromDate, setFromDate] = useLocalStorage<string>(`db:strategy:${symbol}:fromDate`, '')
  const [toDate,   setToDate]   = useLocalStorage<string>(`db:strategy:${symbol}:toDate`, '')

  const learningSession = useLearningSession()
  const [learningModalOpen, setLearningModalOpen] = useState(false)

  const dataRef = useRef<BotResults | null>(null)
  dataRef.current = data

  const isLearningActiveRef = useRef(false)
  isLearningActiveRef.current = learningSession.isActive

  // Poll the bot snapshot every POLL_MS. On the first successful load, default
  // the level filter to the highest available level. Subsequent polls update the
  // data without resetting the user's filter selections.
  const POLL_MS = 15_000

  useEffect(() => {
    let cancelled = false

    function load() {
      fetch(`/api/public-file?f=results_${symbol}.json`)
        .then(r => {
          if (r.status === 404) return null   // file not yet written — not an error
          if (!r.ok) throw new Error(`HTTP ${r.status}`)
          return r.json()
        })
        .then((d: BotResults | null) => {
          if (cancelled) return
          if (d === null) return              // keep showing "no data" state
          setData(d)
          setError(null)
          // Initialise the level filter, or reset it if the stored value is
          // below all available levels (happens when a placeholder file with
          // no trend_levels was loaded first, setting selectedLevel to 0).
          setSelectedLevel(prev => {
            if (d.trend_levels.length === 0) return 0
            const max = Math.max(...d.trend_levels.map(t => t.level))
            const min = Math.min(...d.trend_levels.map(t => t.level))
            if (prev === null || prev < min) return max
            return prev
          })
        })
        .catch(e => { if (!cancelled) setError(e.message) })
    }

    load()
    const id = setInterval(load, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [symbol, setSelectedLevel])

  useEffect(() => {
    if (scrubberIdx === null) return
    // Don't disable controls yet — let the user keep scrolling freely.
    // isReplaying only becomes true when the debounce fires and the fetch starts.
    // replayData is NOT cleared here so the previous overlay stays visible while dragging.
    const timer = setTimeout(() => {
      setIsReplaying(true)
      fetch('/api/replay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, candle_index: scrubberIdx }),
      })
        .then(r => r.json())
        .then((d: Omit<ReplayResult, 'candle_index'>) => {
          setReplayData({ ...d, candle_index: scrubberIdx })
          setIsReplaying(false)
          if (isLearningActiveRef.current && dataRef.current) {
            const kline = dataRef.current.klines[scrubberIdx]
            if (kline) {
              learningSession.onReplayResult(
                scrubberIdx,
                kline,
                { trend_levels: d.trend_levels, all_points: d.all_points },
              )
            }
          }
        })
        .catch(() => setIsReplaying(false))
    }, 300)
    return () => clearTimeout(timer)
  }, [scrubberIdx, symbol, learningSession.onReplayResult])

  // Derived values: replay state is only active when a specific candle is selected.
  // This avoids synchronous setState calls inside effects to clear these values.
  const effectiveReplayData  = scrubberIdx !== null ? replayData  : null
  const effectiveIsReplaying = scrubberIdx !== null && isReplaying

  // Derive filtered datasets whenever the raw data, selected level, or date range changes.
  const { filteredPoints, filteredKlines, filteredLevels, availableLevels } = useMemo(() => {
    if (!data || selectedLevel === null) {
      return { filteredPoints: [], filteredKlines: [], filteredLevels: [], availableLevels: [] }
    }

    // Klines clip immediately to scrubberIdx (client-side, instant visual feedback while dragging).
    // Overlays (points, levels) only switch when a server response has arrived — stale replay
    // data is intentionally kept visible during the debounce + loading window so there is no flash.
    const isReplayOverlay = scrubberIdx !== null && effectiveReplayData !== null
    const srcLevels = isReplayOverlay ? effectiveReplayData!.trend_levels : data.trend_levels
    const srcPoints = isReplayOverlay ? effectiveReplayData!.all_points   : data.all_points
    const srcKlines = scrubberIdx !== null
      ? data.klines.slice(0, scrubberIdx + 1)
      : data.klines

    const availableLevels = srcLevels.map(t => t.level).sort((a, b) => a - b)
    const filteredLevels  = srcLevels.filter(t => t.level <= selectedLevel)

    const fromMs = fromDate ? new Date(fromDate).getTime() : 0
    const toMs   = toDate   ? new Date(toDate).getTime()   : Infinity

    const levelPoints = srcPoints.filter(p => {
      if (p.level > selectedLevel) return false
      const ms = new Date(p.time).getTime()
      return ms >= fromMs && ms <= toMs
    })

    const activeMs = levelPoints.filter(p => p.active).map(p => new Date(p.time).getTime())
    const oldestActiveMs = activeMs.length > 0 ? Math.min(...activeMs) : 0
    const filteredPoints = levelPoints.filter(p => p.active || new Date(p.time).getTime() >= oldestActiveMs)

    const effectiveFromMs = !fromDate && oldestActiveMs > 0 ? oldestActiveMs : fromMs

    const filteredKlines = srcKlines.filter(k => {
      const ms = k.time * 1000
      return ms >= effectiveFromMs && ms <= toMs
    })

    return { filteredPoints, filteredKlines, filteredLevels, availableLevels }
  }, [data, selectedLevel, fromDate, toDate, scrubberIdx, effectiveReplayData])

  const srcSignals = effectiveReplayData !== null
    ? effectiveReplayData.signals
    : (data?.signals ?? [])

  const currentSignal = learningSession.isActive
    ? (srcSignals.length > 0 ? srcSignals[0] : null)
    : null

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen text-red-400">
        Failed to load results_{symbol}.json: {error}
      </div>
    )
  }

  if (!data || selectedLevel === null) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-2 text-gray-500">
        <span>No data for <span className="text-gray-300 font-mono">{symbol}</span> yet.</span>
        <span className="text-xs text-gray-600">Run the bot or paper trader to generate results_{symbol}.json.</span>
      </div>
    )
  }

  if (data.klines.length === 0 && data.trend_levels.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen gap-2 text-gray-500">
        <span className="text-gray-300 font-mono">{symbol}</span>
        <span>Waiting for bot analysis…</span>
        <span className="text-xs text-gray-600">Strategy data will appear here once the bot processes this symbol.</span>
      </div>
    )
  }

  const klineMinDate = data.klines.length > 0 ? tsToDatetimeLocal(data.klines[0].time) : ''
  const klineMaxDate = data.klines.length > 0 ? tsToDatetimeLocal(data.klines[data.klines.length - 1].time) : ''

  return (
    <main className="max-w-7xl mx-auto px-4 py-6 space-y-6">
      {/* Symbol, timeframe, mode badge, current price, and snapshot timestamp */}
      <Header data={data} />

      {/* Toolbar: time scrubber (left) + level filter + date range pickers + clear button (right) */}
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <TimeScrubber
          klines={data.klines}
          scrubberIdx={scrubberIdx}
          isLoading={effectiveIsReplaying}
          onScrub={setScrubberIdx}
          learningMode={learningSession.isActive}
        />
        <div className="flex items-center gap-3 flex-wrap justify-end">
          <LevelFilter
            levels={availableLevels}
            selected={selectedLevel}
            onChange={setSelectedLevel}
          />

          <div className="flex items-center gap-2 text-xs text-gray-500">
            <span className="uppercase tracking-wider">From</span>
            <input
              type="datetime-local"
              step={900}
              value={fromDate}
              min={klineMinDate}
              max={klineMaxDate}
              onChange={e => setFromDate(snapTo15Min(e.target.value))}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500"
            />
            <span className="uppercase tracking-wider">To</span>
            <input
              type="datetime-local"
              step={900}
              value={toDate}
              min={klineMinDate}
              max={klineMaxDate}
              onChange={e => setToDate(snapTo15Min(e.target.value))}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500"
            />
          </div>

          <button
            onClick={() => { setFromDate(''); setToDate('') }}
            className="px-3 py-1.5 text-xs font-semibold rounded border border-gray-700 bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800 transition-colors"
          >
            Clear
          </button>

          {!learningSession.isActive ? (
            <button
              onClick={() => setLearningModalOpen(true)}
              className="px-3 py-1.5 text-xs font-semibold rounded border border-amber-700 bg-amber-900/30 text-amber-400 hover:text-amber-300 hover:bg-amber-900/50 transition-colors"
            >
              Learning Mode
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-amber-400 animate-pulse">● LEARNING</span>
              <button
                onClick={() => {
                  if (confirm(`Save session? (${learningSession.session?.events.length ?? 0} events recorded)`)) {
                    learningSession.saveAndExit()
                  }
                }}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-amber-600 text-black hover:bg-amber-500 transition-colors"
              >
                Stop &amp; Save
              </button>
              <button
                onClick={() => {
                  if (confirm('Discard this session? All events will be lost.')) {
                    learningSession.discardSession()
                  }
                }}
                className="px-3 py-1.5 text-xs font-semibold rounded border border-red-800 text-red-400 hover:text-red-300 transition-colors"
              >
                Discard
              </button>
            </div>
          )}
        </div>
      </div>

      <CollapsibleSection title="Swing Points" storageKey="db:strategy:s:swingpoints">
        <SwingPointsChart
          key={selectedLevel ?? 0}
          klines={filteredKlines}
          points={filteredPoints}
          learningOrders={learningSession.isActive ? learningSession.orders : undefined}
        />
      </CollapsibleSection>

      {learningSession.isActive && (
        <LearningRecommendationPanel
          signal={currentSignal}
          currentKlineClose={filteredKlines.length > 0 ? filteredKlines[filteredKlines.length - 1].close : 0}
          onAccept={learningSession.acceptSignal}
          onReject={learningSession.rejectSignal}
          onPlaceCustom={learningSession.placeCustomOrder}
        />
      )}

      <CollapsibleSection title="Trend Levels" storageKey="db:strategy:s:trendlevels">
        <TrendLevelsTable levels={filteredLevels} />
      </CollapsibleSection>

      <CollapsibleSection
        title={<>All Points <span className="text-gray-700 font-normal normal-case">(newest first)</span></>}
        storageKey="db:strategy:s:allpoints"
      >
        <AllPointsTable points={filteredPoints} />
      </CollapsibleSection>

      <CollapsibleSection title="Signals" storageKey="db:strategy:s:signals">
        <SignalsPanel signals={srcSignals} />
      </CollapsibleSection>
      <LearningStartModal
        isOpen={learningModalOpen}
        currentCandleIndex={scrubberIdx ?? (data.klines.length - 1)}
        totalCandles={data.klines.length}
        existingSession={learningSession.session}
        onStart={(idx) => {
          learningSession.startSession(symbol, idx)
          setScrubberIdx(idx)
          setLearningModalOpen(false)
        }}
        onResume={() => setLearningModalOpen(false)}
        onDiscard={() => {
          learningSession.discardSession()
          setLearningModalOpen(false)
        }}
        onClose={() => setLearningModalOpen(false)}
      />

      {learningSession.isActive && (
        <LearningNoteOverlay onAddNote={learningSession.addNote} />
      )}
    </main>
  )
}

export default function Page() {
  const { symbol } = useSymbolContext()

  return <PageContent key={symbol} symbol={symbol} />
}
