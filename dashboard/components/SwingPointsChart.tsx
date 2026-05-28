'use client'

import { useMemo } from 'react'
import {
  Chart as ChartJS,
  TimeScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js'
import 'chartjs-adapter-date-fns' // required for type: 'time' scale to parse timestamps
import { Line, Chart } from 'react-chartjs-2'
import { CandlestickController, CandlestickElement } from 'chartjs-chart-financial'
import { SwingPoint, Kline } from '@/lib/types'

ChartJS.register(
  TimeScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
  CandlestickController, CandlestickElement,
)

interface Props {
  klines: Kline[]
  points: SwingPoint[]
  candleView?: boolean
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

const SHARED_SCALES = {
  x: {
    type: 'time' as const,
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
}

const SHARED_LEGEND = {
  display: true,
  labels: {
    color: '#9ca3af',
    boxWidth: 12,
    boxHeight: 12,
    borderRadius: 2,
    usePointStyle: true,
    font: { size: 11 },
  },
}

export default function SwingPointsChart({ klines, points, candleView = false }: Props) {
  const chartData = useMemo(() => {
    const sorted = [...points].sort(
      (a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()
    )
    const activeSorted = sorted.filter(p => p.active)

    const closes = klines.map(k => ({ x: k.time * 1000, y: k.close }))
    const opens  = klines.map(k => ({ x: k.time * 1000, y: k.open  }))
    const highs  = klines.map(k => ({ x: k.time * 1000, y: k.high  }))
    const lows   = klines.map(k => ({ x: k.time * 1000, y: k.low   }))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ohlc   = klines.map(k => ({ x: k.time * 1000, o: k.open, h: k.high, l: k.low, c: k.close } as any))

    const dots      = sorted.map(p => ({ x: new Date(p.time).getTime(), y: p.price }))
    const trendDots = activeSorted.map(p => ({ x: new Date(p.time).getTime(), y: p.price }))
    const colors    = sorted.map(dotColor)
    const radii     = sorted.map(dotRadius)

    return { closes, opens, highs, lows, ohlc, dots, trendDots, colors, radii }
  }, [klines, points])

  if (chartData.closes.length === 0 && chartData.dots.length === 0) {
    return (
      <div className="h-72 flex items-center justify-center text-gray-600 border border-gray-800 rounded-lg">
        No chart data available
      </div>
    )
  }

  const trendLineDataset = {
    type: 'line' as const,
    label: 'Trend Line',
    data: chartData.trendDots,
    borderColor: 'rgba(234, 179, 8, 0.7)',
    borderWidth: 1.5,
    pointRadius: 0,
    showLine: true,
    tension: 0,
    fill: false,
  }

  const swingPointsDataset = {
    type: 'scatter' as const,
    label: 'Swing Points',
    data: chartData.dots,
    borderColor: 'transparent',
    backgroundColor: chartData.colors,
    pointRadius: chartData.radii,
    pointHoverRadius: 10,
    showLine: false,
  }

  const colorLegend = (
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
  )

  if (candleView) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const candleDataset: any = {
      type: 'candlestick',
      label: 'Price',
      data: chartData.ohlc,
      color: {
        up:        'rgba(74,222,128,0.9)',
        down:      'rgba(248,113,113,0.9)',
        unchanged: 'rgba(148,163,184,0.6)',
      },
    }

    const candleData = { datasets: [candleDataset, trendLineDataset, swingPointsDataset] }

    const candleOptions = {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: SHARED_LEGEND,
        tooltip: {
          backgroundColor: '#1f2937',
          titleColor: '#f9fafb',
          bodyColor: '#d1d5db',
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          callbacks: { label: (ctx: any) => {
            const r = ctx.raw
            if (r && 'o' in r) return ` O:${fmt(r.o)}  H:${fmt(r.h)}  L:${fmt(r.l)}  C:${fmt(r.c)}`
            return ` ${fmt(ctx.parsed?.y ?? 0)}`
          }},
        },
      },
      scales: SHARED_SCALES,
    }

    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900">
        <div className="h-72 p-4">
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <Chart type={'candlestick' as any} data={candleData as any} options={candleOptions as any} />
        </div>
        {colorLegend}
      </div>
    )
  }

  const lineData = {
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
        borderColor: 'rgba(148, 163, 184, 0.6)',
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
        borderColor: 'rgba(74, 222, 128, 0.5)',
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
        borderColor: 'rgba(248, 113, 113, 0.5)',
        borderWidth: 1,
        borderDash: [2, 3],
        pointRadius: 0,
        fill: false,
        tension: 0.1,
        spanGaps: false,
      },
      trendLineDataset,
      {
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

  const lineOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: SHARED_LEGEND,
      tooltip: {
        backgroundColor: '#1f2937',
        titleColor: '#f9fafb',
        bodyColor: '#d1d5db',
        callbacks: { label: (ctx: { parsed: { y: number } }) => ` ${fmt(ctx.parsed.y)}` },
      },
    },
    scales: SHARED_SCALES,
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="h-72 p-4">
        <Line data={lineData} options={lineOptions as Parameters<typeof Line>[0]['options']} />
      </div>
      {colorLegend}
    </div>
  )
}
