'use client'

import { useState, useEffect, useCallback } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'

// ── Types ─────────────────────────────────────────────────────────────────────

interface BalanceTier {
  min_balance_usdt: number
  max_deploy_pct: number
  max_leverage_ceiling: number
}

interface RiskConfig {
  balance_tiers: BalanceTier[]
  base_leverage: number
  max_leverage: number
  min_profit_factor: number
  drawdown_warning_pct: number
  drawdown_hard_stop_pct: number
  backtest_initial_balance_usdt: number
  symbol_weights: Record<string, number>
  max_leverage_level: number
  use_allocation_weighting: boolean
}

interface PerSymbol {
  allocation_usdt: number
  leverage: number
  performance_score: number
}

interface RiskState {
  generated_at: string
  mode: string
  balance: number
  peak_balance: number
  drawdown_pct: number
  warning_active: boolean
  hard_stop_active: boolean
  active_tier: BalanceTier
  last_event: string
  last_event_time: string
  per_symbol: Record<string, PerSymbol>
}

// ── Constants ─────────────────────────────────────────────────────────────────

const POLL_MS = 5000

const INPUT_CLS =
  'bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 text-xs font-mono ' +
  'focus:outline-none focus:border-indigo-500 disabled:opacity-40 w-24'

const SECTION_CLS = 'rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden'
const SECTION_HEADER_CLS =
  'px-4 py-2 border-b border-gray-800 text-xs font-semibold text-gray-400 uppercase tracking-wide'
const SECTION_BODY_CLS = 'px-4 py-4 space-y-4'

const SAVE_BTN_CLS =
  'px-4 py-1.5 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs ' +
  'font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors'

// ── Helpers ───────────────────────────────────────────────────────────────────

