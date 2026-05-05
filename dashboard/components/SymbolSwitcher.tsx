'use client'

interface Props {
  symbols: string[]
  selected: string
  onSelect: (s: string) => void
  showAll?: boolean   // adds an "All" option for aggregate view
}

export default function SymbolSwitcher({ symbols, selected, onSelect, showAll = false }: Props) {
  const options = showAll ? [...symbols, 'ALL'] : symbols
  return (
    <div className="flex gap-1">
      {options.map(s => (
        <button
          key={s}
          onClick={() => onSelect(s)}
          className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
            selected === s
              ? 'bg-indigo-600 text-white'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
          }`}
        >
          {s}
        </button>
      ))}
    </div>
  )
}
