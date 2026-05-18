'use client'
import { usePathname } from 'next/navigation'
import { useEffect, useMemo, useState } from 'react'
import { SymbolContext } from '@/lib/SymbolContext'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
import NavBar from './NavBar'
import AlertBanner from './AlertBanner'

const SCORE_POLL_MS = 30_000

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)
  const pathname = usePathname()
  const isLoginPage = pathname === '/login'

  const [perfScores, setPerfScores] = useState<Record<string, number>>({})

  useEffect(() => {
    function loadScores() {
      fetch('/api/public-file?f=risk_state.json')
        .then(r => (r.ok ? r.json() : null))
        .then(data => {
          if (!data?.per_symbol) return
          const scores: Record<string, number> = {}
          for (const [sym, info] of Object.entries(data.per_symbol as Record<string, { performance_score?: number }>)) {
            scores[sym] = (info as { performance_score?: number }).performance_score ?? 0
          }
          setPerfScores(scores)
        })
        .catch(() => {})
    }
    loadScores()
    const id = setInterval(loadScores, SCORE_POLL_MS)
    return () => clearInterval(id)
  }, [])

  // Sort by profit score desc; keep original order when no scores available yet
  const sortedSymbols = useMemo(() => {
    if (Object.keys(perfScores).length === 0) return availableSymbols
    return [...availableSymbols].sort(
      (a, b) => (perfScores[b] ?? 0) - (perfScores[a] ?? 0)
    )
  }, [availableSymbols, perfScores])

  return (
    <SymbolContext.Provider value={{ symbol, setSymbol, availableSymbols: sortedSymbols }}>
      {!isLoginPage && <NavBar />}
      <div className={isLoginPage ? undefined : 'pt-11'}>
        {!isLoginPage && <AlertBanner />}
        {children}
      </div>
    </SymbolContext.Provider>
  )
}
