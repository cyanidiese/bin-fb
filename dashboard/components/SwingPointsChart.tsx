'use client'

import { useMemo } from 'react'
import {
  Chart as ChartJS,
  TimeScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js'
import 'chartjs-adapter-date-fns' // required for type: 'time' scale to parse timestamps
import { Line } from 'react-chartjs-2'
import { SwingPoint, Kline } from '@/lib/types'

ChartJS.register(TimeScale, LinearScale, PointElement, LineElement, Title, Tooltip, Legend, Filler)

interface Props {
  klines: Kline[]   // OHLCV candles — used for the close price line
  points: SwingPoint[] // pre-filtered swing points to overlay as colored dots
}

function fmt(price: number) {
  return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Inactive dots (wiped by a Break of Structure) are shown as small gray marks
// so the historical context is visible without competing with the active points.
function dotColor(p: SwingPoint): string {
  if (!p.active) return 'rgba(107,114,128,0.35)'
  const h = p.type === 'high'
  if (p.level === 1) return h ? 'rgba(74,222,128,0.9)'  : 'rgba(248,113,113,0.9)'
  if (p.level === 2) return h ? 'rgba(251,191,36,0.9)'  : 'rgba(251,146,60,0.9)'
  return                    h ? 'rgba(167,139,250,0.9)' : 'rgba(96,165,250,0.9)'
}

function dotRadius(p: SwingPoint): number {
  if (!p.active) return 3
  return p.level === 1 ? 5 : p.level === 2 ? 7 : 9
}

export default function SwingPointsChart({ klines, points }: Props) {
  const chartData = useMemo(() => {
    const sorted = [...points].sort(
      (a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()
    )

    // Clamp the close price line to start at the earliest active swing point.
    // Inactive (historical) points can predate the current trend structure, so
    // anchoring to active points keeps the chart focused on the relevant window.
    const activeSorted = sorted.filter(p => p.active)
    const minPointMs = activeSorted.length > 0
      ? Math.min(...activeSorted.map(p => new Date(p.time).getTime()))
      : sorted.length > 0
        ? Math.min(...sorted.map(p => new Date(p.time).getTime()))
        : 0
    const visibleKlines = klines.filter(k => k.time * 1000 >= minPointMs)
    const closes = visibleKlines.map(k => ({ x: k.time * 1000, y: k.close }))
    const opens  = visibleKlines.map(k => ({ x: k.time * 1000, y: k.open  }))
    const highs  = visibleKlines.map(k => ({ x: k.time * 1000, y: k.high  }))
    const lows   = visibleKlines.map(k => ({ x: k.time * 1000, y: k.low   }))

    const dots      = sorted.map(p => ({ x: new Date(p.time).getTime(), y: p.price }))
    const trendDots = activeSorted.map(p => ({ x: new Date(p.time).getTime(), y: p.price }))
    const colors    = sorted.map(dotColor)
    const radii     = sorted.map(dotRadius)

    return { closes, opens, highs, lows, dots, trendDots, colors, radii }
  }, [klines, points])

  if (chartData.closes.length === 0 && chartData.dots.length === 0) {
    return (
      <div className="h-72 flex items-center justify-center text-gray-600 border border-gray-800 rounded-lg">
        No chart data available
      </div>
    )
  }

  const data = {
    datasets: [
      {
        label: 'Close Price',
        data: chartData.closes,
        borderColor: 'rgb(99, 102, 241)',
        backgroundColor: 'rgba(99, 102, 241, 0.05)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: true,
        tension: 0.1,
        spanGaps: false,
      },
      {
        label: 'Open Price',
        data: chartData.opens,
        borderColor: 'rgba(148, 163, 184, 0.6)',  // slate-300 at 60%
        borderWidth: 1,
        borderDash: [4, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.1,
        spanGaps: false,
      },
      {
        label: 'Max Price',
        data: chartData.highs,
        borderColor: 'rgba(74, 222, 128, 0.5)',   // green at 50%
        borderWidth: 1,
        borderDash: [2, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.1,
        spanGaps: false,
      },
      {
        label: 'Min Price',
        data: chartData.lows,
        borderColor: 'rgba(248, 113, 113, 0.5)',  // red at 50%
        borderWidth: 1,
        borderDash: [2, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.1,
        spanGaps: false,
      },
      {
        // Thin line connecting active swing points so it traces the live structure.
        label: 'Trend Line',
        data: chartData.trendDots,
        borderColor: 'rgba(234, 179, 8, 0.7)',  // amber-400 at 70% opacity
        borderWidth: 1.5,
        pointRadius: 0,      // dots are drawn by the dataset below; no duplicates here
        showLine: true,
        tension: 0,          // straight segments between highs and lows
        fill: false,
      },
      {
        // Swing point overlay — each dot at its exact timestamp, no connecting line
        label: 'Swing Points',
        data: chartData.dots,
        borderColor: 'transparent',
        backgroundColor: chartData.colors,
        pointRadius: chartData.radii,
        pointHoverRadius: 10,
        showLine: false,
      },
    ],
  }

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      // Built-in legend — clicking any item toggles that dataset on/off
      legend: {
        display: true,
        labels: {
          color: '#9ca3af',
          boxWidth: 12,
          boxHeight: 12,
          borderRadius: 2,
          usePointStyle: true,
          font: { size: 11 },
        },
      },
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        // ctx.parsed.y holds the numeric price value when data is {x, y} objects
        callbacks: { label: (ctx: { parsed: { y: number } }) => ` ${fmt(ctx.parsed.y)}` },
      },
    },
    scales: {
      x: {
        type: 'time' as const, // proportional time spacing — gaps between old L3 and recent L1 are real
        time: {
          tooltipFormat: 'MMM d, HH:mm',
          displayFormats: { hour: 'MMM d HH:mm', day: 'MMM d', week: 'MMM d' },
        },
        ticks: { color: '#6b7280', maxTicksLimit: 10, font: { size: 10 } },
        grid: { color: '#1f2937' },
      },
      y: {
        ticks: { color: '#6b7280', font: { size: 10 }, callback: (v: unknown) => fmt(v as number) },
        grid: { color: '#1f2937' },
      },
    },
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="h-72 p-4">
        <Line data={data} options={options as Parameters<typeof Line>[0]['options']} />
      </div>

      {/* Color legend explaining level × high/low dot encoding */}
      <div className="flex gap-4 px-4 pb-3 text-xs text-gray-500">
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(74,222,128,0.9)' }} />
          L1 High
        </span>
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(248,113,113,0.9)' }} />
          L1 Low
        </span>
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(251,191,36,0.9)' }} />
          L2 High
        </span>
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(251,146,60,0.9)' }} />
          L2 Low
        </span>
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(167,139,250,0.9)' }} />
          L3 High
        </span>
        <span className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'rgba(96,165,250,0.9)' }} />
          L3 Low
        </span>
      </div>
    </div>
  )
}
