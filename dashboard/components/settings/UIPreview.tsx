'use client'

import { useState } from 'react'

type PreviewState = { liveRunning: boolean; testRunning: boolean; emergency: boolean }

export default function UIPreview() {
  const [preview, setPreview] = useState<PreviewState>({ liveRunning: false, testRunning: false, emergency: false })

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        UI Preview
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <div className="space-y-2">
          {([
            ['liveRunning', 'Imitate live mode running'],
            ['testRunning', 'Imitate test mode running'],
            ['emergency',   'Imitate emergency notice'],
          ] as [keyof PreviewState, string][]).map(([key, label]) => (
            <label key={key} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={preview[key]}
                onChange={e => setPreview(p => ({ ...p, [key]: e.target.checked }))}
                className="rounded"
              />
              <span className="text-sm text-gray-300">{label}</span>
            </label>
          ))}
        </div>
        {(preview.liveRunning || preview.testRunning || preview.emergency) && (
          <div className="mt-2 p-3 border border-gray-700 rounded space-y-2">
            {(preview.liveRunning || preview.testRunning) && (
              <div className="flex items-center gap-2">
                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono font-semibold border
                  ${preview.liveRunning ? 'border-amber-500 text-amber-400' : 'border-slate-600 text-slate-400'}`}>
                  {preview.liveRunning && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
                  {preview.liveRunning ? 'LIVE' : 'TEST'} · RUNNING
                </span>
                <span className="text-xs text-gray-500">Badge preview</span>
              </div>
            )}
            {preview.emergency && (
              <div className="flex items-start gap-3 px-3 py-2 bg-red-950 border border-red-800 rounded text-sm">
                <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-red-600 shrink-0">EMERGENCY</span>
                <span className="text-red-200">Sample emergency alert · main · {new Date().toLocaleTimeString()}</span>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
