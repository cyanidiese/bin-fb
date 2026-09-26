'use client'

import { useState, useEffect, useCallback } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import { RiskConfig, RiskState } from '@/lib/risk-types'
import { SAVE_BTN_CLS } from '@/lib/risk-styles'
import ScenarioSection from '@/components/risk/ScenarioSection'
import WeightRebalancerSection from '@/components/risk/WeightRebalancerSection'
import GlobalCapitalRules from '@/components/risk/GlobalCapitalRules'
import PerSymbolAllocation from '@/components/risk/PerSymbolAllocation'
import LeverageControls from '@/components/risk/LeverageControls'
import DrawdownGuard from '@/components/risk/DrawdownGuard'
import LiveRiskState from '@/components/risk/LiveRiskState'
import InstanceToggle, { oppositeMode, type Instance } from '@/components/InstanceToggle'
import PresetRankingSection from '@/components/risk/PresetRankingSection'
import MaxLossSection from '@/components/risk/MaxLossSection'

const POLL_MS = 5000

export default function RiskPage() {
  const { availableSymbols } = useSymbolContext()
  const [config, setConfig] = useState<RiskConfig | null>(null)
  const [state, setState] = useState<RiskState | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)

  useEffect(() => {
    fetch('/api/risk')
      .then(r => r.json())
      .then(({ config: cfg }) => setConfig(cfg))
      .catch(() => {})
  }, [])

  // Instance switcher, same as the Trades page (and the same saved choice). Settings are
  // SHARED by both instances — the mirror mounts risk_config.json read-only so both run
  // identical rules; only locked presets are per mode, and they are edited on the Trades
  // page. What differs per instance is the live state each one writes: the primary's
  // risk_state.json, the mirror's risk_state_{mode}.json (bot/instance_paths.py).
  const [instance, setInstance] = useState<Instance>('primary')
  const [botMode, setBotMode] = useState<'test' | 'live'>('test')
  useEffect(() => {
    fetch('/api/mode')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.mode === 'live' || d?.mode === 'test') setBotMode(d.mode)
        try {
          const saved = localStorage.getItem('bfb-instance')
          if (saved === 'shadow' || saved === 'primary') setInstance(saved)
        } catch { /* private window — keep the default */ }
      })
      .catch(() => {})
  }, [])
  function chooseInstance(i: Instance) {
    setInstance(i)
    setState(null)   // never show one instance's numbers under the other's label
    try { localStorage.setItem('bfb-instance', i) } catch { /* private window */ }
  }
  const dataMode = instance === 'primary' ? botMode : oppositeMode(botMode)
  const stateFile = instance === 'primary' ? 'risk_state.json' : `risk_state_${dataMode}.json`

  const pollState = useCallback(() => {
    fetch(`/api/public-file?f=${stateFile}`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setState(data) })
      .catch(() => {})
  }, [stateFile])

  useEffect(() => {
    pollState()
    const id = setInterval(pollState, POLL_MS)
    return () => clearInterval(id)
  }, [pollState])

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

  if (!config) {
    return <main className="p-6 text-gray-500 text-sm">Loading risk config…</main>
  }

  const scenario = (config.scenario ?? 'default') as string

  return (
    <main className="p-4 space-y-6 max-w-3xl">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-lg font-bold text-white">Risk Manager</h1>
        <label className="flex items-center gap-2" title="Switches the LIVE STATE shown (balance, drawdown, per-symbol scores). Settings below are shared by both instances.">
          <span className="text-[11px] uppercase tracking-wider text-gray-500">Instance</span>
          <InstanceToggle value={instance} onChange={chooseInstance} botMode={botMode} />
        </label>
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

      <p className="text-[11px] text-gray-500 -mt-3">
        Settings are shared by both instances (Save All writes the one risk_config.json);
        the Instance switch changes only the live state shown
        {instance === 'shadow' && <> — the shadow ({dataMode}, virtual only) holds no real balance</>}.
        Locked presets are per mode and set on the Trades page.
      </p>

      <ScenarioSection config={config} patchConfig={patchConfig} />

      <GlobalCapitalRules config={config} state={state} patchConfig={patchConfig} />

      <PerSymbolAllocation
        config={config}
        state={state}
        availableSymbols={availableSymbols}
        patchConfig={patchConfig}
        bgfMode={scenario === 'best_gets_first'}
      />

      <LeverageControls config={config} patchConfig={patchConfig} scenario={scenario} />

      <DrawdownGuard config={config} state={state} patchConfig={patchConfig} />

      <LiveRiskState config={config} state={state} />

      {config.weight_rebalancer && (
        <WeightRebalancerSection
          config={config.weight_rebalancer}
          mode={dataMode}
          patchConfig={(patch) => setConfig(prev => prev ? { ...prev, ...patch } : prev)}
        />
      )}

      <PresetRankingSection
        config={config}
        availableSymbols={availableSymbols}
        patchConfig={patchConfig}
      />

      <MaxLossSection
        config={config}
        availableSymbols={availableSymbols}
        patchConfig={patchConfig}
      />
    </main>
  )
}
