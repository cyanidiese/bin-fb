'use client'
import { useEffect, useState } from 'react'

type Alert = { id: string; level: string; title: string; body: string; source: string; timestamp: string }
type AlertState = { alerts: Alert[]; dismissed_ids: string[] }

export default function AlertBanner() {
  const [alertState, setAlertState] = useState<AlertState>({ alerts: [], dismissed_ids: [] })
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/alert_state.json?t=${Date.now()}`)
        if (r.ok) setAlertState(await r.json())
      } catch {}
    }
    poll()
    const id = setInterval(poll, 10_000)
    return () => clearInterval(id)
  }, [])

  const visible = alertState.alerts.filter(a =>
    !alertState.dismissed_ids.includes(a.id) && ['warning', 'emergency'].includes(a.level)
  )

  if (visible.length === 0) return null

  const dismiss = async (id: string) => {
    await fetch('/api/alerts/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    })
    setAlertState(s => ({ ...s, dismissed_ids: [...s.dismissed_ids, id] }))
  }

  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  return (
    <div className="space-y-0">
      {visible.map(a => (
        <div key={a.id} className="flex items-start gap-3 px-4 py-2 bg-red-950 border-b border-red-800 text-sm">
          <span className={`px-1.5 py-0.5 rounded text-xs font-bold shrink-0 ${a.level === 'emergency' ? 'bg-red-600' : 'bg-amber-600'}`}>
            {a.level.toUpperCase()}
          </span>
          <div className="flex-1 min-w-0">
            <span className="font-medium text-red-200">{a.title}</span>
            <span className="ml-2 text-xs text-red-400">{a.source} · {new Date(a.timestamp).toLocaleTimeString()}</span>
            {expanded.has(a.id) && <p className="mt-1 text-red-300 text-xs whitespace-pre-wrap">{a.body}</p>}
          </div>
          <button onClick={() => toggleExpand(a.id)} className="text-xs text-red-400 hover:text-red-200 shrink-0">
            {expanded.has(a.id) ? 'Hide' : 'Details'}
          </button>
          <button onClick={() => dismiss(a.id)} className="text-red-400 hover:text-red-200 shrink-0 text-lg leading-none">×</button>
        </div>
      ))}
    </div>
  )
}
