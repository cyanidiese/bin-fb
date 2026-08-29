'use client'

import { useMemo } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import {
  Chart as ChartJS,
  TimeScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
} from 'chart.js'
import 'chartjs-adapter-date-fns' // required for type: 'time' scale to parse timestamps
import { Line, Chart } from 'react-chartjs-2'
import { CandlestickController, CandlestickElement } from 'chartjs-chart-financial'
import { SwingPoint, Kline } from '@/lib/types'
import { formatPrice } from '@/lib/formatPrice'
import type { LearningOrder } from '@/lib/learningTypes'
import type { Plugin } from 'chart.js'

ChartJS.register(
  TimeScale, LinearScale, PointElement, LineElement,
  Title, Tooltip, Legend, Filler,
  CandlestickController, CandlestickElement,
)

interface Props {
  klines: Kline[]
  points: SwingPoint[]
  learningOrders?: LearningOrder[]
  /**
   * Learning Mode: called with the price under the cursor when the chart is clicked.
   * Lets the user pick a TP/SL/Entry level off the chart instead of typing a number.
   */
  onPriceClick?: (price: number) => void
  /** True while a custom-order field is focused — shows a crosshair cursor. */
  priceCaptureActive?: boolean
}

const fmt = (price: number) => formatPrice(price)

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

// If a candle's upper or lower wick is more than spikeRatio × the average wick of
// the previous `lookback` candles, clamp the wick tip to bodyEdge + clampRatio × avgWick.
// open/close (the body) are never modified — only high/low are adjusted for display.
function applySpikeClamping(klines: Kline[], lookback = 10, spikeRatio = 5, clampRatio = 2): Kline[] {
  return klines.map((k, i) => {
    if (i < 3) return k

    const prev = klines.slice(Math.max(0, i - lookback), i)
    const avgUpperWick = prev.reduce((s, c) => s + (c.high - Math.max(c.open, c.close)), 0) / prev.length
    const avgLowerWick = prev.reduce((s, c) => s + (Math.min(c.open, c.close) - c.low), 0) / prev.length

    const bodyTop    = Math.max(k.open, k.close)
    const bodyBottom = Math.min(k.open, k.close)

    let high = k.high
    let low  = k.low
    if (avgUpperWick > 0 && (k.high - bodyTop)    > spikeRatio * avgUpperWick) high = bodyTop    + clampRatio * avgUpperWick
    if (avgLowerWick > 0 && (bodyBottom - k.low)  > spikeRatio * avgLowerWick) low  = bodyBottom - clampRatio * avgLowerWick

    if (high === k.high && low === k.low) return k
    return { ...k, high, low }
  })
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

function makeLearningOrdersPlugin(klines: Kline[], orders: LearningOrder[]): Plugin<'line'> {
  return {
    id: 'learningOrders',
    beforeDatasetsDraw(chart) {
      if (!orders || orders.length === 0) return
      const ctx = chart.ctx
      const xs = chart.scales.x
      const ys = chart.scales.y
      if (!xs || !ys) return

      ctx.save()

      for (const order of orders) {
        const openKline = klines[order.placedAtCandleIndex]
        if (!openKline) continue
        const closeKline = order.closedAtCandleIndex !== undefined
          ? klines[order.closedAtCandleIndex]
          : klines[klines.length - 1]
        if (!closeKline) continue

        const x1 = xs.getPixelForValue(openKline.time * 1000)
        const x2 = xs.getPixelForValue(closeKline.time * 1000)
        const w  = Math.max(Math.abs(x2 - x1), 3)

        const isClosed = order.closedAtCandleIndex !== undefined
        const alpha = isClosed ? 0.15 : 0.30

        const ey = ys.getPixelForValue(order.entryPrice)
        const ty = ys.getPixelForValue(order.tpPrice)
        const sy = ys.getPixelForValue(order.slPrice)

        // TP zone (green)
        ctx.fillStyle = `rgba(52,211,153,${alpha})`
        ctx.fillRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))
        ctx.strokeStyle = `rgba(52,211,153,${alpha * 2.5})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))

        // SL zone (red)
        ctx.fillStyle = `rgba(248,113,113,${alpha})`
        ctx.fillRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))
        ctx.strokeStyle = `rgba(248,113,113,${alpha * 2.5})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))

        // Entry line
        ctx.strokeStyle = `rgba(209,213,219,0.7)`
        ctx.lineWidth = 1.5
        ctx.setLineDash([])
        ctx.beginPath()
        ctx.moveTo(x1, ey)
        ctx.lineTo(x2, ey)
        ctx.stroke()
      }

      ctx.restore()
    },
  }
}

