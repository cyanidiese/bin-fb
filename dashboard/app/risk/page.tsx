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
import WeightSuggestions from '@/components/risk/WeightSuggestions'

const POLL_MS = 5000

/** Top-level fields of `next` that differ from `base` (deep, via JSON). locked_presets is
 *  never sent — it is edited per mode on the Trades page. */
function changedFields(base: RiskConfig | null, next: RiskConfig): Partial<RiskConfig> {
  const out: Record<string, unknown> = {}
  const b = (base ?? {}) as unknown as Record<string, unknown>
  for (const [k, v] of Object.entries(next as unknown as Record<string, unknown>)) {
    if (k === 'locked_presets') continue
    if (JSON.stringify(v) !== JSON.stringify(b[k])) out[k] = v
  }
  return out as Partial<RiskConfig>
}

export default function RiskPage() {
  const { availableSymbols } = useSymbolContext()
  const [config, setConfig] = useState<RiskConfig | null>(null)
  const [state, setState] = useState<RiskState | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saveOk, setSaveOk] = useState(false)

  // Instance switcher, same as the Trades page (and the same saved choice). Each trading
  // mode has its own config file, risk_config_{mode}.json (spec
  // 2026-09-26-per-mode-risk-config), so the switch selects BOTH the settings being edited
  // and saved, and the live state shown: the primary's risk_state.json, the mirror's
  // risk_state_{mode}.json (bot/instance_paths.py).
  const [instance, setInstance] = useState<Instance>('primary')
  const [botMode, setBotMode] = useState<'test' | 'live'>('test')
  const [modeReady, setModeReady] = useState(false)
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
      .finally(() => setModeReady(true))
  }, [])
  function chooseInstance(i: Instance) {
    if (i === instance) return
    setInstance(i)
    setState(null)    // never show one instance's numbers under the other's label
    setConfig(null)   // nor its settings: the other mode's file loads next
    setLoaded(null)
    setSaveError(null)
    try { localStorage.setItem('bfb-instance', i) } catch { /* private window */ }
  }
  const dataMode = instance === 'primary' ? botMode : oppositeMode(botMode)

  // The mode the config on screen was loaded for — Save All writes exactly that file,
  // even if the switch moved while a save was in flight.
  const [configMode, setConfigMode] = useState<'test' | 'live' | null>(null)
  const [configFile, setConfigFile] = useState<string | null>(null)
  // The config as loaded — Save All sends only what differs from it, so a save never
  // reverts a field someone changed elsewhere (Settings, the other mode) since the load.
  const [loaded, setLoaded] = useState<RiskConfig | null>(null)
  const [noChanges, setNoChanges] = useState(false)
  const [configReload, setConfigReload] = useState(0)
  useEffect(() => {
    if (!modeReady) return
    let alive = true
    fetch(`/api/risk?mode=${dataMode}`)
      .then(r => r.json())
      .then(d => {
        if (!alive || !d?.config) return
        setConfig(d.config)
        setLoaded(d.config)
        setConfigMode(d.mode)
        setConfigFile(d.file ?? null)
      })
      .catch(() => {})
    return () => { alive = false }
  }, [dataMode, modeReady, configReload])
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
    setNoChanges(false)
    try {
      if (!configMode) return
      const changed = changedFields(loaded, config)
      if (Object.keys(changed).length === 0) {
        setNoChanges(true)
        setTimeout(() => setNoChanges(false), 3000)
        return
      }
      const res = await fetch(`/api/risk?mode=${configMode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changed),
      })
      const data = await res.json()
      if (!res.ok) { setSaveError(data.error ?? `HTTP ${res.status}`); return }
      setLoaded(config)
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
        <label className="flex items-center gap-2" title="Selects which trading mode's settings you edit and save (risk_config_{mode}.json), and whose live state is shown.">
          <span className="text-[11px] uppercase tracking-wider text-gray-500">Instance</span>
          <InstanceToggle value={instance} onChange={chooseInstance} botMode={botMode} />
        </label>
        <div className="ml-auto flex items-center gap-3">
          {saveError && <span className="text-xs text-red-400 font-mono">{saveError}</span>}
          {saveOk && <span className="text-xs text-emerald-400 font-mono">Saved ✓</span>}
          {noChanges && <span className="text-xs text-gray-500 font-mono">No changes</span>}
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
        Editing the <span className="text-gray-300 font-semibold">{configMode ?? dataMode}</span> settings
        {configFile && <> — Save All writes <span className="font-mono text-gray-400">{configFile}</span></>}.
        Test and live keep separate settings; a setting live has never saved is read from test.
        {instance === 'shadow' && <> The shadow ({dataMode}, virtual only) holds no real balance.</>}
        {' '}Shared by both modes (<span className="font-mono text-gray-400">risk_config_shared.json</span>):
        preset ranking, signal filters, Telegram and backtest settings. Locked presets are set on the Trades page.
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

      {/* Applying writes the mode's weights server-side; reload so Save All's
          change detection starts from what is now on disk. */}
      <WeightSuggestions key={configMode ?? dataMode} mode={configMode ?? dataMode} onApplied={() => setConfigReload(n => n + 1)} />

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
