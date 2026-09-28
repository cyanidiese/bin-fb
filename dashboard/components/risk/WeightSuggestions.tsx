'use client'

// Weight suggestions: what the measured policies would set, next to the real weights.
// Nothing changes until the user clicks Apply (merged into the mode's config server-side).
// Tilt = score allocation over the top N symbols (the rest 0); Brake = halve after a
// sustained 14-day loss. Spec: docs/specs/2026-09-28-weight-shadow-calculator.md

import { useCallback, useEffect, useState } from 'react'
import { SECTION_CLS, SECTION_HEADER_CLS, SECTION_BODY_CLS, INPUT_CLS, SAVE_BTN_CLS } from '@/lib/risk-styles'
import { useLocalStorage } from '@/lib/useLocalStorage'

type Top = [number | null, number, string | null]
interface Row {
  w: number; lock: string | null; p7: Top; p14: Top
  c7?: [number, number]; c14?: [number, number]
  policies: { static: number; tilt: number; brake: number }
}
interface Score { mean: number; vsStatic: number; betterDays: number }
interface Suggestion { value: number; flags: string[]; note?: string }
interface Alloc { score: number; s7: number; s14: number; rank: number | null; inTopN: boolean; value: number }
interface ScoreHist { days: number; mean: number; vs_equal: number; vs_current: number; better_equal: number }
type SortCol = 'symbol' | 'weight' | 'p7' | 'p14' | 'tilt' | 'brake'
interface Track { snapshots: number; evaluated: number; days: number; scores: Record<'static' | 'tilt' | 'brake', Score> | null }
interface Data {
  mode: 'test' | 'live'
  day: string | null
  tats_min_weight: number
  budget: number
  n: number
  eligible: number
  symbols: Record<string, Row>
  allocation: Record<string, Alloc>
  /** A value for every symbol, with the reasons to doubt it ("!"). */
  suggestions: Record<string, { tilt: Suggestion; brake: Suggestion }>
  /** Disabled in this mode's registry: no real orders whatever the weight. */
  disabled: string[]
  track: Track
  /** Reconstructed from order history (scripts/backfill_weight_shadow.py). */
  history: Track
  /** This Tilt formula's reconstructed history for the chosen N. */
  score_history: { top?: ScoreHist; r1?: ScoreHist } | null
}

interface Props {
  mode: 'test' | 'live'
  /** Called after weights (or locks) were saved, so the page reloads its config. */
  onApplied: () => void
}

const fmtPct = (t: Top) => t[0] === null ? '—' : `${t[0] > 0 ? '+' : ''}${t[0].toFixed(1)}% (${t[1]})`
const fmtW = (w: number) => Number.isInteger(w) ? String(w) : w.toFixed(2)
const sign = (x: number) => `${x >= 0 ? '+' : ''}${x.toFixed(2)}`
/** Strong, sustained 14-day evidence on an unfunded symbol (≥ 30 trades, ≥ +30 %). */
const isCandidate = (r: Row) => r.w === 0 && r.p14[0] !== null && r.p14[1] >= 30 && r.p14[0] >= 30

function countsTip(label: string, c: [number, number] | undefined, t: Top): string {
  const lines = [c ? `${c[0]} of ${c[1]} presets are profitable (${label})` : `no preset data (${label})`]
  if (t[2]) lines.push(`shown: ${t[2]} — Profit% ${t[0] === null ? '—' : sign(t[0]) + ' %'} over ${t[1]} trade(s)`)
  if (c && c[1] > 0 && c[0] <= 2) lines.push('few profitable presets: a strong number may be one lucky preset')
  return lines.join('\n')
}

