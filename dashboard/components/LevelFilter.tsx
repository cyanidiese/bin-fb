'use client'

// Props for the segmented level filter control.
// `levels` comes from trend_levels in the snapshot (e.g. [1, 2, 3]).
// Selecting a level means "show this level and everything below it".
interface Props {
  levels: number[]
  selected: number
  onChange: (level: number) => void
}

// Segmented button control that lets the user choose which trend levels to display.
// The selected level acts as a ceiling: L2 selected → show L1 + L2 data.
export default function LevelFilter({ levels, selected, onChange }: Props) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 uppercase tracking-wider mr-1">View</span>
      <div className="flex rounded-lg border border-gray-700 overflow-hidden">
        {levels.map(lvl => (
          <button
            key={lvl}
            onClick={() => onChange(lvl)}
            className={[
              'px-4 py-1.5 text-sm font-semibold transition-colors select-none',
              selected === lvl
                ? 'bg-indigo-600 text-white'           // active level: highlighted
                : 'bg-gray-900 text-gray-400 hover:text-white hover:bg-gray-800',
            ].join(' ')}
          >
            L{lvl}
          </button>
        ))}
      </div>
    </div>
  )
}
