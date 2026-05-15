'use client'
import { useEffect, useRef, useState } from 'react'

const POLL_MS = 15_000

export function useSymbols(): string[] {
  const [symbols, setSymbols] = useState<string[]>(['BTCUSDT'])
  const lastJoined = useRef('')

  useEffect(() => {
    function load() {
      fetch('/api/public-file?f=symbols.json')
        .then(r => r.json())
        .then(d => {
          if (!Array.isArray(d.symbols) || d.symbols.length === 0) return
          const joined = (d.symbols as string[]).join(',')
          if (joined !== lastJoined.current) {
            lastJoined.current = joined
            setSymbols(d.symbols as string[])
          }
        })
        .catch(() => {})
    }

    load()
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [])

  return symbols
}