export default function WeightSuggestions({ mode, onApplied }: Props) {
  const [data, setData] = useState<Data | null>(null)
  const [custom, setCustom] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  // Symbols involved in the Tilt allocation; remembered per mode. 0 = all eligible.
  const [nInput, setNInput] = useLocalStorage<number>(`risk-weight-suggestions:n:${mode}`, 0)
  const [nApplied, setNApplied] = useState<number | null>(null)
  // Sort by any column; remembered across reloads. null col = default (funded first).
  const [sort, setSort] = useLocalStorage<{ col: SortCol | null; dir: 'asc' | 'desc' }>(
    'risk-weight-suggestions:sort', { col: null, dir: 'desc' })
  function toggleSort(col: SortCol) {
    setSort(s => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: col === 'symbol' ? 'asc' : 'desc' })
  }

  const load = useCallback((n?: number) => {
    const q = n && n > 0 ? `&n=${n}` : ''
    fetch(`/api/weight-suggestions?mode=${mode}${q}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setData(d); setNApplied(d.n) } })
      .catch(() => {})
  }, [mode])
  // The page remounts this per mode (key). First load uses the remembered N.
  useEffect(() => {
    let n = 0
    try { n = Number(JSON.parse(localStorage.getItem(`risk-weight-suggestions:n:${mode}`) ?? '0')) || 0 } catch { /* none */ }
    load(n)
  }, [load, mode])

  function recalculate() { setMsg(null); load(nInput) }

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
      load(nApplied ?? nInput); onApplied()
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  /** Lock the symbol's 14-day top preset, or unlock it; then recalculate. */
  async function toggleLock(sym: string, r: Row) {
    const target = r.lock ? null : (r.p14[2] ?? r.p7[2])
    if (!r.lock && !target) { setMsg(`${sym}: no top preset to lock (no trades in 14 days)`); return }
    const text = r.lock
      ? `Unlock ${sym} (${mode.toUpperCase()})?\n\nIt is locked to ${r.lock}. Unlocked, real orders use whichever preset ranks best, and this table recalculates with that top preset.`
      : `Lock ${sym} (${mode.toUpperCase()}) to ${target}?\n\nThat is its top preset over 14 days (${fmtPct(r.p14)}). Real orders will use only this preset until unlocked.`
    if (!window.confirm(text)) return
    setBusy(true); setMsg(null)
    try {
      const res = await fetch('/api/risk/lock-preset', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: sym, preset: target, mode }),
      })
      const d = await res.json()
      if (!res.ok) { setMsg(d.error ?? `HTTP ${res.status}`); return }
      setMsg(r.lock ? `${sym} unlocked ✓` : `${sym} locked to ${target} ✓`)
      load(nApplied ?? nInput); onApplied()
    } catch (e) { setMsg(String(e)) } finally { setBusy(false) }
  }

  if (!data) return null
  const disabled = new Set(data.disabled ?? [])
  const p14 = (r: Row) => r.p14[0] ?? -Infinity
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
  const changed = (pol: 'tilt' | 'brake', onlyUnflagged: boolean) =>
    Object.fromEntries(rows.filter(([s, r]) => sug(s, pol).value !== r.w && (!onlyUnflagged || sug(s, pol).flags.length === 0))
      .map(([s]) => [s, sug(s, pol).value]))
  const tiltAll = changed('tilt', false), brakeAll = changed('brake', true)
  const sh = data.score_history
  const nWanted = nInput > 0 ? Math.min(nInput, data.eligible) : data.eligible   // empty/0 = all eligible
  const nStale = nApplied !== null && nWanted !== nApplied
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
            <span className="text-gray-300 font-semibold">Tilt</span> shares the budget ({fmtW(data.budget)}, the current
            total weight) among the <span className="text-gray-300">top N</span> enabled symbols by score, in proportion to
            score, at most 30 % each (with N ≤ 3 an equal share, so the whole budget is used); every other symbol gets 0. Score = ½ × 7-day + ½ × 14-day Profit%, each shrunk by its
            trade count (few trades count for little). <span className="text-gray-300 font-semibold">Brake</span> halves the
            weight of a symbol that clearly kept losing for two weeks (≥ 30 trades and −30 % or worse); it never raises one.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label className="flex items-center gap-2 text-gray-400"
            title={`How many symbols take part in the Tilt allocation. The top N by score share the budget; all others get 0. ${data.eligible} symbol(s) are eligible (enabled, positive score). Empty or 0 = all eligible.`}>
            Symbols involved
            <input type="number" min={0} max={data.eligible} step={1}
              className={`${INPUT_CLS} !w-16 text-right`}
              value={nInput > 0 ? nInput : ''} placeholder={String(data.eligible)}
              onChange={e => setNInput(Math.max(0, Math.floor(Number(e.target.value) || 0)))}
              onKeyDown={e => { if (e.key === 'Enter') recalculate() }} />
            <span className="text-gray-600">of {data.eligible} eligible</span>
          </label>
          <button className={SAVE_BTN_CLS} disabled={busy} onClick={recalculate}
            title="Recompute the table with the chosen number of symbols and the latest Profit% and locks">
            ↻ Recalculate
          </button>
          {nStale && <span className="text-amber-400/80">N changed — press Recalculate</span>}
          <span className="text-gray-600">showing top {data.n}</span>
        </div>

        <div className="rounded border border-gray-800 px-3 py-2 text-[11px] font-mono space-y-1">
          {sh?.top ? (
            <div className={sh.top.vs_equal > 0 ? 'text-emerald-400' : 'text-amber-400/90'}>
              History of this Tilt (top {data.n}, reconstructed {sh.top.days} days, next 7 days): {sign(sh.top.mean)} %/week —
              {' '}{sign(sh.top.vs_equal)} vs equal weights (better on {sh.top.better_equal}/{sh.top.days} days),
              {' '}{sign(sh.top.vs_current)} vs today’s weights
              {sh.r1 && <span className="text-gray-500"> · would-be-real trades ({sh.r1.days} days): {sign(sh.r1.vs_equal)} vs equal</span>}
            </div>
          ) : (
            <div className="text-gray-500">History of this Tilt: not computed yet — run scripts/backfill_weight_shadow.py {data.mode} --write</div>
          )}
          {data.history.scores && (
            <div className="text-gray-400">
              History of Brake ({data.history.evaluated} days): {sign(data.history.scores.brake.vsStatic)} %/week vs current weights,
              better on {data.history.scores.brake.betterDays}/{data.history.evaluated} days
            </div>
          )}
          <div className="text-gray-500">Live since deploy: {t.snapshots} daily snapshot(s){t.scores ? `, ${t.evaluated} scored` : ' — scored once a snapshot is 7 days old'}</div>
        </div>

        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              {([
                ['symbol', 'Symbol', 'text-left', 'Click 🔒 to lock the 14-day top preset (or unlock)'],
                ['weight', 'Weight', 'text-right', ''],
                ['p7', '7d', 'text-right', 'Top row of Preset Efficiency (locked preset if any): Profit% (trades), last 7 days. Hover a value for how many presets are profitable'],
                ['p14', '14d', 'text-right', 'Same, last 14 days'],
                ['tilt', 'Tilt', 'text-right', 'Score allocation over the top N symbols; others 0. Hover for the score'],
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
              const a = data.allocation?.[sym]
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
                <tr key={sym} className={`border-b border-gray-900 ${a && !a.inTopN && r.w === 0 ? 'opacity-70' : ''}`}>
                  <td className="py-1 text-indigo-300 whitespace-nowrap">
                    {sym}
                    <button disabled={busy} onClick={() => toggleLock(sym, r)}
                      className={`ml-1 text-[10px] disabled:opacity-40 ${r.lock ? 'text-amber-300' : 'text-gray-600 hover:text-gray-300'}`}
                      title={r.lock ? `Locked to ${r.lock} — click to unlock and recalculate` : `Click to lock the 14-day top preset${r.p14[2] ? ` (${r.p14[2]})` : ''}`}>
                      {r.lock ? '🔒' : '🔓'}
                    </button>
                    {disabled.has(sym) && <span className="ml-1.5 text-[9px] text-red-400 uppercase" title="Disabled in this mode: no real orders whatever the weight (virtual orders continue)">off</span>}
                    {isCandidate(r) && <span className="ml-1.5 text-[9px] px-1 rounded border border-emerald-900/60 text-emerald-400"
                      title="Weight 0, but ≥ +30 % over 14 days with ≥ 30 trades — a prompt to look, not a rule.">candidate</span>}
                    {a?.inTopN && <span className="ml-1.5 text-[9px] text-sky-400/80" title={`Rank ${a.rank} of ${data.eligible} by score (${a.score.toFixed(1)})`}>#{a.rank}</span>}
                  </td>
                  <td className={`py-1 text-right ${r.w > 0 ? 'text-gray-200' : 'text-gray-600'}`}>{fmtW(r.w)}</td>
                  <td className="py-1 text-right text-gray-400 cursor-help" title={countsTip('7 days', r.c7, r.p7)}>{fmtPct(r.p7)}</td>
                  <td className="py-1 text-right text-gray-300 cursor-help" title={countsTip('14 days', r.c14, r.p14)}>{fmtPct(r.p14)}</td>
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
          <button className={SAVE_BTN_CLS} disabled={busy || !Object.keys(tiltAll).length} onClick={() => apply(tiltAll, 'tilt')}
            title="Apply the Tilt allocation to every row that changes — including the zeros for symbols outside the top N. The confirmation lists each change and its reasons.">
            Apply tilt allocation ({Object.keys(tiltAll).length} change{Object.keys(tiltAll).length === 1 ? '' : 's'})
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
