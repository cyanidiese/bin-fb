import { NextRequest, NextResponse } from 'next/server'
import { randomUUID } from 'crypto'
import path from 'path'
import fs from 'fs'
import { BOT_ROOT } from '../../_utils'

const COMMAND_PATH = path.join(BOT_ROOT, 'data', 'bot_command.json')
const RESULT_PATH = path.join(BOT_ROOT, 'data', 'bot_command_result.json')
const MODE_PATH = path.join(BOT_ROOT, 'data', 'bot_mode.json')

// The bot polls bot_command.json every 2s, so 10s covers a poll plus a market close with
// margin. Falling back to "assume it worked" would be wrong here — this moves real money,
// so a timeout is reported as unknown and the caller re-reads the position list.
const COMMAND_TIMEOUT_MS = 10_000

function currentMode(): string {
  try {
    return (JSON.parse(fs.readFileSync(MODE_PATH, 'utf8')) as { mode?: string }).mode ?? 'test'
  } catch {
    return 'test'
  }
}

async function waitForResult(id: string): Promise<{ done: boolean; ok: boolean; error?: string }> {
  const deadline = Date.now() + COMMAND_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 300))
    if (!fs.existsSync(RESULT_PATH)) continue
    try {
      const r = JSON.parse(fs.readFileSync(RESULT_PATH, 'utf8'))
      if (r.id === id) return { done: true, ok: !!r.ok, error: r.error ?? undefined }
    } catch { /* mid-write; try again */ }
  }
  return { done: false, ok: false }
}

/**
 * POST /api/orders/close — close one open position.
 *
 * Body: { symbol, kind: 'real' | 'virtual', rank?, mode? }
 *
 * Only the running instance can close its own positions: the command file is shared and
 * only the primary polls it, so a request aimed at the other instance is refused here
 * rather than being written and silently applied to the wrong position.
 */
export async function POST(req: NextRequest) {
  let body: { symbol?: string; kind?: string; rank?: number; mode?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, error: 'Invalid JSON body' }, { status: 400 })
  }

  const symbol = (body.symbol ?? '').toUpperCase()
  const kind = (body.kind ?? '').toLowerCase()
  if (!symbol) return NextResponse.json({ ok: false, error: 'symbol required' }, { status: 400 })
  if (kind !== 'real' && kind !== 'virtual') {
    return NextResponse.json({ ok: false, error: "kind must be 'real' or 'virtual'" }, { status: 400 })
  }
  if (kind === 'virtual' && (body.rank === undefined || body.rank === null)) {
    return NextResponse.json({ ok: false, error: 'rank required for a virtual close' }, { status: 400 })
  }

  const running = currentMode()
  const wanted = body.mode ?? running
  if (wanted !== running) {
    return NextResponse.json({
      ok: false,
      error: `The bot is running '${running}'. Positions of the '${wanted}' instance can `
           + `only be closed by that instance, and it does not accept commands.`,
    }, { status: 409 })
  }

  const id = randomUUID()
  try {
    const tmp = COMMAND_PATH + '.tmp'
    fs.writeFileSync(tmp, JSON.stringify({
      id,
      type: 'close_order',
      payload: { symbol, kind, rank: body.rank, mode: wanted },
      issued_at: new Date().toISOString(),
    }))
    fs.renameSync(tmp, COMMAND_PATH)
  } catch (err) {
    return NextResponse.json({ ok: false, error: `Failed to queue the close: ${err}` }, { status: 500 })
  }

  const res = await waitForResult(id)
  if (!res.done) {
    return NextResponse.json({
      ok: false,
      pending: true,
      error: 'The bot did not answer in time. It may still have closed the position — '
           + 'reload before trying again.',
    }, { status: 504 })
  }
  if (!res.ok) {
    return NextResponse.json({ ok: false, error: res.error ?? 'The bot refused the close' }, { status: 409 })
  }
  return NextResponse.json({ ok: true, symbol, kind, rank: body.rank ?? null })
}
