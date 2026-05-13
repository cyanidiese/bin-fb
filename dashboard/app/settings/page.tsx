'use client'

import { useState, useEffect, useCallback } from 'react'
import SymbolDiscovery from '@/components/SymbolDiscovery'

type BacktestStatus = 'none' | 'running' | 'complete' | 'error' | 'cancelled'

interface SymbolStatus {
  backtest: BacktestStatus
  pid: number | null
}

interface RegistryData {
  symbols: string[]
  updated_at: string
  status: Record<string, SymbolStatus>
}

interface BotState {
  running: boolean
  pid: number | null
  mode: string
  last_heartbeat: string | null
}

const POLL_MS = 3000

function StatusBadge({ status }: { status: BacktestStatus }) {
  if (status === 'running') {
    return (
      <span className="flex items-center gap-1.5 text-indigo-400" title="Backtest is currently running for this symbol">
        <svg className="animate-spin h-3 w-3 shrink-0" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4l3-3-3-3v4a8 8 0 00-8 8h4z" />
        </svg>
        running…
      </span>
    )
  }
  if (status === 'complete') {
    return <span className="text-emerald-400" title="Backtest finished successfully">✓ complete</span>
  }
  if (status === 'error') {
    return <span className="text-red-400" title="Backtest exited with an error — check terminal logs">✗ error</span>
  }
  if (status === 'cancelled') {
    return <span className="text-gray-500" title="Backtest was cancelled when the symbol was removed">cancelled</span>
  }
  return <span className="text-gray-600" title="No backtest has been run for this symbol yet">none</span>
}

