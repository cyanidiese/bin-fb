'use client'

import type { FilterEntry, FiltersMap, FilterState } from '@/lib/presetFilters'

interface Props {
  title: string
  specs: FilterEntry[]
  filters: FiltersMap
  open: boolean
  onToggle: () => void
  onChange: (key: string, patch: Partial<FilterState>) => void
  onClear: () => void
}

export default function PresetFilters({ title, specs, filters, open, onToggle, onChange, onClear }: Props) {
  const activeCount = open
    ? specs.filter(({ key }) => filters[key]?.enabled).length
    : 0

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-3 space-y-3">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <p className="text-xs text-gray-500 uppercase tracking-wide font-semibold">
            {title}
            {activeCount > 0 && (
              <span className="text-amber-400 ml-1">({activeCount} active)</span>
            )}
          </p>
          {open && activeCount > 0 && (
            <button
              onClick={onClear}
              className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
            >
              Clear all
            </button>
          )}
        </div>
        {/* Show / hide toggle */}
        <button
          onClick={onToggle}
          className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors select-none"
        >
          {open ? '▲ Hide' : '▼ Show'}
        </button>
      </div>

      {/* Filter controls — only rendered (and applied) when open */}
      {open && (
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {specs.map(({ key, label, spec }) => {
            const f = filters[key]
            if (!f) return null
            return (
              <div key={key} className="flex items-start gap-2 min-w-0">
                {/* Checkbox */}
                <input
                  type="checkbox"
                  id={`filter-chk-${key}`}
                  checked={f.enabled}
                  onChange={e => onChange(key, { enabled: e.target.checked })}
                  className="mt-0.5 shrink-0 accent-amber-400 cursor-pointer"
                />
                <label
                  htmlFor={`filter-chk-${key}`}
                  className={`text-xs cursor-pointer select-none whitespace-nowrap ${f.enabled ? 'text-gray-200' : 'text-gray-500'}`}
                >
                  {label}
                </label>

                {/* Control — visible only when enabled */}
                {f.enabled && (
                  <div className="flex items-center gap-1.5 ml-1">
                    {spec.kind === 'text' && (
                      <input
                        type="text"
                        value={f.text}
                        onChange={e => onChange(key, { text: e.target.value })}
                        placeholder="contains…"
                        className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 focus:outline-none focus:border-gray-600 w-32"
                      />
                    )}

                    {spec.kind === 'bool' && (
                      <div className="flex rounded overflow-hidden border border-gray-700 text-[10px] font-mono">
                        {(['any', 'yes', 'no'] as const).map(opt => {
                          const val = opt === 'any' ? null : opt === 'yes'
                          const active = f.boolVal === val
                          return (
                            <button
                              key={opt}
                              onClick={() => onChange(key, { boolVal: val })}
                              className={`px-2 py-0.5 transition-colors ${active ? 'bg-amber-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200'}`}
                            >
                              {opt.charAt(0).toUpperCase() + opt.slice(1)}
                            </button>
                          )
                        })}
                      </div>
                    )}

                    {spec.kind === 'number' && (
                      <>
                        <input
                          type="number"
                          value={f.numMin}
                          min={spec.absMin}
                          max={spec.absMax}
                          step={spec.step}
                          onChange={e => onChange(key, { numMin: e.target.value })}
                          className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 focus:outline-none focus:border-gray-600 w-20 font-mono"
                        />
                        <span className="text-gray-600 text-[10px]">–</span>
                        <input
                          type="number"
                          value={f.numMax}
                          min={spec.absMin}
                          max={spec.absMax}
                          step={spec.step}
                          onChange={e => onChange(key, { numMax: e.target.value })}
                          className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-0.5 text-gray-300 focus:outline-none focus:border-gray-600 w-20 font-mono"
                        />
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