// Chart.js reports the click position in canvas pixels; the y scale converts that
// to a price. Learning Mode uses it to fill the focused custom-order field so a
// TP/SL can be picked off the chart instead of typed.
function makePriceClickHandler(onPriceClick?: (price: number) => void) {
  if (!onPriceClick) return undefined
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (evt: any, _els: unknown, chart: any) => {
    const y = evt?.y
    const scale = chart?.scales?.y
    if (typeof y !== 'number' || !scale) return
    const price = scale.getValueForPixel(y)
    if (typeof price === 'number' && isFinite(price)) onPriceClick(price)
  }
}

export default function SwingPointsChart({ klines, points, learningOrders, onPriceClick, priceCaptureActive }: Props) {
  const [candleView]  = useLocalStorage<boolean>('db:chart:candleView',  false)
  const [clampSpikes] = useLocalStorage<boolean>('db:chart:clampSpikes', false)
  const chartData = useMemo(() => {
    const sorted = [...points].sort(
      (a, b) => new Date(a.time).getTime() - new Date(b.time).getTime()
    )
    const activeSorted = sorted.filter(p => p.active)

    // Raw klines — chart always shows unmodified candles
    const closes = klines.map(k => ({ x: k.time * 1000, y: k.close }))
    const opens  = klines.map(k => ({ x: k.time * 1000, y: k.open  }))
    const highs  = klines.map(k => ({ x: k.time * 1000, y: k.high  }))
    const lows   = klines.map(k => ({ x: k.time * 1000, y: k.low   }))
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const ohlc   = klines.map(k => ({ x: k.time * 1000, o: k.open, h: k.high, l: k.low, c: k.close } as any))

    // Clamped values used only for swing-point dot placement (when enabled)
    const clampedByTime = clampSpikes
      ? new Map(applySpikeClamping(klines).map(k => [k.time * 1000, k]))
      : null

    function swingY(p: SwingPoint): number {
      if (!clampedByTime) return p.price
      const c = clampedByTime.get(new Date(p.time).getTime())
      if (!c) return p.price
      if (p.type === 'high' && p.price > c.high) return c.high
      if (p.type === 'low'  && p.price < c.low)  return c.low
      return p.price
    }

    const dots      = sorted.map(p => ({ x: new Date(p.time).getTime(), y: swingY(p) }))
    const trendDots = activeSorted.map(p => ({ x: new Date(p.time).getTime(), y: swingY(p) }))
    const colors    = sorted.map(dotColor)
    const radii     = sorted.map(dotRadius)

    return { closes, opens, highs, lows, ohlc, dots, trendDots, colors, radii }
  }, [klines, points, clampSpikes])

  const learningPlugin = useMemo(
    () => makeLearningOrdersPlugin(klines, learningOrders ?? []),
    [klines, learningOrders]
  )

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
    type: 'line' as const,
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
      onClick: makePriceClickHandler(onPriceClick),
    }

    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900">
        <div className={`h-72 p-4 ${priceCaptureActive ? 'cursor-crosshair' : ''}`}>
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <Chart type={'candlestick' as any} data={candleData as any} options={candleOptions as any} plugins={[learningPlugin]} />
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
    onClick: makePriceClickHandler(onPriceClick),
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className={`h-72 p-4 ${priceCaptureActive ? 'cursor-crosshair' : ''}`}>
        <Line data={lineData} options={lineOptions as Parameters<typeof Line>[0]['options']} plugins={[learningPlugin]} />
      </div>
      {colorLegend}
    </div>
  )
}