export default function SettingsPage() {
  const [registry, setRegistry] = useState<RegistryData | null>(null)
  const [addInput, setAddInput] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<string | null>(null)
  const [botState, setBotState] = useState<BotState | null>(null)
  const [botActionLoading, setBotActionLoading] = useState(false)
  const [botActionError, setBotActionError] = useState('')
  const [mode, setMode] = useState<string>('test')
  const [modeSwitching, setModeSwitching] = useState(false)
  const [modeError, setModeError] = useState('')
  const [backtestRefreshing, setBacktestRefreshing] = useState(false)
  const [telegram, setTelegram] = useState({ token: '', chat_id: '' })
  const [telegramStatus, setTelegramStatus] = useState<'idle'|'testing'|'ok'|'error'>('idle')
  const [telegramError, setTelegramError] = useState('')
  const [notifyInterval, setNotifyInterval] = useState(120)
  const [testMsgType, setTestMsgType] = useState('connection')
  const [preview, setPreview] = useState({ liveRunning: false, testRunning: false, emergency: false })

  useEffect(() => {
    fetch('/api/mode').then(r => r.json()).then(d => setMode(d.mode)).catch(() => {})
  }, [])

  useEffect(() => {
    fetch('/api/risk').then(r => r.json()).then(d => {
      if (d.config?.telegram) setTelegram(d.config.telegram)
      if (d.config?.telegram_notify_interval_s != null) {
        setNotifyInterval(Number(d.config.telegram_notify_interval_s))
      }
    }).catch(() => {})
  }, [])

  const handleSwitchMode = async () => {
    const target = mode === 'test' ? 'live' : 'test'
    const botRunning = botState?.running ?? false
    const msg = botRunning
      ? target === 'live'
        ? 'Switch to LIVE mode? Real orders will be placed with real money.'
        : 'Switch to TEST mode? All open orders will be closed at market price.'
      : `Switch to ${target.toUpperCase()} mode?\n\nThe bot is not running — the mode preference will be saved and used on next start.\n\nRun "Refresh Backtests" afterward to load ${target}-mode kline data into the dashboard.`
    if (!confirm(msg)) return
    setModeSwitching(true)
    setModeError('')
    try {
      const r = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_mode: target }),
      })
      const data = await r.json()
      if (data.ok) setMode(target)
      else setModeError(data.error || 'Mode switch failed')
    } catch (e) {
      setModeError(String(e))
    } finally {
      setModeSwitching(false)
    }
  }

  const handleRefreshBacktests = async () => {
    if (!registry || registry.symbols.length === 0) return
    if (!confirm(`Re-run backtests for all ${registry.symbols.length} symbols in ${mode.toUpperCase()} mode?\n\nThis fetches fresh klines from the exchange for the current mode.`)) return
    setBacktestRefreshing(true)
    try {
      await Promise.allSettled(
        registry.symbols.map(sym =>
          fetch('/api/run-backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, klines_count: 1500 }),
          })
        )
      )
      fetchRegistry()
    } finally {
      setBacktestRefreshing(false)
    }
  }

  const saveTelegram = async () => {
    try {
      await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram }),
      })
    } catch {}
  }

  const saveInterval = async (value: number) => {
    await fetch('/api/risk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_notify_interval_s: value }),
    })
  }

  const testTelegram = async () => {
    setTelegramStatus('testing')
    setTelegramError('')
    try {
      const r = await fetch('/api/telegram/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: testMsgType }),
      })
      const d = await r.json()
      setTelegramStatus(d.ok ? 'ok' : 'error')
      if (!d.ok) setTelegramError(d.error || 'Unknown error')
    } catch (e) {
      setTelegramStatus('error')
      setTelegramError(String(e))
    }
  }

  const fetchRegistry = useCallback(async () => {
    try {
      const res = await fetch('/api/symbols')
      if (res.ok) setRegistry(await res.json())
    } catch { /* network error — keep last state */ }
  }, [])

  // Initial load + polling
  useEffect(() => {
    fetchRegistry()
    const id = setInterval(fetchRegistry, POLL_MS)
    return () => clearInterval(id)
  }, [fetchRegistry])

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch(`/bot_state.json?t=${Date.now()}`)
        if (!r.ok) { setBotState(null); return }
        const data = await r.json()
        const isStale = data.last_heartbeat
          ? (Date.now() - new Date(data.last_heartbeat).getTime()) > 30_000
          : true
        setBotState({ ...data, running: data.running && !isStale })
      } catch {
        setBotState(null)
      }
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const handleStart = async () => {
    setBotActionLoading(true)
    setBotActionError('')
    try {
      const r = await fetch('/api/bot/start', { method: 'POST' })
      const data = await r.json()
      if (!data.ok) setBotActionError(data.error || 'Failed to start bot')
    } catch (e) {
      setBotActionError(String(e))
    } finally {
      setBotActionLoading(false)
    }
  }

  const handleStop = async () => {
    if (!window.confirm('Stop the bot? All open orders will be closed at market price.')) return
    setBotActionLoading(true)
    setBotActionError('')
    try {
      const r = await fetch('/api/bot/stop', { method: 'POST' })
      const data = await r.json()
      if (!data.ok) setBotActionError(data.error || 'Failed to stop bot')
    } catch (e) {
      setBotActionError(String(e))
    } finally {
      setBotActionLoading(false)
    }
  }

  async function handleAdd() {
    const symbol = addInput.trim().toUpperCase()
    setAddError(null)
    if (!symbol) { setAddError('Enter a symbol name'); return }

    setAdding(true)
    try {
      const res = await fetch('/api/symbols', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol }),
      })
      const data = await res.json()
      if (!res.ok) { setAddError(data.error ?? `HTTP ${res.status}`); return }
      setAddInput('')
      fetchRegistry()
    } catch (e) {
      setAddError(String(e))
    } finally {
      setAdding(false)
    }
  }

  async function handleRemove(symbol: string) {
    const status = registry?.status[symbol]?.backtest
    const isRunning = status === 'running'
    const msg = isRunning
      ? `Cancel the running backtest for ${symbol} and remove it?\n\nPartial results will be discarded.`
      : `Remove ${symbol} from active symbols?`
    if (!window.confirm(msg)) return

    setRemoving(symbol)
    try {
      await fetch(`/api/symbols/${symbol}`, { method: 'DELETE' })
      fetchRegistry()
    } finally {
      setRemoving(null)
    }
  }

  const updatedAt = registry?.updated_at
    ? new Date(registry.updated_at).toLocaleTimeString()
    : '—'

  return (
    <main className="p-4 space-y-6 max-w-2xl">
      <div className="flex flex-wrap items-baseline gap-4">
        <h1 className="text-lg font-bold text-white">Settings</h1>
      </div>

      {/* ── Bot Control ──────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Bot Control
        </h2>
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4">
          <div className="flex items-center gap-4">
            {botState?.running ? (
              <button
                onClick={handleStop}
                disabled={botActionLoading}
                className="px-4 py-2 bg-red-600 text-white text-sm font-semibold rounded hover:bg-red-700 disabled:opacity-50 transition-colors"
              >
                {botActionLoading ? 'Stopping…' : 'Stop Bot'}
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={botActionLoading}
                className="px-4 py-2 bg-green-600 text-white text-sm font-semibold rounded hover:bg-green-700 disabled:opacity-50 transition-colors"
              >
                {botActionLoading ? 'Starting…' : 'Start Bot'}
              </button>
            )}
            <span className="text-sm text-gray-400">
              {botState === null
                ? 'Status unknown'
                : botState.running
                  ? `Running · ${botState.mode?.toUpperCase() ?? ''} mode`
                  : 'Stopped'}
            </span>
          </div>
          {botActionError && (
            <p className="mt-3 text-sm text-red-400">{botActionError}</p>
          )}
        </div>
      </section>

      {/* ── Trading Mode ─────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Trading Mode
        </h2>
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
          <div className="flex items-center gap-4">
            <span className={`px-3 py-1 rounded text-sm font-mono font-bold ${mode === 'live' ? 'bg-amber-500 text-black' : 'bg-slate-600 text-white'}`}>
              {mode.toUpperCase()}
            </span>
            <button
              onClick={handleSwitchMode}
              disabled={modeSwitching}
              className="px-4 py-2 bg-slate-700 text-white text-sm rounded hover:bg-slate-600 disabled:opacity-50 transition-colors"
            >
              {modeSwitching ? 'Switching…' : `Switch to ${mode === 'test' ? 'LIVE' : 'TEST'}`}
            </button>
            <button
              onClick={handleRefreshBacktests}
              disabled={backtestRefreshing || !registry || registry.symbols.length === 0}
              title={`Re-run backtests for all symbols using ${mode.toUpperCase()}-mode klines. Use this after switching mode to load fresh data.`}
              className="px-4 py-2 bg-indigo-900/60 border border-indigo-700 text-indigo-300 text-sm rounded hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {backtestRefreshing ? 'Refreshing…' : 'Refresh Backtests'}
            </button>
          </div>
          {!botState?.running && (
            <p className="text-xs text-gray-500">
              Bot is stopped — switching mode saves the preference for next start.
              Use <span className="text-indigo-400">Refresh Backtests</span> to pull {mode === 'live' ? 'live' : 'testnet'}-mode klines into the dashboard now.
            </p>
          )}
          {modeError && <p className="text-sm text-red-400">{modeError}</p>}
        </div>
      </section>

      {/* ── Symbol Registry ───────────────────────────────────────────── */}
      <section className="space-y-4">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Symbol Registry
        </h2>

        {/* Active symbols table */}
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 overflow-hidden">
          <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between">
            <p
              className="text-xs text-gray-500 font-semibold uppercase tracking-wide"
              title="Symbols currently tracked by the bot. Add or remove symbols below."
            >
              Active Symbols
            </p>
            <span
              className="text-[10px] text-gray-600 font-mono"
              title="Timestamp of the last change to symbol_registry.json"
            >
              {registry ? `${registry.symbols.length} symbol${registry.symbols.length !== 1 ? 's' : ''} · updated ${updatedAt}` : 'Loading…'}
            </span>
          </div>

          {registry && registry.symbols.length === 0 && (
            <p className="px-4 py-6 text-sm text-gray-600 italic text-center">
              No symbols registered. Add one below.
            </p>
          )}

          {registry && registry.symbols.length > 0 && (
            <table className="w-full text-xs font-mono">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500">
                  <th
                    className="text-left px-4 py-2 font-normal"
                    title="Binance USD-M Futures symbol"
                  >
                    Symbol
                  </th>
                  <th
                    className="text-left px-4 py-2 font-normal"
                    title="Status of the most recent backtest run for this symbol"
                  >
                    Backtest
                  </th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {registry.symbols.map(sym => {
                  const st = registry.status[sym] ?? { backtest: 'none', pid: null }
                  return (
                    <tr key={sym} className="border-b border-gray-900 hover:bg-gray-900/40">
                      <td className="px-4 py-2 text-indigo-300 font-semibold">{sym}</td>
                      <td className="px-4 py-2">
                        <StatusBadge status={st.backtest} />
                      </td>
                      <td className="px-4 py-2 text-right">
                        <button
                          onClick={() => handleRemove(sym)}
                          disabled={removing === sym}
                          title={
                            st.backtest === 'running'
                              ? `Cancel backtest and remove ${sym}`
                              : `Remove ${sym} from active symbols`
                          }
                          className="px-2 py-0.5 rounded border border-red-900/60 bg-red-950/30 text-red-400 text-[10px] font-semibold hover:bg-red-900/40 hover:text-red-300 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          {removing === sym ? 'Removing…' : 'Remove'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Add symbol */}
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
          <p
            className="text-xs text-gray-500 font-semibold uppercase tracking-wide"
            title="Add a new Binance USD-M Futures symbol. A backtest will start immediately."
          >
            Add Symbol
          </p>
          <div className="flex gap-2">
            <input
              type="text"
              value={addInput}
              onChange={e => { setAddInput(e.target.value.toUpperCase()); setAddError(null) }}
              onKeyDown={e => e.key === 'Enter' && !adding && handleAdd()}
              placeholder="e.g. ETHUSDT"
              disabled={adding}
              title="Enter a Binance USD-M Futures symbol (e.g. ETHUSDT, SOLUSDT). Must end in USDT."
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-gray-300 text-xs font-mono placeholder-gray-600 focus:outline-none focus:border-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed uppercase"
            />
            <button
              onClick={handleAdd}
              disabled={adding || !addInput.trim()}
              title="Add the symbol and start a backtest immediately"
              className="px-4 py-2 rounded border border-indigo-700 bg-indigo-900/60 text-indigo-300 text-xs font-semibold hover:bg-indigo-800/60 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
            >
              {adding ? 'Adding…' : 'Add'}
            </button>
          </div>
          {addError && (
            <p className="text-xs text-red-400 font-mono">{addError}</p>
          )}
          <p className="text-[10px] text-gray-600">
            A backtest over the last 1500 klines starts immediately after adding.
            Results appear on the Backtest page once complete.
          </p>
        </div>

        {/* Symbol Discovery */}
        <SymbolDiscovery />
      </section>

      {/* ── Telegram Alerts ──────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          Telegram Alerts
        </h2>
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3 max-w-md">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Bot Token</label>
            <input value={telegram.token}
              onChange={e => setTelegram(t => ({ ...t, token: e.target.value }))}
              onBlur={saveTelegram}
              type="password"
              placeholder="123456:ABCdef…"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Chat ID</label>
            <input value={telegram.chat_id}
              onChange={e => setTelegram(t => ({ ...t, chat_id: e.target.value }))}
              onBlur={saveTelegram}
              placeholder="123456789"
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Alert interval (min gap between messages)</label>
            <select
              value={notifyInterval}
              onChange={e => {
                const v = Number(e.target.value)
                setNotifyInterval(v)
                saveInterval(v)
              }}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
            >
              <option value={30}>30 seconds</option>
              <option value={120}>2 minutes</option>
              <option value={300}>5 minutes</option>
              <option value={600}>10 minutes</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Test message type</label>
            <select
              value={testMsgType}
              onChange={e => setTestMsgType(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
            >
              <option value="connection">Connection (backtest highlights)</option>
              <option value="trade_win">Trade Win</option>
              <option value="trade_loss">Trade Loss</option>
              <option value="emergency">Emergency (with @bo_pal)</option>
              <option value="balance_warning">Balance Warning</option>
            </select>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={testTelegram}
              disabled={telegramStatus === 'testing' || !telegram.token || !telegram.chat_id}
              className="px-3 py-1.5 bg-gray-700 rounded text-sm text-gray-200 hover:bg-gray-600 disabled:opacity-50 transition-colors"
            >
              {telegramStatus === 'testing' ? 'Sending…' : 'Send test notification'}
            </button>
            {telegramStatus === 'ok' && <span className="text-green-400 text-sm">✓ Sent</span>}
            {telegramStatus === 'error' && <span className="text-red-400 text-sm">✗ {telegramError}</span>}
          </div>
          <p className="text-xs text-gray-600">
            See <code className="text-gray-500">TELEGRAM_SETUP.md</code> for setup instructions.
          </p>
        </div>
      </section>

      {/* ── UI Preview ────────────────────────────────────────────────── */}
      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
          UI Preview
        </h2>
        <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
          <div className="space-y-2">
            {([
              ['liveRunning', 'Imitate live mode running'],
              ['testRunning', 'Imitate test mode running'],
              ['emergency', 'Imitate emergency notice'],
            ] as [keyof typeof preview, string][]).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={preview[key]}
                  onChange={e => setPreview(p => ({ ...p, [key]: e.target.checked }))}
                  className="rounded"
                />
                <span className="text-sm text-gray-300">{label}</span>
              </label>
            ))}
          </div>
          {(preview.liveRunning || preview.testRunning || preview.emergency) && (
            <div className="mt-2 p-3 border border-gray-700 rounded space-y-2">
              {(preview.liveRunning || preview.testRunning) && (
                <div className="flex items-center gap-2">
                  <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-mono font-semibold border
                    ${preview.liveRunning ? 'border-amber-500 text-amber-400' : 'border-slate-600 text-slate-400'}`}>
                    {preview.liveRunning && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />}
                    {preview.liveRunning ? 'LIVE' : 'TEST'} · RUNNING
                  </span>
                  <span className="text-xs text-gray-500">Badge preview</span>
                </div>
              )}
              {preview.emergency && (
                <div className="flex items-start gap-3 px-3 py-2 bg-red-950 border border-red-800 rounded text-sm">
                  <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-red-600 shrink-0">EMERGENCY</span>
                  <span className="text-red-200">Sample emergency alert · main · {new Date().toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
