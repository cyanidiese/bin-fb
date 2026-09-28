'use client'

// Weight suggestions: what the measured policies would set, next to the real weights.
// Nothing changes until the user clicks Apply (merged into the mode's config server-side).
// Spec: docs/specs/2026-09-28-weight-shadow-calculator.md

import { useCallback, useEffect, useState } from 'react'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS, INPUT_CLS, SAVE_BTN_CLS } from '@/lib/risk-styles'
import { useLocalStorage } from '@/lib/useLocalStorage'

type Top = [number | null, number, string | null]
interface Row { w: number; lock: string | null; p7: Top; p14: Top; policies: { static: number; tilt: number; brake: number } }
interface Score { mean: number; vsStatic: number; betterDays: number }
interface Suggestion { value: number; flags: string[]; note?: string }
type SortCol = 'symbol' | 'weight' | 'p7' | 'p14' | 'tilt' | 'brake'
interface Data {
  mode: 'test' | 'live'
  day: string | null
  tats_min_weight: number
  symbols: Record<string, Row>
  /** A value for every symbol, with the reasons to doubt it ("!"). */
  suggestions: Record<string, { tilt: Suggestion; brake: Suggestion }>
  /** Disabled in this mode's registry: no real orders whatever the weight. */
  disabled: string[]
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
/** Mirror of the brake: strong, sustained 14-day evidence (≥ 30 trades, ≥ +30 %). Shown only;
 *  Profit% did not predict the next days on testnet, so this is a prompt to look, not a rule. */
const isCandidate = (r: Row) => r.w === 0 && r.p14[0] !== null && r.p14[1] >= 30 && r.p14[0] >= 30

export default function WeightSuggestions({ mode, onApplied }: Props) {
  const [data, setData] = useState<Data | null>(null)
  const [custom, setCustom] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  // Sort by any column; remembered across reloads. null col = default (funded first).
  const [sort, setSort] = useLocalStorage<{ col: SortCol | null; dir: 'asc' | 'desc' }>(
    'risk-weight-suggestions:sort', { col: null, dir: 'desc' })
  function toggleSort(col: SortCol) {
    setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: col === 'symbol' ? 'asc' : 'desc' })
  }

  const load = useCallback(() => {
    fetch(`/api/weight-suggestions?mode=${mode}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d) })
      .catch(() => {})
  }, [mode])
  // The page remounts this per mode (key), so there is no stale mode's data to clear.
  useEffect(() => { load() }, [load])

  async function apply(changes: Record<string, number>, policy: 'tilt' | 'brake' | 'custom' = 'custom') {
    if (!data || Object.keys(changes).length === 0) return
    const disabled = new Set(data.disabled ?? [])
    const tmw = data.tats_min_weight
    const lines = Object.entries(changes).map(([s, w]) => {
      const old = data.symbols[s]?.w ?? 0
      const crosses = tmw > 0 && old > 0 && w > 0 && ((old >= tmw) !== (w >= tmw))
      const onOff = old === 0 && w > 0 ? '  ⚠ turns REAL orders ON for this symbol'
        : old > 0 && w === 0 ? '  ⚠ turns REAL orders OFF for this symbol' : ''
      const dis = disabled.has(s) && w > 0 ? '  (disabled in this mode — no real orders until enabled in Settings)' : ''
      const sg = data.suggestions?.[s]
      const why = sg ? [...new Set([sg.tilt, sg.brake].filter(x => x.value === w).flatMap(x => x.flags))] : []
      const doubts = why.length ? `\n      ! ${why.join('\n      ! ')}` : ''
      return `${s}: ${fmtW(old)} → ${fmtW(w)}${onOff}${crosses ? `  ⚠ crosses tats_min_weight ${tmw} (changes how a lone signal is sized)` : ''}${dis}${doubts}`
    })
    if (!window.confirm(`Apply these ${mode.toUpperCase()} weights?\n\n${lines.join('\n')}\n\nThe bot uses them from the next candle. Every change is logged.`)) return
    setBusy(true); setMsg(null)
    try {
      const r = await fetch('/api/weight-suggestions/apply', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, changes, policies: Object.fromEntries(Object.keys(changes).map(k => [k, policy])) }),
      })
      const d = await r.json()
      if (!r.ok) { setMsg(d.error ?? `HTTP ${r.status}`); return }
      setMsg(`Applied ${Object.keys(changes).length} weight(s) ✓`)
      setCustom({})
      load(); onApplied()
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  if (!data) return null
  const disabled = new Set(data.disabled ?? [])
  const p14 = (r: Row) => r.p14[0] ?? -Infinity
  // Funded first (by weight), then weight 0 by 14-day Profit% — every symbol is checked.
  const sug = (sym: string, pol: 'tilt' | 'brake'): Suggestion =>
    data.suggestions?.[sym]?.[pol] ?? { value: data.symbols[sym].policies[pol], flags: [] }
  const key = (sym: string, r: Row, col: SortCol): number | string => {
    switch (col) {
      case 'symbol': return sym
      case 'weight': return r.w
      case 'p7': return r.p7[0] ?? -Infinity
      case 'p14': return r.p14[0] ?? -Infinity
      case 'tilt': return sug(sym, 'tilt').value
      case 'brake': return sug(sym, 'brake').value
    }
  }
  const rows = Object.entries(data.symbols).sort((a, b) => {
    if (sort.col) {
      const ka = key(a[0], a[1], sort.col), kb = key(b[0], b[1], sort.col)
      const c = typeof ka === 'string' ? ka.localeCompare(kb as string) : (ka as number) - (kb as number)
      if (c !== 0) return sort.dir === 'asc' ? c : -c
      return a[0].localeCompare(b[0])
    }
    // default: funded first (by weight), then weight 0 by 14-day Profit%
    return (b[1].w > 0 ? 1 : 0) - (a[1].w > 0 ? 1 : 0) || b[1].w - a[1].w || p14(b[1]) - p14(a[1])
  })
  const pending = (pol: 'tilt' | 'brake') =>
    // bulk apply takes only well-supported values (no "!") — flagged ones are per-row decisions
    Object.fromEntries(rows.filter(([s, r]) => sug(s, pol).value !== r.w && sug(s, pol).flags.length === 0)
      .map(([s]) => [s, sug(s, pol).value]))
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
              {([
                ['symbol', 'Symbol', 'text-left', ''],
                ['weight', 'Weight', 'text-right', ''],
                ['p7', '7d', 'text-right', 'Top row of Preset Efficiency (locked preset if any): Profit% (trades), last 7 days'],
                ['p14', '14d', 'text-right', 'Same, last 14 days — what tilt and brake are computed from'],
                ['tilt', 'Tilt', 'text-right', 'Weight moved at most ±30 % toward the 14-day Profit%. Weight 0: starts from tats_min_weight, only when Profit% > 0'],
                ['brake', 'Brake', 'text-right', 'Halves the weight after ≤ −30 % over 14 days; never raises a weight'],
              ] as [SortCol, string, string, string][]).map(([col, label, align, tip]) => (
                <th key={col} className={`${align} py-1 font-normal cursor-pointer select-none hover:text-gray-300`}
                  title={`${tip ? tip + ' — ' : ''}click to sort`} onClick={() => toggleSort(col)}>
                  {label}{sort.col === col ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                </th>
              ))}
              <th className="text-right py-1 font-normal">Your value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([sym, r]) => {
              const cell = (pol: 'tilt' | 'brake') => {
                const { value: v, flags, note } = sug(sym, pol)
                const warn = flags.length > 0 && (
                  <span className="ml-0.5 text-amber-400 cursor-help font-bold" title={flags.map(f => '• ' + f).join('\n')}>!</span>
                )
                if (v === r.w) return <span className="text-gray-600" title={[note, ...flags].filter(Boolean).join('\n') || 'no change'}>={warn}</span>
                return (
                  <span className="whitespace-nowrap">
                    <button disabled={busy} onClick={() => apply({ [sym]: v }, pol)}
                      className={`px-1.5 rounded border ${v > r.w ? 'border-emerald-900/60 text-emerald-400' : 'border-amber-900/60 text-amber-400'} ${flags.length ? 'opacity-70' : ''} hover:bg-gray-800 disabled:opacity-40`}
                      title={`Apply ${pol}: ${fmtW(r.w)} → ${fmtW(v)}${note ? '\n' + note : ''}${flags.length ? '\n' + flags.map(f => '! ' + f).join('\n') : ''}`}>
                      {fmtW(v)} ✓
                    </button>{warn}
                  </span>
                )
              }
              return (
                <tr key={sym} className={`border-b border-gray-900 ${r.w === 0 ? 'opacity-80' : ''}`}>
                  <td className="py-1 text-indigo-300">
                    {sym}
                    {r.lock && <span className="ml-1 text-[9px] text-gray-500" title={`locked: ${r.lock}`}>🔒</span>}
                    {disabled.has(sym) && <span className="ml-1.5 text-[9px] text-red-400 uppercase" title="Disabled in this mode: no real orders whatever the weight (virtual orders continue)">off</span>}
                    {isCandidate(r) && <span className="ml-1.5 text-[9px] px-1 rounded border border-emerald-900/60 text-emerald-400"
                      title="Weight 0, but ≥ +30 % over 14 days with ≥ 30 trades. Profit% did not predict the next days on testnet — a prompt to look, not a rule.">candidate</span>}
                  </td>
                  <td className={`py-1 text-right ${r.w > 0 ? 'text-gray-200' : 'text-gray-600'}`}>{fmtW(r.w)}</td>
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
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.keys(tiltAll).length} onClick={() => apply(tiltAll, 'tilt')}>
            Apply all well-supported tilt ({Object.keys(tiltAll).length})
          </button>
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.keys(brakeAll).length} onClick={() => apply(brakeAll, 'brake')}>
            Apply all well-supported brake ({Object.keys(brakeAll).length})
          </button>
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.values(custom).some(v => v.trim())}
            onClick={() => apply(Object.fromEntries(Object.entries(custom).filter(([, v]) => v.trim()).map(([s, v]) => [s, Number(v)])))}>
            Apply your values
          </button>
          {msg && <span className="text-xs font-mono text-gray-400">{msg}</span>}
          <span className="ml-auto text-[10px] text-gray-600">
            weight 0 = no real orders (virtual orders continue) · data as of {data.day ?? '—'}
          </span>
        </div>
      </div>
    </section>
  )
}
