export type SettingMeta = {
  label: string
  default: number | boolean
  description: string
  unit?: string
  category: 'Structure' | 'Entry filter' | 'Exit strategy' | 'Cooldown'
}

export const SETTINGS_META: Record<string, SettingMeta> = {
  // ── Structure ──────────────────────────────────────────────────────────
  swing_neighbours: {
    label: 'Swing neighbours',
    default: 2,
    description: 'Candles on each side required to confirm a swing high/low. Higher = only major swings detected, fewer but more reliable signals.',
    category: 'Structure',
  },
  min_swing_points: {
    label: 'Min swing points',
    default: 3,
    description: 'Minimum confirmed swing points before the trend is considered established enough to trade.',
    category: 'Structure',
  },

  // ── Entry filter ───────────────────────────────────────────────────────
  proximity_zone_pct: {
    label: 'Proximity zone',
    default: 10.0,
    unit: '%',
    description: 'Price must be within this % of a swing level to trigger a signal. Wider = more signals, looser entries.',
    category: 'Entry filter',
  },
  min_profit_pct: {
    label: 'Min TP distance',
    default: 0.5,
    unit: '%',
    description: 'Skip signals where the TP target is less than this % away from entry. Filters out low-reward setups.',
    category: 'Entry filter',
  },
  min_profit_loss_ratio: {
    label: 'Min R:R ratio',
    default: 1.5,
    description: 'Minimum ratio of TP distance to SL distance. Trades below this are skipped or SL-adjusted (if sl_adjust_to_rr is on).',
    category: 'Entry filter',
  },
  tp_multiplier: {
    label: 'TP multiplier',
    default: 1.0,
    description: 'Scale the raw TP distance by this factor before placing the order. Values below 1.0 target an easier-to-hit level at the cost of smaller wins (e.g. 0.95 = aim for 95% of full TP).',
    category: 'Entry filter',
  },
  max_profit_pct: {
    label: 'Max TP distance',
    default: 0.0,
    unit: '%',
    description: 'Skip signals with a TP farther than this % from entry. Filters out overly ambitious targets that rarely get hit. 0 = disabled.',
    category: 'Entry filter',
  },
  min_sl_pct: {
    label: 'Min SL distance',
    default: 0.0,
    unit: '%',
    description: 'Skip signals where the SL is closer than this % to entry. Avoids noise-prone trades with very tight stops. 0 = disabled.',
    category: 'Entry filter',
  },
  max_sl_pct: {
    label: 'Max SL distance',
    default: 0.0,
    unit: '%',
    description: 'Skip signals where the SL is farther than this % from entry. Caps maximum per-trade risk exposure. 0 = disabled.',
    category: 'Entry filter',
  },
  sl_adjust_to_rr: {
    label: 'Adjust SL to R:R',
    default: false,
    description: 'When a trade fails the R:R filter, tighten the SL to just meet the minimum ratio instead of skipping the trade entirely. Trades more often but with tighter stops.',
    category: 'Entry filter',
  },

  // ── Exit strategy ──────────────────────────────────────────────────────
  partial_take_pct: {
    label: 'Arm threshold',
    default: 0.0,
    unit: '× TP dist',
    description: 'Arm the partial / trailing-stop mechanism when price reaches this fraction of the full TP distance from entry. For example, 0.30 = arm at 30% of TP distance. 0 = no partial exit, use fixed TP.',
    category: 'Exit strategy',
  },
  trailing_stop_pct: {
    label: 'Trail retrace',
    default: 0.0,
    unit: '× gain',
    description: 'Once armed, close the trade when price pulls back by this fraction of the maximum gain seen from entry. For example, 0.15 = trail closes if price retraces 15% of peak gain. 0 = use fixed partial retrace to the arm threshold instead.',
    category: 'Exit strategy',
  },

  // ── Cooldown ───────────────────────────────────────────────────────────
  loss_streak_max: {
    label: 'Loss streak limit',
    default: 0,
    unit: 'losses',
    description: 'Block new entries on a side (BUY or SELL) after this many consecutive losses. Prevents doubling down into a losing streak. 0 = disabled.',
    category: 'Cooldown',
  },
  loss_streak_cooldown_candles: {
    label: 'Streak cooldown',
    default: 5,
    unit: 'candles',
    description: 'Candles to block re-entry on a side after hitting the consecutive loss limit. At 15m timeframe: 5 candles ≈ 75 min, 10 candles ≈ 2.5 h.',
    category: 'Cooldown',
  },
  global_pause_trigger_candles: {
    label: 'Global pause trigger',
    default: 0,
    unit: 'candles',
    description: 'If BUY and SELL each lose within this candle window of each other, the market is likely ranging — pause all entries on both sides. 0 = disabled.',
    category: 'Cooldown',
  },
  global_pause_candles: {
    label: 'Global pause duration',
    default: 10,
    unit: 'candles',
    description: 'Candles to pause all new entries (both sides) after a global pause is triggered by simultaneous BUY + SELL losses.',
    category: 'Cooldown',
  },
}

