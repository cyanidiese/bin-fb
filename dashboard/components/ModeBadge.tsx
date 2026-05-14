'use client'
import { useEffect, useState } from 'react'
import Link from 'next/link'

type BotState = { running: boolean; mode: string; last_heartbeat?: string }

export default function ModeBadge() {
  const [state, setState] = useState<BotState | null>(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/bot_state.json?t=${Date.now()}`)
        if (r.ok) {
          const data: BotState = await r.json()
          if (data.last_heartbeat) {
            const age = Date.now() - new Date(data.last_heartbeat).getTime()
            if (age > 30_000) data.running = false
          }
          setState(data)
        } else {
          setState(null)
        }
      } catch {
        setState(null)
      }
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [])

  const mode = state?.mode?.toUpperCase() ?? '…'
  const running = state?.running ?? false
  const isLive = state?.mode === 'live'

  return (
    <Link href="/settings" title="Go to Settings">
      <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono font-semibold border transition-colors
        ${isLive && running ? 'border-amber-500 text-amber-400' : 'border-slate-600 text-slate-400'}`}>
        {isLive && running && (
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
        )}
        {mode} · {running ? 'RUNNING' : 'STOPPED'}
      </span>
    </Link>
  )
}
