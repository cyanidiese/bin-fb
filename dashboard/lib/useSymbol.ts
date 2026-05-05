'use client'
import { useLocalStorage } from './useLocalStorage'

export function useSymbol(availableSymbols: string[]): [string, (s: string) => void] {
  const defaultSymbol = availableSymbols[0] ?? 'BTCUSDT'
  const [symbol, setSymbol] = useLocalStorage<string>('db:symbol', defaultSymbol)
  // If saved symbol is not in available list (symbols changed), reset to first
  const resolved = availableSymbols.includes(symbol) ? symbol : defaultSymbol
  return [resolved, setSymbol]
}
