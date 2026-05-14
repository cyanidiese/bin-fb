'use client'

import { useState, useEffect } from 'react'

interface LogFile {
  name: string
  size: number
  modified: string
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function LogsPage() {
  const [files, setFiles] = useState<LogFile[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/logs')
      .then(r => r.json())
      .then(d => setFiles(d.files ?? []))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  return (
    <main className="p-4 max-w-2xl space-y-6">
      <h1 className="text-lg font-bold text-white">Logs</h1>

      {loading && <p className="text-sm text-gray-500">Loading…</p>}

      {!loading && files.length === 0 && (
        <p className="text-sm text-gray-600 italic">No log files found in /logs.</p>
      )}

      {files.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-800 text-xs font-semibold text-gray-400 uppercase tracking-wide">
            Log Files
          </div>
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
                  <td className="px-4 py-2 text-gray-500">
                    {new Date(f.modified).toLocaleString()}
                  </td>
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

      <p className="text-[10px] text-gray-600 font-mono">
        Log files are also available via SSH:{' '}
        <code className="text-gray-500">bash scripts/download-logs.sh root@your-server</code>
      </p>
    </main>
  )
}
