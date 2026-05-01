'use client'

import { useState } from 'react'
import { SETTINGS_META, CATEGORIES } from './PresetSettingsPanel'
import { FILTER_SPECS } from '@/lib/presetFilters'

// bounds per key from FILTER_SPECS
const BOUNDS: Record<string, { step: number; min: number; max: number }> = {}
for (const { key, spec } of FILTER_SPECS) {
  if (spec.kind === 'number') {
    BOUNDS[key] = { step: spec.step, min: spec.absMin, max: spec.absMax }
  }
}

interface Props {
  // Base settings from the selected preset (overrides only — not full defaults)
  baseSettings: Record<string, number | boolean>
  onChange: (values: Record<string, number | boolean>) => void
}

export default function EditableSettingsPanel({ baseSettings, onChange }: Props) {
  // Start with merged base: SETTING_DEFAULTS + preset overrides
  const [values, setValues] = useState<Record<string, number | boolean>>(() => {
    const out: Record<string, number | boolean> = {}
    for (const [key, meta] of Object.entries(SETTINGS_META)) {
      out[key] = key in baseSettings ? baseSettings[key] : meta.default
    }
    return out
  })

  // Tooltip state
  const [tooltip, setTooltip] = useState<string | null>(null)
  const [tooltipKey, setTooltipKey] = useState<string | null>(null)

  function set(key: string, value: number | boolean) {
    const next = { ...values, [key]: value }
    setValues(next)
    // Only emit non-default values (overrides)
    const overrides: Record<string, number | boolean> = {}
    for (const [k, v] of Object.entries(next)) {
      if (v !== SETTINGS_META[k]?.default) overrides[k] = v
    }
    onChange(overrides)
  }

  function reset(key: string) {
    set(key, SETTINGS_META[key].default)
  }

  return (
    <div className="space-y-4">
      {CATEGORIES.map(cat => {
        const rows = Object.entries(SETTINGS_META).filter(([, m]) => m.category === cat)
        return (
          <div key={cat}>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-600 mb-1.5 pb-1 border-b border-gray-800">
              {cat}
            </p>
            <div className="grid grid-cols-2 gap-x-5">
              {rows.map(([key, meta]) => {
                const val = values[key]
                const isOverridden = val !== meta.default
                const bounds = BOUNDS[key]
                const isShowingTip = tooltipKey === key

                return (
                  <div
                    key={key}
                    className="py-1.5 grid gap-x-2 items-center border-b border-gray-800/30"
                    style={{ gridTemplateColumns: '1fr 96px 36px' }}
                  >
                    {/* Label + description popover */}
                    <div className="relative">
                      <button
                        onMouseEnter={() => { setTooltipKey(key); setTooltip(meta.description) }}
                        onMouseLeave={() => { setTooltipKey(null); setTooltip(null) }}
                        className={`text-xs text-left leading-tight hover:underline decoration-dotted cursor-help ${isOverridden ? 'text-white font-medium' : 'text-gray-400'}`}
                      >
                        {meta.label}
                        {meta.unit && <span className="text-gray-600 ml-1 text-[10px]">{meta.unit}</span>}
                      </button>
                      {isShowingTip && tooltip && (
                        <div className="absolute bottom-full left-0 mb-1.5 z-50 w-64 rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-[11px] text-gray-300 leading-relaxed shadow-xl pointer-events-none">
                          {tooltip}
                        </div>
                      )}
                    </div>

                    {/* Edit control */}
                    <div className="flex items-center gap-1.5 justify-end">
                      {typeof val === 'boolean' ? (
                        <div className="flex rounded overflow-hidden border border-gray-700 text-[10px] font-mono">
                          {[true, false].map(opt => (
                            <button
                              key={String(opt)}
                              onClick={() => set(key, opt)}
                              className={`px-2 py-0.5 transition-colors ${val === opt ? 'bg-amber-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}
                            >
                              {opt ? 'Yes' : 'No'}
                            </button>
                          ))}
                        </div>
                      ) : (
                        <input
                          type="number"
                          value={val as number}
                          min={bounds?.min}
                          max={bounds?.max}
                          step={bounds?.step ?? 1}
                          onChange={e => {
                            const n = parseFloat(e.target.value)
                            if (!isNaN(n)) set(key, n)
                          }}
                          className={`w-full text-xs font-mono text-right bg-gray-800 border rounded px-2 py-0.5 focus:outline-none transition-colors ${isOverridden ? 'border-amber-600/60 text-amber-400 focus:border-amber-500' : 'border-gray-700 text-gray-300 focus:border-gray-600'}`}
                        />
                      )}
                    </div>

                    {/* Reset button */}
                    <div className="w-12 flex justify-end">
                      {isOverridden ? (
                        <button
                          onClick={() => reset(key)}
                          title={`Reset to default: ${typeof meta.default === 'boolean' ? (meta.default ? 'yes' : 'no') : meta.default}`}
                          className="text-[9px] text-gray-600 hover:text-gray-300 transition-colors"
                        >
                          ↺ {typeof meta.default === 'boolean' ? (meta.default ? 'yes' : 'no') : meta.default}
                        </button>
                      ) : (
                        <span className="text-[9px] text-gray-700 font-mono">
                          {typeof meta.default === 'boolean' ? (meta.default ? 'yes' : 'no') : meta.default}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
