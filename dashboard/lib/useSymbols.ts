'use client'
import { useEffect, useState } from 'react'

/**
 * Fetches /symbols.json (written by the bot at startup) and returns the list
 * of active symbols. Falls back to ['BTCUSDT'] if the file is missing.
 */
export function useSymbols(): string[] {
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT'])

  useEffect(() => {
    fetch('/symbols.json')
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.symbols) && d.symbols.length > 0) {
          setSymbols(d.symbols as string[])
        }
      })
      .catch(() => { /* keep default */ })
  }, [])

  return symbols
}
