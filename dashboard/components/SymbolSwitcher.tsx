'use client'

import { useSymbolContext } from '@/lib/SymbolContext'

interface Props {
  symbols: string[]
  selected: string
  onSelect: (s: string) => void
  showAll?: boolean   // adds an "All" option for aggregate view
}

function Btn({
  label,
  active,
  onClick,
  disabled: isDisabled,
  hasOrders,
}: {
  label: string
  active: boolean
  onClick: () => void
  disabled?: boolean
  hasOrders?: boolean
}) {
  return (
    <button
      onClick={onClick}
      className={`relative px-3 py-1 rounded text-xs font-semibold transition-colors shrink-0 ${
        active
          ? 'bg-indigo-600 text-white'
          : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
      }`}
      title={isDisabled ? `${label} — disabled (precision error)` : label}
    >
      {label}
      {/* Green dot: has live orders */}
      {hasOrders && (
        <span
          className="absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-emerald-400"
          title={`${label} has live orders`}
        />
      )}
      {/* Red dot: disabled — can co-exist with green */}
      {isDisabled && (
        <span
          className="absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-red-500"
          title={`${label} is disabled`}
        />
      )}
    </button>
  )
}

export default function SymbolSwitcher({ symbols, selected, onSelect, showAll = false }: Props) {
  const { disabledSymbols, symbolsWithOrders } = useSymbolContext()
  const options = showAll ? [...symbols, 'ALL'] : symbols
  const rest = options.filter(s => s !== selected)

  return (
    <div className="flex items-center gap-1 min-w-0">
      {/* Selected symbol — always visible on the left */}
      <Btn
        label={selected}
        active
        onClick={() => onSelect(selected)}
        disabled={disabledSymbols.has(selected)}
        hasOrders={symbolsWithOrders.has(selected)}
      />

      {/* Remaining symbols — scroll horizontally */}
      {rest.length > 0 && (
        <>
          <div className="w-px h-4 bg-gray-700 shrink-0" />
          <div className="flex gap-1 overflow-x-auto scrollbar-none min-w-0" style={{ scrollbarWidth: 'none' }}>
            {rest.map(s => (
              <Btn
                key={s}
                label={s}
                active={false}
                onClick={() => onSelect(s)}
                disabled={disabledSymbols.has(s)}
                hasOrders={symbolsWithOrders.has(s)}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
