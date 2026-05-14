'use client'

import { useEffect, useRef } from 'react'
import {
  Chart,
  LineController,
  ScatterController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Tooltip,
  Legend,
} from 'chart.js'
import 'chartjs-adapter-date-fns'
import type { RealOrder } from '@/lib/types'

Chart.register(LineController, ScatterController, LineElement, PointElement, LinearScale, TimeScale, Tooltip, Legend)

interface Kline {
  time: number  // Unix timestamp in seconds
  close: number
}

interface Props {
  klines: Kline[]
  realOrders: RealOrder[]
}

export default function TradesChart({ klines, realOrders }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!canvasRef.current || klines.length === 0) return

    chartRef.current?.destroy()

    const priceData = klines.map(k => ({ x: k.time * 1000, y: k.close }))

    const entryPoints: { x: number; y: number; side: string }[] = []
    const exitPoints: { x: number; y: number; result: string }[] = []

    for (const order of realOrders) {
      if (order.open_time) {
        entryPoints.push({ x: new Date(order.open_time).getTime(), y: order.entry_price, side: order.side })
      }
      exitPoints.push({ x: new Date(order.close_time).getTime(), y: order.close_price, result: order.result })
    }

    chartRef.current = new Chart(canvasRef.current, {
      type: 'line',
      data: {
        datasets: [
          {
            label: 'Close',
            type: 'line' as const,
            data: priceData,
            borderColor: 'rgb(99,102,241)',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0,
            order: 1,
          },
          {
            label: 'Entry',
            type: 'scatter' as const,
            data: entryPoints.map(p => ({ x: p.x, y: p.y })),
            backgroundColor: entryPoints.map(p => p.side === 'BUY' ? 'rgb(74,222,128)' : 'rgb(248,113,113)'),
            pointRadius: 6,
            pointStyle: entryPoints.map(() => 'triangle') as unknown as string,
            order: 0,
          },
          {
            label: 'Exit',
            type: 'scatter' as const,
            data: exitPoints.map(p => ({ x: p.x, y: p.y })),
            backgroundColor: exitPoints.map(p =>
              ['win', 'partial', 'trail'].includes(p.result) ? 'rgb(74,222,128)' : 'rgb(248,113,113)'
            ),
            pointRadius: 5,
            pointStyle: 'cross' as const,
            order: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: {
          legend: { display: true, labels: { color: '#9ca3af', boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => `${ctx.dataset.label}: ${Number(ctx.parsed.y).toFixed(2)}`,
            },
          },
        },
        scales: {
          x: {
            type: 'time' as const,
            time: { unit: 'hour' as const },
            ticks: { color: '#6b7280', maxTicksLimit: 8 },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
          y: {
            ticks: { color: '#6b7280' },
            grid: { color: 'rgba(255,255,255,0.05)' },
          },
        },
      },
    })

    return () => { chartRef.current?.destroy() }
  }, [klines, realOrders])

  return (
    <div className="relative w-full" style={{ height: 320 }}>
      <canvas ref={canvasRef} />
    </div>
  )
}
