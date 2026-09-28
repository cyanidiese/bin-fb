'use client'

// Weight suggestions: what the measured policies would set, next to the real weights.
// Nothing changes until the user clicks Apply (merged into the mode's config server-side).
// Spec: docs/specs/2026-09-28-weight-shadow-calculator.md

import { useCallback, useEffect, useState } from 'react'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS, INPUT_CLS, SAVE_BTN_CLS } from '@/lib/risk-styles'

type Top = [number | null, number, string | null]
interface Row { w: number; lock: string | null; p7: Top; p14: Top; policies: { static: number; tilt: number; brake: number } }
interface Score { mean: number; vsStatic: number; betterDays: number }
interface Data {
  mode: 'test' | 'live'
  day: string | null
  tats_min_weight: number
  symbols: Record<string, Row>
  track: Track
  /** Reconstructed from order history (scripts/backfill_weight_shadow.py). */
  history: Track
}
interface Track { snapshots: number; evaluated: number; days: number; scores: Record<'static' | 'tilt' | 'brake', Score> | null }

function TrackLine({ label, t }: { label: string; t: Track }) {
  if (!t.scores) return <div className="text-gray-500">{label}: {t.snapshots} snapshot(s) — scored once a snapshot is {t.days} days old.</div>
  return (
    <div className="flex flex-wrap gap-x-6 gap-y-1">
      <span className="text-gray-500">{label} ({t.evaluated} day(s), next {t.days} days, would-be-real trades):</span>
      {(['tilt', 'brake'] as const).map(p => (
        <span key={p} className={t.scores![p].vsStatic > 0 ? 'text-emerald-400' : 'text-gray-400'}>
          {p}: {t.scores![p].vsStatic > 0 ? '+' : ''}{t.scores![p].vsStatic.toFixed(2)}% vs current, better on {t.scores![p].betterDays}/{t.evaluated} days
        </span>
      ))}
      <span className="text-gray-500">current weights: {t.scores.static.mean > 0 ? '+' : ''}{t.scores.static.mean.toFixed(2)}%</span>
    </div>
  )
}

interface Props {
  mode: 'test' | 'live'
  /** Called after weights were saved, so the page reloads its config. */
  onApplied: () => void
}

const fmtPct = (t: Top) => t[0] === null ? '—' : `${t[0] > 0 ? '+' : ''}${t[0].toFixed(1)}% (${t[1]})`
const fmtW = (w: number) => Number.isInteger(w) ? String(w) : w.toFixed(2)