function LabeledInput({
  label, tooltip, value, onChange, type = 'number', min, max, step, disabled,
}: {
  label: string; tooltip: string; value: number | string
  onChange: (v: string) => void; type?: string
  min?: number; max?: number; step?: number; disabled?: boolean
}) {
  return (
    <div className="flex items-center gap-3">
      <label
        className="text-xs text-gray-500 w-52 shrink-0"
        title={tooltip}
      >
        {label}
      </label>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        title={tooltip}
        onChange={e => onChange(e.target.value)}
        className={INPUT_CLS}
      />
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function RiskPage() {
  const { availableSymbols } = useSymbolContext()
  const [config, setConfig] = useState<RiskConfig | null>(null)
  const [state, setState] = useState<RiskState | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)

  // ── Load config once ────────────────────────────────────────────────────────
  useEffect(() => {
    fetch('/api/risk')
      .then(r => r.json())
      .then(({ config: cfg }) => setConfig(cfg))
      .catch(() => {})
  }, [])

  // ── Poll risk_state.json ────────────────────────────────────────────────────
  const pollState = useCallback(() => {
    fetch(`/risk_state.json?t=${Date.now()}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setState(data) })
      .catch(() => {})
  }, [])

  useEffect(() => {
    pollState()
    const id = setInterval(pollState, POLL_MS)
    return () => clearInterval(id)
  }, [pollState])

  // ── Ensure every active symbol has a weight entry ───────────────────────────
  useEffect(() => {
    if (!config || availableSymbols.length === 0) return
    const w = { ...config.symbol_weights }
    let changed = false
    for (const sym of availableSymbols) {
      if (!(sym in w)) { w[sym] = 1; changed = true }
    }
    if (changed) setConfig(c => c ? { ...c, symbol_weights: w } : c)
  }, [availableSymbols, config?.symbol_weights])

  async function handleSave() {
    if (!config) return
    setSaving(true)
    setSaveError(null)
    setSaveOk(false)
    try {
      const res = await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      })
      const data = await res.json()
      if (!res.ok) { setSaveError(data.error ?? `HTTP ${res.status}`); return }
      setSaveOk(true)
      setTimeout(() => setSaveOk(false), 3000)
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  function patchConfig(patch: Partial<RiskConfig>) {
    setConfig(c => c ? { ...c, ...patch } : c)
  }

  function patchTier(idx: number, patch: Partial<BalanceTier>) {
    if (!config) return
    const tiers = config.balance_tiers.map((t, i) => i === idx ? { ...t, ...patch } : t)
    patchConfig({ balance_tiers: tiers })
  }

  function addTier() {
    if (!config) return
    patchConfig({
      balance_tiers: [
        ...config.balance_tiers,
        { min_balance_usdt: 0, max_deploy_pct: 40, max_leverage_ceiling: 5 },
      ],
    })
  }

  function removeTier(idx: number) {
    if (!config || config.balance_tiers.length <= 1) return
    patchConfig({ balance_tiers: config.balance_tiers.filter((_, i) => i !== idx) })
  }

  const totalWeight = config
    ? Object.values(config.symbol_weights).reduce((a, b) => a + b, 0) || 1
    : 1

  if (!config) {
    return <main className="p-6 text-gray-500 text-sm">Loading risk config…</main>
  }

  return (
    <main className="p-4 space-y-6 max-w-3xl">
      {/* Title row */}
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-bold text-white">Risk Manager</h1>
        <div className="ml-auto flex items-center gap-3">
          {saveError && <span className="text-xs text-red-400 font-mono">{saveError}</span>}
          {saveOk && <span className="text-xs text-emerald-400 font-mono">Saved ✓</span>}
          <button
            onClick={handleSave}
            disabled={saving}
            title="Save all risk settings to disk. The bot picks up changes within 60 seconds."
            className={SAVE_BTN_CLS}
          >
            {saving ? 'Saving…' : 'Save All'}
          </button>
        </div>
      </div>

      {/* ── Section A — Global Capital Rules ─────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Controls how much of your balance is deployed across all symbols combined.">
          A — Global Capital Rules
        </p>
        <div className={SECTION_BODY_CLS}>

          {/* Active tier display */}
          {state && (
            <div className="text-xs font-mono text-gray-500 bg-gray-800/60 rounded px-3 py-2">
              <span title="The balance tier currently active, based on your live balance.">
                Active tier:
              </span>{' '}
              balance ≥ ${state.active_tier.min_balance_usdt.toLocaleString()} →{' '}
              deploy up to{' '}
              <span className="text-indigo-300">{state.active_tier.max_deploy_pct}%</span>,{' '}
              max leverage{' '}
              <span className="text-indigo-300">{state.active_tier.max_leverage_ceiling}×</span>
            </div>
          )}

          {/* Balance tiers editor */}
          <div>
            <p
              className="text-xs text-gray-500 mb-2"
              title="Define balance thresholds that unlock higher deployment caps and leverage ceilings. The highest tier whose min_balance ≤ current balance is active."
            >
              Balance tiers
            </p>
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="text-gray-600 border-b border-gray-800">
                  <th className="text-left py-1 pr-4 font-normal" title="Minimum balance in USDT to activate this tier.">Min balance (USDT)</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Maximum % of available balance that may be deployed across all symbols when this tier is active.">Max deploy %</th>
                  <th className="text-left py-1 pr-4 font-normal" title="Maximum leverage ceiling for any symbol when this tier is active. Further limited by max_leverage.">Leverage ceiling</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {config.balance_tiers.map((tier, idx) => (
                  <tr key={idx} className="border-b border-gray-900">
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={0} step={100}
                        value={tier.min_balance_usdt}
                        title="Minimum account balance (USDT) to activate this tier."
                        onChange={e => patchTier(idx, { min_balance_usdt: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={1} max={100} step={1}
                        value={tier.max_deploy_pct}
                        title="Maximum % of available balance to deploy across all symbols when this tier is active."
                        onChange={e => patchTier(idx, { max_deploy_pct: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 pr-4">
                      <input
                        type="number" min={1} max={125} step={1}
                        value={tier.max_leverage_ceiling}
                        title="Hard cap on leverage for any symbol in this tier. Overrides max_leverage if lower."
                        onChange={e => patchTier(idx, { max_leverage_ceiling: Number(e.target.value) })}
                        className={INPUT_CLS}
                      />
                    </td>
                    <td className="py-1 text-right">
                      <button
                        onClick={() => removeTier(idx)}
                        disabled={config.balance_tiers.length <= 1}
                        title="Remove this tier. At least one tier must remain."
                        className="text-[10px] text-red-500 hover:text-red-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button
              onClick={addTier}
              title="Add a new balance tier row."
              className="mt-2 text-[10px] text-indigo-400 hover:text-indigo-300 transition-colors"
            >
              + Add tier
            </button>
          </div>

          <LabeledInput
            label="Backtest initial balance (USDT)"
            tooltip="Starting balance used when simulating capital deployment during backtests. Higher values produce more stable drawdown percentages."
            value={config.backtest_initial_balance_usdt}
            onChange={v => patchConfig({ backtest_initial_balance_usdt: Number(v) })}
            min={10} step={100}
          />
        </div>
      </section>

      {/* ── Section B — Per-Symbol Allocation ────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Relative weights determine how the deployable budget is split across active symbols.">
          B — Per-Symbol Allocation
        </p>
        <div className={SECTION_BODY_CLS}>
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="text-gray-600 border-b border-gray-800">
                <th className="text-left py-1 pr-4 font-normal" title="Binance USD-M Futures symbol.">Symbol</th>
                <th className="text-left py-1 pr-4 font-normal" title="Relative weight. Higher weight = larger share of deployable capital.">Weight</th>
                <th className="text-left py-1 pr-4 font-normal" title="Computed share of the deployable budget this symbol receives (weight ÷ total weights).">Alloc %</th>
                <th className="text-left py-1 pr-4 font-normal" title="Current USDT allocation for this symbol, based on live balance and active tier.">Alloc USDT</th>
                <th className="text-left py-1 pr-4 font-normal" title="Dynamic leverage currently assigned to this symbol by the risk manager.">Leverage</th>
                <th className="text-left py-1 font-normal" title="Normalised performance score (0–1) from the best backtest preset for this symbol. Drives the leverage formula.">Perf score</th>
              </tr>
            </thead>
            <tbody>
              {availableSymbols.map(sym => {
                const w = config.symbol_weights[sym] ?? 1
                const allocPct = (w / totalWeight * 100).toFixed(1)
                const live = state?.per_symbol[sym]
                return (
                  <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                    <td className="py-1.5 pr-4 text-indigo-300 font-semibold">{sym}</td>
                    <td className="py-1.5 pr-4">
                      <input
                        type="number" min={1} step={1}
                        value={w}
                        title={`Relative capital weight for ${sym}. Increase to allocate more USDT to this symbol.`}
                        onChange={e => patchConfig({
                          symbol_weights: { ...config.symbol_weights, [sym]: Number(e.target.value) },
                        })}
                        className={INPUT_CLS + ' w-16'}
                      />
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">{allocPct}%</td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {live ? `$${live.allocation_usdt.toFixed(0)}` : '—'}
                    </td>
                    <td className="py-1.5 pr-4 text-gray-400">
                      {live ? `${live.leverage}×` : '—'}
                    </td>
                    <td className="py-1.5 text-gray-400">
                      {live ? live.performance_score.toFixed(3) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Section C — Leverage Controls ────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Global leverage bounds. Actual leverage is computed per symbol from the performance score formula, then capped by the active balance tier.">
          C — Leverage Controls
        </p>
        <div className={SECTION_BODY_CLS}>
          <LabeledInput
            label="Base leverage"
            tooltip="Minimum leverage assigned to any symbol, regardless of performance score. Applied when score = 0."
            value={config.base_leverage}
            onChange={v => patchConfig({ base_leverage: Number(v) })}
            min={1} max={20} step={1}
          />
          <LabeledInput
            label="Max leverage"
            tooltip="Upper bound for leverage before the balance-tier ceiling is applied. Applied when performance score = 1."
            value={config.max_leverage}
            onChange={v => patchConfig({ max_leverage: Number(v) })}
            min={1} max={125} step={1}
          />
          <LabeledInput
            label="Min profit factor"
            tooltip="Minimum true profit factor (realized gains ÷ realized losses from best backtest preset) required to allow trading a symbol. Below this, can_open() returns False regardless of capital availability."
            value={config.min_profit_factor}
            onChange={v => patchConfig({ min_profit_factor: Number(v) })}
            min={0.1} max={10} step={0.1}
          />
          <LabeledInput
            label="Max leverage level"
            tooltip="LeverageTracker ceiling — global level will not advance past this value (1–20)"
            value={config.max_leverage_level ?? 5}
            onChange={v => patchConfig({ max_leverage_level: Number(v) })}
            min={1}
            max={20}
            step={1}
          />
          <div className="flex items-center gap-3">
            <label className="text-xs text-gray-500 w-52 shrink-0" title="When disabled, position size = min_notional / leverage. Enable to use per-symbol weighted allocation.">
              Use allocation weighting
            </label>
            <input
              type="checkbox"
              checked={config.use_allocation_weighting ?? false}
              onChange={e => patchConfig({ use_allocation_weighting: e.target.checked })}
              className="accent-indigo-500 h-4 w-4 cursor-pointer"
            />
          </div>
          <p className="text-[10px] text-gray-600 font-mono">
            Formula: leverage = base + floor(score × (min(max, tier_ceiling) − base))
          </p>
        </div>
      </section>

      {/* ── Section D — Drawdown Guard ───────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p className={SECTION_HEADER_CLS} title="Protects capital from large losing streaks. The hard stop is latched and requires a manual reset.">
          D — Drawdown Guard
        </p>
        <div className={SECTION_BODY_CLS}>
          <LabeledInput
            label="Warning threshold %"
            tooltip="When drawdown from peak balance exceeds this %, a warning banner appears and a risk event is logged. Resets automatically when balance recovers."
            value={config.drawdown_warning_pct}
            onChange={v => patchConfig({ drawdown_warning_pct: Number(v) })}
            min={1} max={50} step={0.5}
          />
          <LabeledInput
            label="Hard stop threshold %"
            tooltip="When drawdown from peak exceeds this %, all new entries are blocked. Existing orders close naturally. Requires manual reset — does not auto-reset."
            value={config.drawdown_hard_stop_pct}
            onChange={v => patchConfig({ drawdown_hard_stop_pct: Number(v) })}
            min={1} max={100} step={0.5}
          />

          {/* Live drawdown display */}
          <div className="flex items-center gap-6 text-xs font-mono">
            <div title="Current drawdown from peak balance, updated every 5 seconds.">
              <span className="text-gray-600">Current drawdown: </span>
              <span className={
                state
                  ? state.drawdown_pct >= config.drawdown_hard_stop_pct
                    ? 'text-red-400'
                    : state.drawdown_pct >= config.drawdown_warning_pct
                    ? 'text-amber-400'
                    : 'text-emerald-400'
                  : 'text-gray-600'
              }>
                {state ? `${state.drawdown_pct.toFixed(2)}%` : '—'}
              </span>
            </div>

            {/* Hard stop indicator */}
            <div className="flex items-center gap-2" title="Hard stop status. Red = active, all new entries blocked.">
              <span className="text-gray-600">Hard stop:</span>
              <span className={`font-semibold ${state?.hard_stop_active ? 'text-red-400' : 'text-emerald-400'}`}>
                {state ? (state.hard_stop_active ? '● ACTIVE' : '● OK') : '—'}
              </span>
            </div>

            {/* Reset button — only shown when hard stop is active */}
            {state?.hard_stop_active && (
              <button
                onClick={() => {
                  if (window.confirm(
                    'Reset the hard stop latch?\n\n' +
                    'This allows new entries again. Only do this after reviewing ' +
                    'your drawdown and confirming you are ready to resume trading.'
                  )) {
                    fetch('/api/risk', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ ...config, _reset_hard_stop: true }),
                    })
                  }
                }}
                title="Reset the drawdown hard stop latch. Requires confirmation. The bot must be restarted to take effect."
                className="px-3 py-1 rounded border border-red-700 bg-red-950/40 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 transition-colors"
              >
                Reset drawdown guard
              </button>
            )}
          </div>

          {/* Warning banner */}
          {state?.warning_active && !state.hard_stop_active && (
            <div
              className="rounded border border-amber-700/60 bg-amber-900/20 px-3 py-2 text-xs text-amber-300 font-mono"
              title="Drawdown has crossed the warning threshold. Review your positions."
            >
              ⚠ Drawdown warning — {state.drawdown_pct.toFixed(2)}% from peak ${state.peak_balance.toFixed(2)}
            </div>
          )}

          {state?.hard_stop_active && (
            <div
              className="rounded border border-red-700/60 bg-red-900/20 px-3 py-2 text-xs text-red-300 font-mono"
              title="Hard stop is active. No new entries will be opened until it is manually reset."
            >
              ✗ HARD STOP ACTIVE — all new entries blocked. Reset to resume.
            </div>
          )}
        </div>
      </section>

      {/* ── Section E — Live Risk State ──────────────────────────────────── */}
      <section className={SECTION_CLS}>
        <p
          className={SECTION_HEADER_CLS}
          title="Read-only snapshot from risk_state.json, updated by the bot after each balance change. Polling every 5 seconds."
        >
          E — Live Risk State
          {state && (
            <span className="ml-2 text-[10px] text-gray-600 normal-case tracking-normal font-normal">
              updated {new Date(state.generated_at).toLocaleTimeString()}
            </span>
          )}
        </p>
        <div className="px-4 py-4">
          {!state ? (
            <p className="text-xs text-gray-600 italic">
              No risk_state.json yet — start the bot to generate it.
            </p>
          ) : (
            <div className="space-y-3">
              {/* Key stats */}
              <div className="flex flex-wrap gap-6 text-xs font-mono">
                {[
                  { label: 'Mode',    value: state.mode,                              color: 'text-gray-300' },
                  { label: 'Balance', value: `$${state.balance.toFixed(2)}`,          color: 'text-gray-300' },
                  { label: 'Peak',    value: `$${state.peak_balance.toFixed(2)}`,     color: 'text-gray-300' },
                  { label: 'Drawdown',value: `${state.drawdown_pct.toFixed(2)}%`,
                    color: state.drawdown_pct >= config.drawdown_hard_stop_pct
                      ? 'text-red-400'
                      : state.drawdown_pct >= config.drawdown_warning_pct
                      ? 'text-amber-400'
                      : 'text-emerald-400' },
                  { label: 'Last event', value: state.last_event || 'none',           color: 'text-gray-500' },
                ].map(s => (
                  <div key={s.label}>
                    <span className="text-gray-600">{s.label}: </span>
                    <span className={s.color}>{s.value}</span>
                  </div>
                ))}
              </div>

              {/* Raw JSON */}
              <details>
                <summary
                  className="text-[10px] text-gray-600 cursor-pointer hover:text-gray-400 transition-colors"
                  title="Expand to see the full raw risk_state.json snapshot."
                >
                  Raw snapshot
                </summary>
                <pre className="mt-2 text-[10px] text-gray-500 font-mono bg-gray-900 rounded p-3 overflow-x-auto max-h-64">
                  {JSON.stringify(state, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
