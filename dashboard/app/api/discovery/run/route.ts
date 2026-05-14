import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { BOT_ROOT, isAlive } from '../../_utils'

const STATE_PATH = path.join(BOT_ROOT, 'dashboard', 'public', 'discovery_state.json')
const CONFIG_PATH = path.join(BOT_ROOT, 'data', 'discovery_config.json')

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

function readState(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'))
  } catch {
    return { status: 'idle', pid: null, total_precandidates: 0, processed_count: 0, in_progress: [] }
  }
}

function writeState(data: Record<string, unknown>): void {
  fs.mkdirSync(path.dirname(STATE_PATH), { recursive: true })
  fs.writeFileSync(STATE_PATH, JSON.stringify(data, null, 2))
}

export async function POST(req: NextRequest) {
  let body: Record<string, unknown> = {}
  try { body = await req.json() } catch { /* use defaults */ }

  const cfg = {
    min_volume:     Number(body.min_volume     ?? 1_000_000),
    preset_count:   Number(body.preset_count   ?? 12),
    batch_size:     Number(body.batch_size     ?? 3),
    baseline_ratio: Number(body.baseline_ratio ?? 0.7),
    min_floor:      Number(body.min_floor      ?? 0.0),
    position_size:  Number(body.position_size  ?? 1000),
    leverage:       Number(body.leverage       ?? 1),
    klines_count:   Number(body.klines_count   ?? 500),
  }

  // Check for a live run (allow stale runs with dead PIDs to be restarted).
  const state = readState()
  if (state.status === 'running' && isAlive(state.pid as number | null)) {
    return NextResponse.json({ error: 'Discovery already running' }, { status: 409 })
  }

  // Write config for discover.py to read.
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true })
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2))

  // Write initial state before spawning (discover.py only updates progress).
  writeState({
    status: 'running',
    pid: null,
    total_precandidates: 0,
    processed_count: 0,
    in_progress: [],
    last_run_timestamp: null,
  })

  const python = getPython()
  const child = spawn(python, ['discover.py'], {
    cwd: BOT_ROOT,
    detached: false,
    stdio: 'ignore',
  })

  // Store PID as soon as we have it.
  if (child.pid) {
    const s = readState()
    s.pid = child.pid
    writeState(s)
  }

  child.on('close', (code: number | null) => {
    const s = readState()
    if (s.status === 'running') {
      s.status = code === 0 ? 'complete' : 'error'
      if (code !== 0) s.error = `discover.py exited with code ${code}`
      writeState(s)
    }
  })

  child.unref()

  return NextResponse.json({ ok: true, pid: child.pid ?? null })
}
