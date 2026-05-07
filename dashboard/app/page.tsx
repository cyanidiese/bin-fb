'use client'

import { useEffect, useMemo, useState } from 'react'
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

  // Which trend level the user has selected in the filter control.
  // Selecting L2 means: show L1 and L2 data only (hide L3 and above).
  // Defaults to the highest available level (show everything) once data loads.
  const [selectedLevel, setSelectedLevel] = useLocalStorage<number | null>(`db:strategy:${symbol}:selectedLevel`, null)
  // datetime-local inputs use "YYYY-MM-DDTHH:mm" strings; empty string means no limit
  const [fromDate, setFromDate] = useLocalStorage<string>(`db:strategy:${symbol}:fromDate`, '')
  const [toDate,   setToDate]   = useLocalStorage<string>(`db:strategy:${symbol}:toDate`, '')

  // Poll the bot snapshot every POLL_MS. On the first successful load, default
  // the level filter to the highest available level. Subsequent polls update the
  // data without resetting the user's filter selections.
  const POLL_MS = 15_000

  useEffect(() => {
    let cancelled = false
    setData(null)

    function load() {
      fetch(`/results_${symbol}.json?_=${Date.now()}`)
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
  }, [symbol])

  // Derive filtered datasets whenever the raw data, selected level, or date range changes.
  const { filteredPoints, filteredKlines, filteredLevels, availableLevels } = useMemo(() => {
    if (!data || selectedLevel === null) {
      return { filteredPoints: [], filteredKlines: [], filteredLevels: [], availableLevels: [] }
    }

    const availableLevels = data.trend_levels.map(t => t.level).sort((a, b) => a - b)
    const filteredLevels  = data.trend_levels.filter(t => t.level <= selectedLevel)

    // Convert picker strings to ms boundaries (0 / Infinity when not set)
    const fromMs = fromDate ? new Date(fromDate).getTime() : 0
    const toMs   = toDate   ? new Date(toDate).getTime()   : Infinity

    // Filter by level, then by date range
    const levelPoints = data.all_points.filter(p => {
      if (p.level > selectedLevel) return false
      const ms = new Date(p.time).getTime()
      return ms >= fromMs && ms <= toMs
    })

    // Drop inactive points that predate the earliest active point in this selection
    const activeMs = levelPoints.filter(p => p.active).map(p => new Date(p.time).getTime())
    const oldestActiveMs = activeMs.length > 0 ? Math.min(...activeMs) : 0
    const filteredPoints = levelPoints.filter(p => p.active || new Date(p.time).getTime() >= oldestActiveMs)

    // When no fromDate is set, auto-clip klines to the oldest active swing point so
    // the chart stays focused on the current structure. When fromDate IS explicitly
    // set, honour it exactly — don't let the auto-clip override the user's choice.
    const effectiveFromMs = !fromDate && oldestActiveMs > 0 ? oldestActiveMs : fromMs

    const filteredKlines = data.klines.filter(k => {
      const ms = k.time * 1000
      return ms >= effectiveFromMs && ms <= toMs
    })

    return { filteredPoints, filteredKlines, filteredLevels, availableLevels }
  }, [data, selectedLevel, fromDate, toDate])

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

      {/* Toolbar: level filter + date range pickers + clear button */}
      <div className="flex flex-wrap items-center gap-3 justify-end">
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
      </div>

      <CollapsibleSection title="Swing Points" storageKey="db:strategy:s:swingpoints">
        <SwingPointsChart key={selectedLevel ?? 0} klines={filteredKlines} points={filteredPoints} />
      </CollapsibleSection>

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
        <SignalsPanel signals={data.signals} />
      </CollapsibleSection>
    </main>
  )
}

export default function Page() {
  const { symbol } = useSymbolContext()

  return <PageContent key={symbol} symbol={symbol} />
}
