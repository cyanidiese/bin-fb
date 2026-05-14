'use client'

import { useState, useEffect, useCallback } from 'react'
import { useSymbolContext } from '@/lib/SymbolContext'
import { RiskConfig, RiskState } from '@/lib/risk-types'
import { SAVE_BTN_CLS } from '@/lib/risk-styles'
import ScenarioSection from '@/components/risk/ScenarioSection'
import GlobalCapitalRules from '@/components/risk/GlobalCapitalRules'
import PerSymbolAllocation from '@/components/risk/PerSymbolAllocation'
import LeverageControls from '@/components/risk/LeverageControls'
import DrawdownGuard from '@/components/risk/DrawdownGuard'
import LiveRiskState from '@/components/risk/LiveRiskState'

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

  const pollState = useCallback(() => {
    fetch(`/api/public-file?f=risk_state.json`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data) setState(data) })
      .catch(() => {})
  }, [])

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

      <ScenarioSection config={config} patchConfig={patchConfig} />

      <GlobalCapitalRules config={config} state={state} patchConfig={patchConfig} />

      {scenario !== 'best_gets_first' && (
        <PerSymbolAllocation
          config={config}
          state={state}
          availableSymbols={availableSymbols}
          patchConfig={patchConfig}
        />
      )}

      <LeverageControls config={config} patchConfig={patchConfig} scenario={scenario} />

      <DrawdownGuard config={config} state={state} patchConfig={patchConfig} />

      <LiveRiskState config={config} state={state} />
    </main>
  )
}
