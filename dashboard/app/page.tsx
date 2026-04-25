'use client'

import { useEffect, useMemo, useState } from 'react'
import { BotResults } from '@/lib/types'
import Header from '@/components/Header'
import SwingPointsChart from '@/components/SwingPointsChart'
import TrendLevelsTable from '@/components/TrendLevelsTable'
import AllPointsTable from '@/components/AllPointsTable'
import SignalsPanel from '@/components/SignalsPanel'
import LevelFilter from '@/components/LevelFilter'

export default function Page() {
  // Raw snapshot loaded from /results.json (written by bot/exporter.py after each candle close)
  const [data, setData] = useState<BotResults | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Which trend level the user has selected in the filter control.
  // Selecting L2 means: show L1 and L2 data only (hide L3 and above).
  // Defaults to the highest available level (show everything) once data loads.
  const [selectedLevel, setSelectedLevel] = useState<number | null>(null)
  // datetime-local inputs use "YYYY-MM-DDTHH:mm" strings; empty string means no limit
  const [fromDate, setFromDate] = useState<string>('')
  const [toDate,   setToDate]   = useState<string>('')

  // Fetch the bot snapshot once on mount. The file is served as a static asset
  // from /public/results.json and updated by the bot every 15 minutes.
  useEffect(() => {
    fetch('/results.json')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: BotResults) => {
        setData(d)
        // Default to the highest level so all points are visible on first load
        const maxLevel = Math.max(...d.trend_levels.map(t => t.level))
        setSelectedLevel(maxLevel)
      })
      .catch(e => setError(e.message))
  }, [])

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

    const filteredKlines = data.klines.filter(k => {
      const ms = k.time * 1000
      return ms >= fromMs && ms <= toMs
    })

    return { filteredPoints, filteredKlines, filteredLevels, availableLevels }
  }, [data, selectedLevel, fromDate, toDate])

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen text-red-400">
        Failed to load results.json: {error}
      </div>
    )
  }

  if (!data || selectedLevel === null) {
    return (
      <div className="flex items-center justify-center min-h-screen text-gray-500">
        Loading…
      </div>
    )
  }

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
            value={fromDate}
            onChange={e => setFromDate(e.target.value)}
            className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs focus:outline-none focus:border-indigo-500"
          />
          <span className="uppercase tracking-wider">To</span>
          <input
            type="datetime-local"
            value={toDate}
            onChange={e => setToDate(e.target.value)}
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

      {/* Price chart with kline close line and colored swing point dots.
          key={selectedLevel} forces a full Chart.js remount on level change,
          preventing canvas state from leaking between selections. */}
      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Swing Points</h2>
        <SwingPointsChart key={selectedLevel ?? 0} klines={filteredKlines} points={filteredPoints} />
      </section>

      {/* Summary table: one row per trend level showing direction, BoS price, last high/low */}
      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Trend Levels</h2>
        <TrendLevelsTable levels={filteredLevels} />
      </section>

      {/* Full list of swing points for the selected levels, split into two columns */}
      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">
          All Points <span className="text-gray-700 font-normal normal-case">(newest first)</span>
        </h2>
        <AllPointsTable points={filteredPoints} />
      </section>

      {/* Active trading signals from the bot's strategy — signals are not filtered by level */}
      <section>
        <h2 className="text-sm font-semibold uppercase text-gray-500 mb-3 tracking-wider">Signals</h2>
        <SignalsPanel signals={data.signals} />
      </section>
    </main>
  )
}
