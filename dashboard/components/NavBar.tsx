'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import SymbolSwitcher from './SymbolSwitcher'
import { useSymbolContext } from '@/lib/SymbolContext'

const NAV_LINKS = [
  { href: '/',          label: 'Strategy' },
  { href: '/backtest',  label: 'Backtest' },
  { href: '/paper',     label: 'Paper'    },
  { href: '/create',    label: 'Create'   },
  { href: '/risk',      label: 'Risk'     },
  { href: '/settings',  label: 'Settings' },
]

export default function NavBar() {
  const pathname = usePathname()
  const { symbol, setSymbol, availableSymbols } = useSymbolContext()

  return (
    <nav className="fixed top-0 inset-x-0 z-50 h-11 flex items-center gap-6 px-4 border-b border-gray-800 bg-gray-900 text-sm font-medium">
      {NAV_LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          className={
            pathname === href
              ? 'text-white'
              : 'text-gray-400 hover:text-white transition-colors'
          }
        >
          {label}
        </Link>
      ))}
      <div className="ml-auto">
        <SymbolSwitcher symbols={availableSymbols} selected={symbol} onSelect={setSymbol} />
      </div>
    </nav>
  )
}
