'use client'

interface Props {
  symbols: string[]
  selected: string
  onSelect: (s: string) => void
}

export default function SymbolPicker({ symbols, selected, onSelect }: Props) {
  if (symbols.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {symbols.map(sym => (
        <button
          key={sym}
          onClick={() => onSelect(sym)}
          className={`px-3 py-1 rounded text-xs font-semibold transition-colors ${
            sym === selected
              ? 'bg-indigo-600 text-white'
              : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
          }`}
        >
          {sym}
        </button>
      ))}
    </div>
  )
}
