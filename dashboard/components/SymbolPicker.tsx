'use client'

interface Props {
  symbols: string[]
  selected: string
  onSelect: (s: string) => void
  /** Disabled in symbol_registry.json — red dot, bottom-right. */
  disabled?: Set<string>
  /** Has orders on the primary but none on the instance being viewed — dimmed. */
  dimmed?: Set<string>
  /** A REAL position is open right now in the viewed instance — bright gold. */
  openReal?: Set<string>
  /** A VIRTUAL position is open right now in the viewed instance — faint gold. */
  openVirtual?: Set<string>
  /** Names the instance being viewed, for the tooltips. */
  instanceLabel?: string
}

/**
 * Symbol selector for the Trades page.
 *
 * Markers match the header switcher's placement so the two read the same way: state
 * that belongs to the symbol itself (disabled) sits bottom-right, activity sits
 * top-right. Every marker prop is optional, so callers that only need plain buttons
 * pass none.
 */
export default function SymbolPicker({
  symbols, selected, onSelect,
  disabled, dimmed, openReal, openVirtual, instanceLabel,
}: Props) {
  if (symbols.length === 0) return null
  const where = instanceLabel ? ` on ${instanceLabel}` : ''

  const anyReal = symbols.some(s => openReal?.has(s))
  const anyVirtual = symbols.some(s => openVirtual?.has(s))
  const anyDisabled = symbols.some(s => disabled?.has(s))
  const anyDimmed = symbols.some(s => dimmed?.has(s))

  return (
    <div className="space-y-1.5">
    <div className="flex flex-wrap gap-1.5">
      {symbols.map(sym => {
        const isDisabled = disabled?.has(sym) ?? false
        const isDimmed = dimmed?.has(sym) ?? false
        const hasReal = openReal?.has(sym) ?? false
        const hasVirtual = openVirtual?.has(sym) ?? false
        // Real wins when both are open: it is the position with money behind it.
        const openKind = hasReal ? 'real' : hasVirtual ? 'virtual' : null

        const title = [
          sym,
          isDisabled ? 'disabled in the registry' : null,
          openKind === 'real' ? `real position open${where}` : null,
          openKind === 'virtual' ? `virtual position open${where}` : null,
          isDimmed ? `no orders${where} (has them on the primary)` : null,
        ].filter(Boolean).join(' — ')

        return (
          <button
            key={sym}
            onClick={() => onSelect(sym)}
            title={title}
            className={`relative px-3 py-1 rounded text-xs font-semibold transition-colors ${
              sym === selected
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
            } ${isDimmed && sym !== selected ? 'opacity-60' : ''}`}
          >
            {sym}

            {/* Top-right: a position is open right now in the viewed instance.
                A REAL order is the one with money behind it, so it gets a large
                saturated yellow dot with a dark ring for contrast against both the
                grey and the indigo button. The first version used amber-300 vs
                amber-400/60 at the same 1.5px size, which was not distinguishable —
                the data was correct but the marker was invisible in practice. */}
            {openKind && (
              <span
                className={
                  openKind === 'real'
                    ? 'absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-yellow-400 '
                      + 'ring-2 ring-gray-950 shadow-[0_0_5px_rgba(250,204,21,0.9)]'
                    : 'absolute -top-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-amber-400/50'
                }
              />
            )}

            {/* Red dot, bottom-right: disabled. Coexists with the gold dot, because a
                disabled symbol can still be holding a position it opened earlier. */}
            {isDisabled && (
              <span className="absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-red-500" />
            )}
          </button>
        )
      })}
    </div>

    {/* Only legend the markers actually on screen, so it stays quiet when nothing
        is open. */}
    {(anyReal || anyVirtual || anyDisabled || anyDimmed) && (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-gray-500">
        {anyReal && (
          <span className="inline-flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-yellow-400 ring-1 ring-gray-950" />
            real order open
          </span>
        )}
        {anyVirtual && (
          <span className="inline-flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-400/50" />
            virtual only
          </span>
        )}
        {anyDisabled && (
          <span className="inline-flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
            disabled
          </span>
        )}
        {anyDimmed && (
          <span className="inline-flex items-center gap-1 opacity-60">
            dimmed — no orders on {instanceLabel || 'this instance'}
          </span>
        )}
      </div>
    )}
    </div>
  )
}
