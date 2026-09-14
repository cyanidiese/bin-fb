'use client'

import { useEffect, useRef } from 'react'
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Legend,
  type Plugin,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import type {
  RealOrder,
  VirtualOrder,
  OpenRealPosition,
  OpenVirtualPosition,
} from '@/lib/types'
import { formatPrice } from '@/lib/formatPrice'

Chart.register(LineController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend)

interface Kline {
  time: number  // Unix seconds
  open?: number
  high?: number
  low?: number
  close: number
}

interface Props {
  klines: Kline[]
  realOrders: RealOrder[]
  virtualOrders?: VirtualOrder[]
  /** Positions still running. Drawn with no right edge — they have not ended yet. */
  openReal?: OpenRealPosition[]
  openVirtual?: OpenVirtualPosition[]
  /** The page date range, in ms. Pins the x-axis so nothing outside it is drawn. */
  fromMs?: number | null
  toMs?: number | null
}

/** One trade to paint. `endMs === null` means "still running". */
interface Bar {
  startMs: number
  endMs: number | null
  entry_price: number
  tp: number
  sl: number
  close_price: number | null
  result: string | null
  isReal: boolean
}

/** Pixels over which a cut edge fades out, so a truncated trade reads as continuing
 *  past the edge rather than as a trade that genuinely started or ended there. */
const FADE_PX = 28

