'use client'
import { useEffect, useState } from 'react'

type LogEntry = { id: string; timestamp: string; level: string; title: string; detail: string; source: string }

interface LogFile {
  name: string
  size: number
  modified: string
}

const LEVEL_STYLE: Record<string, string> = {
  info: 'bg-slate-600 text-slate-200',
  warning: 'bg-amber-600 text-white',
  emergency: 'bg-red-600 text-white',
}

const LEVELS = ['info', 'warning', 'emergency']

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function LogPage() {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [levels, setLevels] = useState<Set<string>>(new Set(LEVELS))
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [keepInput, setKeepInput] = useState('50')
  const [trimming, setTrimming] = useState(false)
  const [files, setFiles] = useState<LogFile[]>([])

  const reload = () =>
    fetch('/api/log').then(r => r.json()).then(setEntries).catch(() => {})

  useEffect(() => {
    localStorage.setItem('log_last_read', new Date().toISOString())
    reload()
    fetch('/api/logs')
      .then(r => r.json())
      .then(d => setFiles(d.files ?? []))
      .catch(() => {})
  }, [])

  const toggleLevel = (l: string) =>
    setLevels(s => { const n = new Set(s); n.has(l) ? n.delete(l) : n.add(l); return n })

  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })

  const handleTrim = async () => {
    const keep = parseInt(keepInput, 10)
    if (isNaN(keep) || keep < 0) return
    setTrimming(true)
    await fetch(`/api/log?keep=${keep}`, { method: 'DELETE' }).catch(() => {})
    await reload()
    setTrimming(false)
  }

  const visible = entries.filter(e => levels.has(e.level))

  return (
    <main className="max-w-5xl mx-auto px-4 py-6 space-y-8">
      {/* System Log */}
      <section>
        <h1 className="text-xl font-semibold mb-4">System Log</h1>
        <div className="flex flex-wrap items-center gap-2 mb-4">
          {LEVELS.map(l => (
            <button key={l} onClick={() => toggleLevel(l)}
              className={`px-3 py-1 rounded text-xs font-bold border ${levels.has(l) ? LEVEL_STYLE[l] : 'border-slate-700 text-slate-500'}`}>
              {l.toUpperCase()}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-xs text-slate-400">Keep latest</span>
            <input
              type="number"
              min={0}
              value={keepInput}
              onChange={e => setKeepInput(e.target.value)}
              className="w-20 px-2 py-1 text-xs bg-slate-800 border border-slate-700 rounded text-slate-200"
            />
            <button
              onClick={handleTrim}
              disabled={trimming}
              className="px-3 py-1 text-xs rounded bg-red-900 hover:bg-red-800 text-red-200 disabled:opacity-50"
            >
              {trimming ? 'Trimming…' : 'Trim'}
            </button>
          </div>
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
      </section>

      {/* Log Files */}
      <section>
        <h2 className="text-lg font-semibold mb-3 text-white">Log Files</h2>
        {files.length === 0 ? (
          <p className="text-sm text-gray-600 italic">No log files found in /logs.</p>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500">
                  <th className="text-left px-4 py-2 font-normal">File</th>
                  <th className="text-left px-4 py-2 font-normal">Size</th>
                  <th className="text-left px-4 py-2 font-normal">Last modified</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {files.map(f => (
                  <tr key={f.name} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="px-4 py-2 text-indigo-300">{f.name}</td>
                    <td className="px-4 py-2 text-gray-400">{formatBytes(f.size)}</td>
                    <td className="px-4 py-2 text-gray-500">{new Date(f.modified).toLocaleString()}</td>
                    <td className="px-4 py-2 text-right">
                      <a
                        href={`/api/logs/${encodeURIComponent(f.name)}`}
                        download={f.name}
                        className="px-2 py-0.5 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-[10px] font-semibold hover:bg-indigo-800/60 transition-colors"
                      >
                        Download
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="mt-3 text-[10px] text-gray-600 font-mono">
          Also available via SSH:{' '}
          <code className="text-gray-500">bash scripts/download-logs.sh root@your-server</code>
        </p>
      </section>
    </main>
  )
}
