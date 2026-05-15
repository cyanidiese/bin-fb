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
import type { RealOrder, VirtualOrder } from '@/lib/types'

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
}

export default function TradesChart({ klines, realOrders, virtualOrders = [] }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!canvasRef.current || klines.length === 0) return
    chartRef.current?.destroy()

    const hasOHLC = klines[0]?.open != null

    // Closed orders with known open + close times
    const closedOrders = [
      ...realOrders
        .filter(o => o.open_time && o.close_time && o.result !== 'closed_early')
        .map(o => ({
          open_time:   o.open_time!,
          close_time:  o.close_time,
          entry_price: o.entry_price,
          tp:          o.tp,
          sl:          o.sl,
          close_price: o.close_price,
          result:      o.result as string,
          isReal:      true,
        })),
      ...virtualOrders
        .filter(o => o.status === 'closed' && o.open_time && o.close_time && o.result && o.result !== 'closed_early')
        .map(o => ({
          open_time:   o.open_time,
          close_time:  o.close_time!,
          entry_price: o.entry_price,
          tp:          o.tp,
          sl:          o.sl,
          close_price: o.close_price,
          result:      o.result as string,
          isReal:      false,
        })),
    ]

    // Plugin: draws TP/SL rectangles for each closed order
    const tradeRects: Plugin<'line'> = {
      id: 'tradeRects',
      beforeDatasetsDraw(chart) {
        const ctx = chart.ctx
        const xs = chart.scales.x
        const ys = chart.scales.y
        if (!xs || !ys) return

        ctx.save()

        for (const o of closedOrders) {
          const x1 = xs.getPixelForValue(new Date(o.open_time).getTime())
          const x2 = xs.getPixelForValue(new Date(o.close_time).getTime())
          const w  = Math.max(Math.abs(x2 - x1), 3)

          const ey = ys.getPixelForValue(o.entry_price)
          const ty = ys.getPixelForValue(o.tp)
          const sy = o.sl > 0 ? ys.getPixelForValue(o.sl) : null

          const won     = o.result === 'win'
          const lost    = o.result === 'loss'
          const partial = o.result === 'partial'
          const trail   = o.result === 'trail'

          // TP zone (green)
          ctx.fillStyle   = `rgba(52,211,153,${won ? 0.28 : 0.07})`
          ctx.fillRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))
          ctx.strokeStyle = `rgba(52,211,153,${won ? 0.70 : 0.28})`
          ctx.lineWidth   = 1
          ctx.strokeRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))

          // SL zone (red)
          if (sy != null) {
            ctx.fillStyle   = `rgba(248,113,113,${lost ? 0.28 : 0.07})`
            ctx.fillRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))
            ctx.strokeStyle = `rgba(248,113,113,${lost ? 0.70 : 0.28})`
            ctx.lineWidth   = 1
            ctx.strokeRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))
          }

          // Partial / trail actual-close zone
          if ((partial || trail) && o.close_price != null) {
            const cy   = ys.getPixelForValue(o.close_price)
            const base = trail ? [56, 189, 248] : [251, 191, 36]   // sky : amber
            ctx.fillStyle   = `rgba(${base[0]},${base[1]},${base[2]},0.28)`
            ctx.fillRect(x1, Math.min(ey, cy), w, Math.abs(cy - ey))
            ctx.strokeStyle = `rgba(${base[0]},${base[1]},${base[2]},0.70)`
            ctx.lineWidth   = 1
            ctx.strokeRect(x1, Math.min(ey, cy), w, Math.abs(cy - ey))
          }

          // Entry line
          ctx.strokeStyle = `rgba(209,213,219,${o.isReal ? 0.7 : 0.35})`
          ctx.lineWidth   = o.isReal ? 1.5 : 1
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
              label: ctx => ` ${ctx.dataset.label}: ${Number(ctx.parsed.y).toLocaleString()}`,
            },
          },
        },
        scales: {
          x: {
            type: 'time' as const,
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
  }, [klines, realOrders, virtualOrders])

  return (
    <div className="relative w-full" style={{ height: 380 }}>
      <canvas ref={canvasRef} />
    </div>
  )
}
