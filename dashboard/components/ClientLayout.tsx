'use client'
import { SymbolContext } from '@/lib/SymbolContext'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
import NavBar from './NavBar'
import AlertBanner from './AlertBanner'

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)

  return (
    <SymbolContext.Provider value={{ symbol, setSymbol, availableSymbols }}>
      <AlertBanner />
      <NavBar />
      <div className="pt-11">{children}</div>
    </SymbolContext.Provider>
  )
}
