'use client'
import { createContext, useContext } from 'react'

interface SymbolContextValue {
  symbol: string
  setSymbol: (s: string) => void
  availableSymbols: string[]
  disabledSymbols: Set<string>
  symbolsWithOrders: Set<string>
}

export const SymbolContext = createContext<SymbolContextValue>({
  symbol: 'BTCUSDT',
  setSymbol: () => {},
  availableSymbols: ['BTCUSDT'],
  disabledSymbols: new Set(),
  symbolsWithOrders: new Set(),
})

export function useSymbolContext() {
  return useContext(SymbolContext)
}