export const CATEGORIES: SettingMeta['category'][] = [
  'Structure',
  'Entry filter',
  'Exit strategy',
  'Cooldown',
]

interface PresetOption {
  name: string
  total_profit_pct: number
}

interface Props {
  settings: Record<string, number | boolean>
  presets?: PresetOption[]
  selectedPreset?: string
  onSelect?: (name: string) => void
}

export default function PresetSettingsPanel({ settings, presets, selectedPreset, onSelect }: Props) {
  const sortedPresets = presets
    ? [...presets].sort((a, b) => b.total_profit_pct - a.total_profit_pct)
    : null

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-gray-500 uppercase tracking-wide font-semibold shrink-0">All settings</p>
        {sortedPresets && onSelect && (
          <select
            value={selectedPreset ?? ''}
            onChange={e => onSelect(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1 text-gray-300 focus:outline-none focus:border-gray-600 cursor-pointer min-w-0 max-w-xs truncate"
          >
            {sortedPresets.map(p => (
              <option key={p.name} value={p.name}>
                {p.name} ({p.total_profit_pct >= 0 ? '+' : ''}{p.total_profit_pct.toFixed(2)}%)
              </option>
            ))}
          </select>
        )}
      </div>
      {CATEGORIES.map(cat => {
        const rows = Object.entries(SETTINGS_META).filter(([, m]) => m.category === cat)
        return (
          <div key={cat}>
            <p className="text-[10px] font-bold uppercase tracking-widest text-gray-600 mb-1.5 pb-1 border-b border-gray-800">
              {cat}
            </p>
            <div className="divide-y divide-gray-800/40">
              {rows.map(([key, meta]) => {
                const isOverridden = key in settings
                const value = isOverridden ? settings[key] : meta.default
                const displayValue =
                  typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)
                return (
                  <div
                    key={key}
                    className="py-2 grid gap-x-3 items-start"
                    style={{ gridTemplateColumns: '160px 80px 1fr' }}
                  >
                    <span
                      className={`text-xs leading-tight ${isOverridden ? 'text-white font-medium' : 'text-gray-500'}`}
                    >
                      {meta.label}
                    </span>
                    <div className="text-right">
                      <span
                        className={`text-xs font-mono font-bold ${isOverridden ? 'text-amber-400' : 'text-gray-600'}`}
                      >
                        {displayValue}
                      </span>
                      {meta.unit && (
                        <span className="text-[10px] text-gray-600 ml-1">{meta.unit}</span>
                      )}
                      {isOverridden && (
                        <div className="text-[9px] text-gray-700 mt-0.5">
                          default:{' '}
                          {typeof meta.default === 'boolean'
                            ? meta.default ? 'yes' : 'no'
                            : String(meta.default)}
                        </div>
                      )}
                    </div>
                    <span className="text-[10px] text-gray-500 leading-relaxed">
                      {meta.description}
                    </span>
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
