'use client'
import { useEffect, useState } from 'react'

type LogEntry = { id: string; timestamp: string; level: string; title: string; detail: string; source: string }

const LEVEL_STYLE: Record<string, string> = {
  info: 'bg-slate-600 text-slate-200',
  warning: 'bg-amber-600 text-white',
  emergency: 'bg-red-600 text-white',
}

const LEVELS = ['info', 'warning', 'emergency']

export default function LogPage() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [levels, setLevels] = useState<Set<string>>(new Set(LEVELS))
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    localStorage.setItem('log_last_read', new Date().toISOString())
    fetch('/api/log').then(r => r.json()).then(setEntries).catch(() => {})
  }, [])

  const toggleLevel = (l: string) =>
    setLevels(s => { const n = new Set(s); n.has(l) ? n.delete(l) : n.add(l); return n })

  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  const visible = entries.filter(e => levels.has(e.level))

  return (
    <main className="max-w-5xl mx-auto px-4 py-6">
      <h1 className="text-xl font-semibold mb-4">System Log</h1>
      <div className="flex gap-2 mb-4">
        {LEVELS.map(l => (
          <button key={l} onClick={() => toggleLevel(l)}
            className={`px-3 py-1 rounded text-xs font-bold border ${levels.has(l) ? LEVEL_STYLE[l] : 'border-slate-700 text-slate-500'}`}>
            {l.toUpperCase()}
          </button>
        ))}
      </div>
      <div className="space-y-0 border border-slate-800 rounded overflow-hidden">
        {visible.length === 0 && (
          <div className="px-4 py-8 text-center text-slate-500">No entries</div>
        )}
        {visible.map((e, i) => (
          <div key={e.id} className={`px-4 py-2 text-sm border-b border-slate-800 ${i % 2 === 0 ? 'bg-slate-950' : 'bg-slate-900'}`}>
            <div className="flex items-center gap-3">
              <span className={`px-1.5 py-0.5 rounded text-xs font-bold shrink-0 ${LEVEL_STYLE[e.level] ?? ''}`}>
                {e.level.toUpperCase()}
              </span>
              <span className="text-slate-400 text-xs shrink-0">{new Date(e.timestamp).toLocaleString()}</span>
              <span className="text-slate-300 flex-1 truncate">{e.title}</span>
              <span className="text-slate-500 text-xs shrink-0">{e.source}</span>
              {e.detail && (
                <button onClick={() => toggleExpand(e.id)}
                  className="text-xs text-slate-400 hover:text-slate-200 shrink-0">
                  {expanded.has(e.id) ? '▲' : '▼'}
                </button>
              )}
            </div>
            {expanded.has(e.id) && e.detail && (
              <pre className="mt-2 text-xs text-slate-400 whitespace-pre-wrap pl-2 border-l border-slate-700">{e.detail}</pre>
            )}
          </div>
        ))}
      </div>
    </main>
  )
}
