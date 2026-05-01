'use client'

import { useMemo, useRef } from 'react'
import {
  Chart as ChartJS,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Legend,
  type Plugin,
} from 'chart.js'
import { Line } from 'react-chartjs-2'
import type { BacktestApiKline, BacktestTrade } from '@/lib/types'

ChartJS.register(LinearScale, PointElement, LineElement, Tooltip, Legend)

interface Props {
  klines: BacktestApiKline[]
  trades: BacktestTrade[]
}

function fmtTick(unixSec: number): string {
  const d = new Date(unixSec * 1000)
  const mm = (d.getMonth() + 1).toString().padStart(2, '0')
  const dd = d.getDate().toString().padStart(2, '0')
  const hh = d.getHours().toString().padStart(2, '0')
  const mi = d.getMinutes().toString().padStart(2, '0')
  return `${mm}/${dd} ${hh}:${mi}`
}

export default function PresetChart({ klines, trades }: Props) {
  // Keep trades in a ref so the stable plugin always reads the current list
  // without needing to be destroyed and re-registered on every filter change.
  const tradesRef = useRef<BacktestTrade[]>(trades)
  tradesRef.current = trades

  // Plugin created once — stable identity, reads from ref at draw time.
  const plugin = useMemo<Plugin<'line'>>(() => ({
    id: 'tradeRects',
    beforeDatasetsDraw(chart) {
      const ctx = chart.ctx
      const xs = chart.scales.x
      const ys = chart.scales.y
      if (!xs || !ys) return

      ctx.save()

      for (const t of tradesRef.current) {
        const x1 = xs.getPixelForValue(t.open_candle)
        const x2 = xs.getPixelForValue(t.close_candle ?? t.open_candle + 1)
        const w = Math.max(x2 - x1, 3)

        const ey = ys.getPixelForValue(t.entry)
        const ty = ys.getPixelForValue(t.tp)
        const sy = ys.getPixelForValue(t.sl)

        const won     = t.result === 'win'
        const lost    = t.result === 'loss'
        const partial = t.result === 'partial'
        const trail   = t.result === 'trail'

        ctx.fillStyle = `rgba(52,211,153,${won ? 0.30 : 0.08})`
        ctx.fillRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))
        ctx.strokeStyle = `rgba(52,211,153,${won ? 0.7 : 0.3})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, ty), w, Math.abs(ty - ey))

        ctx.fillStyle = `rgba(248,113,113,${lost ? 0.30 : 0.08})`
        ctx.fillRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))
        ctx.strokeStyle = `rgba(248,113,113,${lost ? 0.7 : 0.3})`
        ctx.lineWidth = 1
        ctx.strokeRect(x1, Math.min(ey, sy), w, Math.abs(sy - ey))

        if ((partial || trail) && t.close_price != null) {
          const cy = ys.getPixelForValue(t.close_price)
          ctx.fillStyle = trail ? 'rgba(56,189,248,0.30)' : 'rgba(251,191,36,0.30)'
          ctx.fillRect(x1, Math.min(ey, cy), w, Math.abs(cy - ey))
          ctx.strokeStyle = trail ? 'rgba(56,189,248,0.7)' : 'rgba(251,191,36,0.7)'
          ctx.lineWidth = 1
          ctx.strokeRect(x1, Math.min(ey, cy), w, Math.abs(cy - ey))
        }

        ctx.strokeStyle = 'rgba(209,213,219,0.5)'
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(x1, ey)
        ctx.lineTo(x2, ey)
        ctx.stroke()
      }

      ctx.restore()
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), []) // intentionally empty — ref carries the live data

  const idxToTime = useMemo(() => {
    const m = new Map<number, number>()
    for (const k of klines) m.set(k.index, k.time)
    return m
  }, [klines])

  const data = useMemo(() => ({
    datasets: [
      {
        label: 'High',
        data: klines.map(k => ({ x: k.index, y: k.high })),
        borderColor: 'rgba(74,222,128,0.45)',
        borderWidth: 1,
        pointRadius: 0,
        tension: 0,
        order: 4,
      },
      {
        label: 'Close',
        data: klines.map(k => ({ x: k.index, y: k.close })),
        borderColor: 'rgba(255,255,255,0.85)',
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0,
        order: 1,
      },
      {
        label: 'Open',
        data: klines.map(k => ({ x: k.index, y: k.open })),
        borderColor: 'rgba(156,163,175,0.45)',
        borderWidth: 1,
        pointRadius: 0,
        tension: 0,
        order: 3,
      },
      {
        label: 'Low',
        data: klines.map(k => ({ x: k.index, y: k.low })),
        borderColor: 'rgba(248,113,113,0.45)',
        borderWidth: 1,
        pointRadius: 0,
        tension: 0,
        order: 2,
      },
    ],
  }), [klines])

  const options = useMemo(() => ({
    responsive: true,
    maintainAspectRatio: false,
    animation: false as const,
    interaction: { mode: 'index' as const, intersect: false },
    scales: {
      x: {
        type: 'linear' as const,
        ticks: {
          color: '#6b7280',
          maxTicksLimit: 8,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          callback: (v: any) => {
            const t = idxToTime.get(Math.round(Number(v)))
            return t != null ? fmtTick(t) : ''
          },
        },
        grid: { color: 'rgba(75,85,99,0.25)' },
      },
      y: {
        type: 'linear' as const,
        ticks: {
          color: '#6b7280',
          callback: (v: number | string) => Number(v).toLocaleString(),
        },
        grid: { color: 'rgba(75,85,99,0.25)' },
      },
    },
    plugins: {
      legend: {
        labels: { color: '#9ca3af', boxWidth: 10, font: { size: 11 } },
      },
      tooltip: {
        backgroundColor: 'rgba(17,24,39,0.9)',
        titleColor: '#f3f4f6',
        bodyColor: '#d1d5db',
        callbacks: {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          title: (items: any[]) => {
            const t = idxToTime.get(Math.round(items[0]?.parsed?.x ?? -1))
            return t != null ? new Date(t * 1000).toLocaleString() : ''
          },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          label: (ctx: any) => ` ${ctx.dataset?.label}: ${Number(ctx.parsed?.y ?? 0).toLocaleString()}`,
        },
      },
    },
  }), [idxToTime])

  if (klines.length === 0) {
    return (
      <div className="flex items-center justify-center h-48 text-gray-500 text-sm">
        No klines data.
      </div>
    )
  }

  return (
    <div style={{ height: 380 }}>
      <Line data={data} options={options} plugins={[plugin]} />
    </div>
  )
}
