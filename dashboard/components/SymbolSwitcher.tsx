'use client'

interface Props {
  symbols: string[]
  selected: string
  onSelect: (s: string) => void
  showAll?: boolean   // adds an "All" option for aggregate view
}

function Btn({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1 rounded text-xs font-semibold transition-colors shrink-0 ${
        active
          ? 'bg-indigo-600 text-white'
          : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
      }`}
    >
      {label}
    </button>
  )
}

export default function SymbolSwitcher({ symbols, selected, onSelect, showAll = false }: Props) {
  const options = showAll ? [...symbols, 'ALL'] : symbols
  const rest = options.filter(s => s !== selected)

  return (
    <div className="flex items-center gap-1 min-w-0">
      {/* Selected symbol — always visible on the left */}
      <Btn label={selected} active onClick={() => onSelect(selected)} />

      {/* Remaining symbols — scroll horizontally */}
      {rest.length > 0 && (
        <>
          <div className="w-px h-4 bg-gray-700 shrink-0" />
          <div className="flex gap-1 overflow-x-auto scrollbar-none min-w-0" style={{ scrollbarWidth: 'none' }}>
            {rest.map(s => (
              <Btn key={s} label={s} active={false} onClick={() => onSelect(s)} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
