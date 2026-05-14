'use client'
import { useEffect, useState } from 'react'

const POLL_MS = 3000

/**
 * Fetches /symbols.json and polls every 3s so the switcher updates immediately
 * when a symbol is added or removed via the Settings page.
 */
export function useSymbols(): string[] {
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT'])

  useEffect(() => {
    function load() {
      fetch(`/symbols.json?t=${Date.now()}`)
        .then(r => r.json())
        .then(d => {
          if (Array.isArray(d.symbols) && d.symbols.length > 0) {
            setSymbols(d.symbols as string[])
          }
        })
        .catch(() => { /* keep last */ })
    }

    load()
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [])

  return symbols
}
