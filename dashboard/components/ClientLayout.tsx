'use client'
import { usePathname } from 'next/navigation'
import { SymbolContext } from '@/lib/SymbolContext'
import { useSymbols } from '@/lib/useSymbols'
import { useSymbol } from '@/lib/useSymbol'
import NavBar from './NavBar'
import AlertBanner from './AlertBanner'

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const availableSymbols = useSymbols()
  const [symbol, setSymbol] = useSymbol(availableSymbols)
  const pathname = usePathname()
  const isLoginPage = pathname === '/login'

  return (
    <SymbolContext.Provider value={{ symbol, setSymbol, availableSymbols }}>
      {!isLoginPage && <AlertBanner />}
      {!isLoginPage && <NavBar />}
      <div className={isLoginPage ? undefined : 'pt-11'}>{children}</div>
    </SymbolContext.Provider>
  )
}
