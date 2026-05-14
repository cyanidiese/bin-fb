'use client'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import SymbolSwitcher from './SymbolSwitcher'
import { useSymbolContext } from '@/lib/SymbolContext'
import ModeBadge from './ModeBadge'

const NAV_LINKS = [
  { href: '/',          label: 'Strategy' },
  { href: '/backtest',  label: 'Backtest' },
  { href: '/trades',    label: 'Trades'   },
  { href: '/create',    label: 'Create'   },
  { href: '/risk',      label: 'Risk'     },
  { href: '/settings',  label: 'Settings' },
  { href: '/logs',      label: 'Logs'     },
]

export default function NavBar() {
  const pathname = usePathname()
  const router = useRouter()
  const { symbol, setSymbol, availableSymbols } = useSymbolContext()
  const [unread, setUnread] = useState(0)

  async function handleLogout() {
    await fetch('/api/auth/logout', { method: 'POST' })
    router.push('/login')
  }

  useEffect(() => {
    const lastRead = localStorage.getItem('log_last_read')
    fetch('/api/log')
      .then(r => r.json())
      .then((entries: Array<{ level: string; timestamp: string }>) => {
        const count = entries.filter(e =>
          ['warning', 'emergency'].includes(e.level) &&
          (!lastRead || new Date(e.timestamp) > new Date(lastRead))
        ).length
        setUnread(count)
      })
      .catch(() => {})
  }, [])

  return (
    <nav className="fixed top-0 inset-x-0 z-50 h-11 flex items-center gap-6 px-4 border-b border-gray-800 bg-gray-900 text-sm font-medium">
      <ModeBadge />
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
      <Link
        href="/log"
        className={`relative ${pathname === '/log' ? 'text-white' : 'text-gray-400 hover:text-white transition-colors'}`}
      >
        Log
        {unread > 0 && (
          <span className="absolute -top-1 -right-2 inline-flex items-center justify-center w-4 h-4 text-[10px] bg-red-600 text-white rounded-full">
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </Link>
      <div className="ml-6 mr-6 flex-1 min-w-0">
        <SymbolSwitcher symbols={availableSymbols} selected={symbol} onSelect={setSymbol} />
      </div>
      <button
        onClick={handleLogout}
        className="text-gray-400 hover:text-white transition-colors text-sm shrink-0"
      >
        Logout
      </button>
    </nav>
  )
}
