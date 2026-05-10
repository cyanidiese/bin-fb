import { NextResponse } from 'next/server'
import { BOT_ROOT } from '../../_utils'
import path from 'path'
import fs from 'fs'

const CONFIG_PATH = path.join(BOT_ROOT, 'risk_config.json')

export async function POST() {
  // Read token + chat_id directly from saved config — no bot process needed
  let token = ''
  let chatId = ''
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'))
    token = cfg?.telegram?.token ?? ''
    chatId = String(cfg?.telegram?.chat_id ?? '')
  } catch {
    return NextResponse.json({ ok: false, error: 'Could not read risk_config.json' }, { status: 500 })
  }

  if (!token || !chatId) {
    return NextResponse.json(
      { ok: false, error: 'Telegram token or chat ID not configured. Save them in Settings first.' },
      { status: 400 },
    )
  }

  try {
    const url = `https://api.telegram.org/bot${token}/sendMessage`
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: '✅ Test notification from your trading bot dashboard.' }),
    })
    const data = await res.json()
    if (!res.ok || !data.ok) {
      return NextResponse.json({ ok: false, error: data.description ?? 'Telegram API error' }, { status: 502 })
    }
    return NextResponse.json({ ok: true })
  } catch (err) {
    return NextResponse.json({ ok: false, error: String(err) }, { status: 502 })
  }
}