export default function WeightSuggestions({ mode, onApplied }: Props) {
  const [data, setData] = useState<Data | null>(null)
  const [custom, setCustom] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const load = useCallback(() => {
    fetch(`/api/weight-suggestions?mode=${mode}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d) })
      .catch(() => {})
  }, [mode])
  // The page remounts this per mode (key), so there is no stale mode's data to clear.
  useEffect(() => { load() }, [load])

  async function apply(changes: Record<string, number>) {
    if (!data || Object.keys(changes).length === 0) return
    const tmw = data.tats_min_weight
    const lines = Object.entries(changes).map(([s, w]) => {
      const old = data.symbols[s]?.w ?? 0
      const crosses = tmw > 0 && ((old >= tmw) !== (w >= tmw))
      return `${s}: ${fmtW(old)} → ${fmtW(w)}${crosses ? `  ⚠ crosses tats_min_weight ${tmw} (changes how a lone signal is sized)` : ''}`
    })
    if (!window.confirm(`Apply these ${mode.toUpperCase()} weights?\n\n${lines.join('\n')}\n\nThe bot uses them from the next candle. Every change is logged.`)) return
    setBusy(true); setMsg(null)
    try {
      const r = await fetch('/api/weight-suggestions/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, changes }),
      })
      const d = await r.json()
      if (!r.ok) { setMsg(d.error ?? `HTTP ${r.status}`); return }
      setMsg(`Applied ${Object.keys(changes).length} weight(s) ✓`)
      setCustom({})
      load(); onApplied()
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  if (!data) return null
  const rows = Object.entries(data.symbols).filter(([, r]) => r.w > 0).sort((a, b) => b[1].w - a[1].w)
  const pending = (pol: 'tilt' | 'brake') =>
    Object.fromEntries(rows.filter(([, r]) => r.policies[pol] !== r.w).map(([s, r]) => [s, r.policies[pol]]))
  const tiltAll = pending('tilt'), brakeAll = pending('brake')
  const t = data.track

  return (
    <section className={SECTION_CLS}>
      <p className={SECTION_HEADER_CLS}>
        Weight suggestions — {data.mode}
        <span className="ml-2 normal-case font-normal tracking-normal text-gray-500">
          suggestions only · nothing changes until you apply
        </span>
      </p>
      <div className={SECTION_BODY_CLS}>
        <div className="text-[11px] text-gray-400 space-y-1">
          <p>
            <span className="text-gray-300 font-semibold">Tilt</span> moves a weight at most ±30 % toward its last-14-day
            Profit% (needs ≥ 20 trades). <span className="text-gray-300 font-semibold">Brake</span> halves the weight of a
            symbol that clearly kept losing for two weeks (≥ 30 trades and −30 % or worse); it never raises a weight.
          </p>
          <p className="text-amber-400/80">
            On testnet history, the last 7–14 days’ Profit% did not predict the next days. Trust a policy only after its
            track record below beats “current weights” over many days.
          </p>
        </div>

        <div className="rounded border border-gray-800 px-3 py-2 text-[11px] font-mono space-y-1">
          <TrackLine label="History (reconstructed)" t={data.history} />
          <TrackLine label="Live since deploy" t={t} />
        </div>

        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-1 font-normal">Symbol</th>
              <th className="text-right py-1 font-normal">Weight</th>
              <th className="text-right py-1 font-normal" title="Top row of Preset Efficiency (locked preset if any): Profit% (trades)">7d</th>
              <th className="text-right py-1 font-normal">14d</th>
              <th className="text-right py-1 font-normal">Tilt</th>
              <th className="text-right py-1 font-normal">Brake</th>
              <th className="text-right py-1 font-normal">Your value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([sym, r]) => {
              const cell = (pol: 'tilt' | 'brake') => {
                const v = r.policies[pol]
                if (v === r.w) return <span className="text-gray-600">=</span>
                return (
                  <button disabled={busy} onClick={() => apply({ [sym]: v })}
                    className={`px-1.5 rounded border ${v > r.w ? 'border-emerald-900/60 text-emerald-400' : 'border-amber-900/60 text-amber-400'} hover:bg-gray-800 disabled:opacity-40`}
                    title={`Apply ${pol}: ${fmtW(r.w)} → ${fmtW(v)}`}>
                    {fmtW(v)} ✓
                  </button>
                )
              }
              return (
                <tr key={sym} className="border-b border-gray-900">
                  <td className="py-1 text-indigo-300">{sym}{r.lock && <span className="ml-1 text-[9px] text-gray-500" title={`locked: ${r.lock}`}>🔒</span>}</td>
                  <td className="py-1 text-right text-gray-200">{fmtW(r.w)}</td>
                  <td className="py-1 text-right text-gray-400">{fmtPct(r.p7)}</td>
                  <td className="py-1 text-right text-gray-300">{fmtPct(r.p14)}</td>
                  <td className="py-1 text-right">{cell('tilt')}</td>
                  <td className="py-1 text-right">{cell('brake')}</td>
                  <td className="py-1 text-right">
                    <input className={`${INPUT_CLS} !w-16 text-right`} value={custom[sym] ?? ''} placeholder={fmtW(r.w)}
                      onChange={e => setCustom(c => ({ ...c, [sym]: e.target.value }))} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>

        <div className="flex flex-wrap items-center gap-2">
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.keys(tiltAll).length} onClick={() => apply(tiltAll)}>
            Apply all tilt ({Object.keys(tiltAll).length})
          </button>
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.keys(brakeAll).length} onClick={() => apply(brakeAll)}>
            Apply all brake ({Object.keys(brakeAll).length})
          </button>
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.values(custom).some(v => v.trim())}
            onClick={() => apply(Object.fromEntries(Object.entries(custom).filter(([, v]) => v.trim()).map(([s, v]) => [s, Number(v)])))}>
            Apply your values
          </button>
          {msg && <span className="text-xs font-mono text-gray-400">{msg}</span>}
          <span className="ml-auto text-[10px] text-gray-600">
            symbols with weight 0 are off for real orders and not shown · data as of {data.day ?? '—'}
          </span>
        </div>
      </div>
    </section>
  )
}
