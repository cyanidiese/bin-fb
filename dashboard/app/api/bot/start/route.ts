import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import { BOT_ROOT, isAlive } from '../../_utils'
import path from 'path'
import fs from 'fs'

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

export async function POST() {
  // Check if already running
  const statePath = path.join(BOT_ROOT, 'dashboard', 'public', 'bot_state.json')
  if (fs.existsSync(statePath)) {
    try {
      const state = JSON.parse(fs.readFileSync(statePath, 'utf8'))
      if (state.running && state.pid && isAlive(state.pid)) {
        return NextResponse.json({ ok: false, error: 'Bot is already running' }, { status: 409 })
      }
    } catch {}
  }

  const python = getPython()
  const child = spawn(python, ['main.py'], {
    cwd: BOT_ROOT,
    detached: true,
    stdio: 'ignore',
  })
  child.unref()

  return NextResponse.json({ ok: true, pid: child.pid ?? null })
}
