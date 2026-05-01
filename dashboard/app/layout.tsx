import type { Metadata } from 'next'
import { Geist, Geist_Mono } from 'next/font/google'
import Link from 'next/link'
import './globals.css'

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] })
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'Binance Futures Bot — Dashboard',
  description: 'Bot results viewer',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased bg-gray-950 text-gray-100 min-h-screen`}>
        <nav className="border-b border-gray-800 bg-gray-900 px-4 py-2 flex gap-6 text-sm font-medium">
          <Link href="/" className="text-gray-300 hover:text-white transition-colors">Strategy</Link>
          <Link href="/backtest" className="text-gray-300 hover:text-white transition-colors">Backtest</Link>
          <Link href="/paper" className="text-gray-300 hover:text-white transition-colors">Paper</Link>
          <Link href="/create" className="text-gray-300 hover:text-white transition-colors">Create</Link>
        </nav>
        {children}
      </body>
    </html>
  )
}
