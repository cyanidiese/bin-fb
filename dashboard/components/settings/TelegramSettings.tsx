'use client'

import { useState, useEffect } from 'react'

export default function TelegramSettings() {
  const [telegram, setTelegram] = useState({ token: '', chat_id: '' })
  const [telegramStatus, setTelegramStatus] = useState<'idle' | 'testing' | 'ok' | 'error'>('idle')
  const [telegramError, setTelegramError] = useState('')
  const [notifyInterval, setNotifyInterval] = useState(120)
  const [emergencyRepeat, setEmergencyRepeat] = useState(1800)
  const [warningRepeat, setWarningRepeat] = useState(14400)
  const [testMsgType, setTestMsgType] = useState('connection')

  useEffect(() => {
    fetch('/api/risk').then(r => r.json()).then(d => {
      if (d.config?.telegram) setTelegram(d.config.telegram)
      if (d.config?.telegram_notify_interval_s != null) {
        setNotifyInterval(Number(d.config.telegram_notify_interval_s))
      }
      if (d.config?.emergency_repeat_interval_s != null) setEmergencyRepeat(Number(d.config.emergency_repeat_interval_s))
      if (d.config?.warning_repeat_interval_s != null) setWarningRepeat(Number(d.config.warning_repeat_interval_s))
    }).catch(() => {})
  }, [])

  async function saveTelegram() {
    try {
      await fetch('/api/risk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram }),
      })
    } catch {}
  }

  async function saveInterval(value: number) {
    await fetch('/api/risk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ telegram_notify_interval_s: value }),
    })
  }

  async function saveEmergencyRepeat(value: number) {
    await fetch('/api/risk', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emergency_repeat_interval_s: value }) })
  }

  async function saveWarningRepeat(value: number) {
    await fetch('/api/risk', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ warning_repeat_interval_s: value }) })
  }

  async function testTelegram() {
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

  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide">
        Telegram Alerts
      </h2>
      <div className="rounded-lg border border-gray-800 bg-gray-900/50 px-4 py-4 space-y-3">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Bot Token</label>
          <input
            value={telegram.token}
            onChange={e => setTelegram(t => ({ ...t, token: e.target.value }))}
            onBlur={saveTelegram}
            type="password"
            placeholder="123456:ABCdef…"
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Chat ID</label>
          <input
            value={telegram.chat_id}
            onChange={e => setTelegram(t => ({ ...t, chat_id: e.target.value }))}
            onBlur={saveTelegram}
            placeholder="123456789"
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
          />
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
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
          >
            <option value={30}>30 seconds</option>
            <option value={120}>2 minutes</option>
            <option value={300}>5 minutes</option>
            <option value={600}>10 minutes</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Emergency repeat cooldown (same message)</label>
          <select value={emergencyRepeat} onChange={e => { const v = Number(e.target.value); setEmergencyRepeat(v); saveEmergencyRepeat(v) }}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500">
            <option value={300}>5 minutes</option>
            <option value={900}>15 minutes</option>
            <option value={1800}>30 minutes</option>
            <option value={3600}>1 hour</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Warning repeat cooldown (same message)</label>
          <select value={warningRepeat} onChange={e => { const v = Number(e.target.value); setWarningRepeat(v); saveWarningRepeat(v) }}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500">
            <option value={1800}>30 minutes</option>
            <option value={3600}>1 hour</option>
            <option value={7200}>2 hours</option>
            <option value={14400}>4 hours</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Test message type</label>
          <select
            value={testMsgType}
            onChange={e => setTestMsgType(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-300 focus:outline-none focus:border-indigo-500"
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
  )
}
