import type { BacktestPreset } from './types'

// Effective value of a setting for a preset: override if present, else default.
export const SETTING_DEFAULTS: Record<string, number | boolean> = {
  swing_neighbours: 2,
  min_swing_points: 3,
  proximity_zone_pct: 10.0,
  min_profit_pct: 0.5,
  min_profit_loss_ratio: 1.5,
  tp_multiplier: 1.0,
  max_profit_pct: 0.0,
  min_sl_pct: 0.0,
  max_sl_pct: 0.0,
  sl_adjust_to_rr: false,
  partial_take_pct: 0.0,
  trailing_stop_pct: 0.0,
  loss_streak_max: 0,
  loss_streak_cooldown_candles: 5,
  global_pause_trigger_candles: 0,
  global_pause_candles: 10,
}

export type FilterSpec =
  | { kind: 'text' }
  | { kind: 'bool' }
  | { kind: 'number'; step: number; absMin: number; absMax: number }

export type FilterEntry = { key: string; label: string; spec: FilterSpec }

// ── Settings filters (filter by strategy parameter values) ─────────────────

export const FILTER_SPECS: FilterEntry[] = [
  { key: '__name__',                    label: 'Preset name',                    spec: { kind: 'text' } },
  { key: 'swing_neighbours',            label: 'Swing neighbours',               spec: { kind: 'number', step: 1,   absMin: 1,   absMax: 5    } },
  { key: 'min_swing_points',            label: 'Min swing points',               spec: { kind: 'number', step: 1,   absMin: 1,   absMax: 10   } },
  { key: 'proximity_zone_pct',          label: 'Proximity zone (%)',             spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 50   } },
  { key: 'min_profit_pct',              label: 'Min TP distance (%)',            spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 5    } },
  { key: 'min_profit_loss_ratio',       label: 'Min R:R ratio',                  spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 10   } },
  { key: 'tp_multiplier',               label: 'TP multiplier',                  spec: { kind: 'number', step: 0.01, absMin: 0.5, absMax: 2.0  } },
  { key: 'max_profit_pct',              label: 'Max TP distance (%)',            spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 10   } },
  { key: 'min_sl_pct',                  label: 'Min SL distance (%)',            spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 5    } },
  { key: 'max_sl_pct',                  label: 'Max SL distance (%)',            spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 5    } },
  { key: 'sl_adjust_to_rr',             label: 'Adjust SL to R:R',              spec: { kind: 'bool' } },
  { key: 'partial_take_pct',            label: 'Arm threshold',                  spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 1.0  } },
  { key: 'trailing_stop_pct',           label: 'Trail retrace',                  spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 1.0  } },
  { key: 'loss_streak_max',             label: 'Loss streak limit',              spec: { kind: 'number', step: 1,   absMin: 0,   absMax: 10   } },
  { key: 'loss_streak_cooldown_candles',label: 'Streak cooldown (candles)',      spec: { kind: 'number', step: 1,   absMin: 0,   absMax: 20   } },
  { key: 'global_pause_trigger_candles',label: 'Global pause trigger (candles)', spec: { kind: 'number', step: 1,   absMin: 0,   absMax: 20   } },
  { key: 'global_pause_candles',        label: 'Global pause duration (candles)',spec: { kind: 'number', step: 1,   absMin: 0,   absMax: 30   } },
]

// ── Table filters (filter by result column values) ──────────────────────────

export const TABLE_FILTER_SPECS: FilterEntry[] = [
  { key: 'preset',                label: 'Preset name',     spec: { kind: 'text' } },
  { key: 'total_trades',          label: 'Trades',          spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 200  } },
  { key: 'wins',                  label: 'Wins',            spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 100  } },
  { key: 'partials',              label: 'Partials',        spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 100  } },
  { key: 'trails',                label: 'Trails',          spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 100  } },
  { key: 'losses',                label: 'Losses',          spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 100  } },
  { key: 'win_rate',              label: 'Win%',            spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 1    } },
  { key: 'total_profit_pct',      label: 'Profit%',         spec: { kind: 'number', step: 0.01, absMin: -50, absMax: 50   } },
  { key: 'avg_rr',                label: 'Avg RR',          spec: { kind: 'number', step: 0.01, absMin: 0,   absMax: 10   } },
  { key: 'max_consecutive_losses',label: 'Max DD',          spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 20   } },
  { key: 'avg_max_tp_reach_pct',  label: 'Avg TP reach%',   spec: { kind: 'number', step: 1,    absMin: 0,   absMax: 100  } },
]

// ── Shared state types ──────────────────────────────────────────────────────

export type FilterState = {
  enabled: boolean
  text: string
  boolVal: boolean | null   // null = Any
  numMin: string
  numMax: string
}

export type FiltersMap = Record<string, FilterState>

function initFromSpecs(specs: FilterEntry[]): FiltersMap {
  return Object.fromEntries(
    specs.map(({ key, spec }) => [
      key,
      {
        enabled: false,
        text: '',
        boolVal: null,
        numMin: spec.kind === 'number' ? String(spec.absMin) : '',
        numMax: spec.kind === 'number' ? String(spec.absMax) : '',
      } satisfies FilterState,
    ])
  )
}

export function initFilters(): FiltersMap {
  return initFromSpecs(FILTER_SPECS)
}

export function initTableFilters(): FiltersMap {
  return initFromSpecs(TABLE_FILTER_SPECS)
}

// ── Filter logic ────────────────────────────────────────────────────────────

export function effectiveValue(preset: BacktestPreset, key: string): number | boolean {
  if (key in preset.settings) return preset.settings[key]
  return SETTING_DEFAULTS[key] ?? 0
}

export function applyFilters(presets: BacktestPreset[], filters: FiltersMap): BacktestPreset[] {
  return presets.filter(preset => {
    for (const { key, spec } of FILTER_SPECS) {
      const f = filters[key]
      if (!f?.enabled) continue

      if (spec.kind === 'text') {
        if (!preset.preset.toLowerCase().includes(f.text.toLowerCase())) return false
      } else if (spec.kind === 'bool') {
        if (f.boolVal !== null && effectiveValue(preset, key) !== f.boolVal) return false
      } else if (spec.kind === 'number') {
        const val = effectiveValue(preset, key) as number
        const min = parseFloat(f.numMin)
        const max = parseFloat(f.numMax)
        if (!isNaN(min) && val < min - 1e-9) return false
        if (!isNaN(max) && val > max + 1e-9) return false
      }
    }
    return true
  })
}

export function applyTableFilters(presets: BacktestPreset[], filters: FiltersMap): BacktestPreset[] {
  return presets.filter(preset => {
    for (const { key, spec } of TABLE_FILTER_SPECS) {
      const f = filters[key]
      if (!f?.enabled) continue

      if (spec.kind === 'text') {
        if (!preset.preset.toLowerCase().includes(f.text.toLowerCase())) return false
      } else if (spec.kind === 'number') {
        const val = (preset as unknown as Record<string, number>)[key]
        const min = parseFloat(f.numMin)
        const max = parseFloat(f.numMax)
        if (!isNaN(min) && val < min - 1e-9) return false
        if (!isNaN(max) && val > max + 1e-9) return false
      }
    }
    return true
  })
}
