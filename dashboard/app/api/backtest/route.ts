import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'

// Bot root is one level above the Next.js project root
const BOT_ROOT = path.resolve(process.cwd(), '..')

// Prefer venv python (has all bot dependencies), fall back to system python3
function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

export async function POST(req: NextRequest) {
  let settings: Record<string, unknown>
  try {
    const body = await req.json()
    settings = body.settings ?? {}
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  const python = getPython()

  return new Promise<NextResponse>(resolve => {
    let stdout = ''
    let stderr = ''

    const child = spawn(python, ['backtest_api.py', JSON.stringify(settings)], {
      cwd: BOT_ROOT,
    })

    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })

    child.on('error', err => {
      resolve(NextResponse.json({ error: `Failed to start Python: ${err.message}` }, { status: 500 }))
    })

    child.on('close', code => {
      if (code !== 0) {
        resolve(NextResponse.json(
          { error: stderr.trim() || `Python exited with code ${code}` },
          { status: 500 }
        ))
        return
      }
      try {
        const data = JSON.parse(stdout)
        resolve(NextResponse.json(data))
      } catch {
        resolve(NextResponse.json(
          { error: 'Failed to parse Python output', raw: stdout.slice(0, 500) },
          { status: 500 }
        ))
      }
    })
  })
}