export default function TradesChart({
  klines,
  realOrders,
  virtualOrders = [],
  openReal = [],
  openVirtual = [],
  fromMs = null,
  toMs = null,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!canvasRef.current || klines.length === 0) return
    chartRef.current?.destroy()

    const ms = (iso: string | null | undefined): number | null => {
      if (!iso) return null
      const t = new Date(iso).getTime()
      return Number.isNaN(t) ? null : t
    }

    const bars: Bar[] = []

    for (const o of realOrders) {
      const s = ms(o.open_time)
      const e = ms(o.close_time)
      if (s === null || e === null || o.result === 'closed_early') continue
      bars.push({
        startMs: s, endMs: e,
        entry_price: o.entry_price, tp: o.tp, sl: o.sl,
        close_price: o.close_price, result: o.result, isReal: true,
      })
    }

    for (const o of virtualOrders) {
      const s = ms(o.open_time)
      const e = ms(o.close_time)
      if (o.status !== 'closed' || s === null || e === null) continue
      if (!o.result || o.result === 'closed_early') continue
      bars.push({
        startMs: s, endMs: e,
        entry_price: o.entry_price, tp: o.tp, sl: o.sl,
        close_price: o.close_price, result: o.result, isReal: false,
      })
    }

    // Running positions: same green/red geometry, but no end — they are drawn to the
    // right edge with that edge faded away, so an ongoing trade is visually distinct
    // from one that closed exactly at the end of the window.
    for (const o of openReal) {
      const s = ms(o.open_time)
      if (s === null) continue
      bars.push({
        startMs: s, endMs: null,
        entry_price: o.entry_price, tp: o.tp, sl: o.sl,
        close_price: o.current_price ?? null, result: null, isReal: true,
      })
    }
    for (const o of openVirtual) {
      const s = ms(o.open_time)
      if (s === null) continue
      bars.push({
        startMs: s, endMs: null,
        entry_price: o.entry_price, tp: o.tp, sl: o.sl,
        close_price: o.current_price ?? null, result: null, isReal: false,
      })
    }

    const hasOHLC = klines[0]?.open != null

    const tradeRects: Plugin<'line'> = {
      id: 'tradeRects',
      beforeDatasetsDraw(chart) {
        const ctx = chart.ctx
        const xs = chart.scales.x
        const ys = chart.scales.y
        const area = chart.chartArea
        if (!xs || !ys || !area) return

        ctx.save()
        // Everything is clipped to the plot area. Without this a trade that began
        // before `from` painted its rectangle across the y-axis and out of the card.
        ctx.beginPath()
        ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top)
        ctx.clip()

        /** Fill + border for one zone, fading whichever edges are cut off. */
        const paintZone = (
          x1: number, x2: number, yA: number, yB: number,
          rgb: [number, number, number], fillA: number, strokeA: number,
          cutLeft: boolean, cutRight: boolean,
        ) => {
          const top = Math.min(yA, yB)
          const h = Math.abs(yB - yA)
          const w = x2 - x1
          if (h <= 0 || w <= 0) return
          const [r, g, b] = rgb

          const ramp = (alpha: number) => {
            if (!cutLeft && !cutRight) return `rgba(${r},${g},${b},${alpha})`
            const grad = ctx.createLinearGradient(x1, 0, x2, 0)
            const f = Math.min(FADE_PX / w, 0.45)
            grad.addColorStop(0, `rgba(${r},${g},${b},${cutLeft ? 0 : alpha})`)
            if (cutLeft) grad.addColorStop(f, `rgba(${r},${g},${b},${alpha})`)
            if (cutRight) grad.addColorStop(1 - f, `rgba(${r},${g},${b},${alpha})`)
            grad.addColorStop(1, `rgba(${r},${g},${b},${cutRight ? 0 : alpha})`)
            return grad
          }

          ctx.fillStyle = ramp(fillA)
          ctx.fillRect(x1, top, w, h)

          // Borders drawn edge by edge: a cut side gets no vertical border at all,
          // which is the "missing edge" cue. Top/bottom fade into the cut.
          ctx.lineWidth = 1
          ctx.strokeStyle = ramp(strokeA)
          ctx.beginPath()
          ctx.moveTo(x1, top); ctx.lineTo(x2, top)
          ctx.moveTo(x1, top + h); ctx.lineTo(x2, top + h)
          ctx.stroke()

          ctx.strokeStyle = `rgba(${r},${g},${b},${strokeA})`
          ctx.beginPath()
          if (!cutLeft) { ctx.moveTo(x1, top); ctx.lineTo(x1, top + h) }
          if (!cutRight) { ctx.moveTo(x2, top); ctx.lineTo(x2, top + h) }
          ctx.stroke()
        }

        for (const o of bars) {
          const rawX1 = xs.getPixelForValue(o.startMs)
          const rawX2 = o.endMs === null ? area.right : xs.getPixelForValue(o.endMs)
          if (rawX2 < area.left || rawX1 > area.right) continue   // wholly outside

          const x1 = Math.max(rawX1, area.left)
          const x2 = Math.min(Math.max(rawX2, x1 + 3), area.right)
          const cutLeft = rawX1 < area.left
          const cutRight = o.endMs === null || rawX2 > area.right

          const ey = ys.getPixelForValue(o.entry_price)
          const ty = ys.getPixelForValue(o.tp)
          const sy = o.sl > 0 ? ys.getPixelForValue(o.sl) : null

          const running = o.result === null
          const won = o.result === 'win'
          const lost = o.result === 'loss'
          const partial = o.result === 'partial'
          const trail = o.result === 'trail'

          // A running trade has no outcome yet, so neither side is emphasised —
          // it shows as the plain green-over-red band of its own TP/SL.
          paintZone(x1, x2, ey, ty, [52, 211, 153],
            won ? 0.28 : running ? 0.13 : 0.07,
            won ? 0.70 : running ? 0.45 : 0.28,
            cutLeft, cutRight)

          if (sy != null) {
            paintZone(x1, x2, ey, sy, [248, 113, 113],
              lost ? 0.28 : running ? 0.13 : 0.07,
              lost ? 0.70 : running ? 0.45 : 0.28,
              cutLeft, cutRight)
          }

          if ((partial || trail) && o.close_price != null) {
            const cy = ys.getPixelForValue(o.close_price)
            const base: [number, number, number] = trail ? [56, 189, 248] : [251, 191, 36]
            paintZone(x1, x2, ey, cy, base, 0.28, 0.70, cutLeft, cutRight)
          }

          // Entry line
          const eGrad = (() => {
            const a = o.isReal ? 0.7 : 0.35
            if (!cutLeft && !cutRight) return `rgba(209,213,219,${a})`
            const g = ctx.createLinearGradient(x1, 0, x2, 0)
            const f = Math.min(FADE_PX / Math.max(x2 - x1, 1), 0.45)
            g.addColorStop(0, `rgba(209,213,219,${cutLeft ? 0 : a})`)
            if (cutLeft) g.addColorStop(f, `rgba(209,213,219,${a})`)
            if (cutRight) g.addColorStop(1 - f, `rgba(209,213,219,${a})`)
            g.addColorStop(1, `rgba(209,213,219,${cutRight ? 0 : a})`)
            return g
          })()
          ctx.strokeStyle = eGrad
          ctx.lineWidth = o.isReal ? 1.5 : 1
          ctx.setLineDash(o.isReal ? [] : [3, 3])
          ctx.beginPath()
          ctx.moveTo(x1, ey)
          ctx.lineTo(x2, ey)
          ctx.stroke()
          ctx.setLineDash([])
        }

        ctx.restore()
      },
    }

    const datasets: Chart['data']['datasets'] = []

    if (hasOHLC) {
      datasets.push(
        {
          label: 'High',
          data: klines.map(k => ({ x: k.time * 1000, y: k.high! })),
          borderColor: 'rgba(74,222,128,0.4)',
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
          order: 4,
        },
        {
          label: 'Low',
          data: klines.map(k => ({ x: k.time * 1000, y: k.low! })),
          borderColor: 'rgba(248,113,113,0.4)',
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
          order: 3,
        },
        {
          label: 'Open',
          data: klines.map(k => ({ x: k.time * 1000, y: k.open! })),
          borderColor: 'rgba(156,163,175,0.4)',
          borderWidth: 1,
          pointRadius: 0,
          tension: 0,
          order: 2,
        },
      )
    }

    datasets.push({
      label: 'Close',
      data: klines.map(k => ({ x: k.time * 1000, y: k.close })),
      borderColor: 'rgba(255,255,255,0.85)',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0,
      order: 1,
    })

    chartRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: 'index' as const, intersect: false },
        plugins: {
          legend: {
            display: true,
            labels: { color: '#9ca3af', boxWidth: 10, font: { size: 11 } },
          },
          tooltip: {
            backgroundColor: 'rgba(17,24,39,0.9)',
            titleColor: '#f3f4f6',
            bodyColor: '#d1d5db',
            callbacks: {
              label: ctx => ` ${ctx.dataset.label}: ${formatPrice(ctx.parsed.y)}`,
            },
          },
        },
        scales: {
          x: {
            type: 'time' as const,
            // Pinned to the page range so the axis is the window the user asked for,
            // not whatever the data happens to span. Without this the axis stretched
            // to fit trade rectangles that began before `from`.
            ...(fromMs != null ? { min: fromMs } : {}),
            ...(toMs != null ? { max: toMs } : {}),
            time: { unit: 'hour' as const },
            ticks: { color: '#6b7280', maxTicksLimit: 8 },
            grid: { color: 'rgba(75,85,99,0.25)' },
          },
          y: {
            type: 'linear' as const,
            ticks: {
              color: '#6b7280',
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              callback: (v: any) => Number(v).toLocaleString(),
            },
            grid: { color: 'rgba(75,85,99,0.25)' },
          },
        },
      },
      plugins: [tradeRects],
    })

    return () => { chartRef.current?.destroy() }
  }, [klines, realOrders, virtualOrders, openReal, openVirtual, fromMs, toMs])

  return (
    <div className="relative w-full" style={{ height: 380 }}>
      <canvas ref={canvasRef} />
    </div>
  )
}
