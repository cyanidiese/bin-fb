import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'

const BOT_ROOT = path.resolve(process.cwd(), '..')

function getPython(): string {
  const venvPy = path.join(BOT_ROOT, '.venv', 'bin', 'python3')
  return fs.existsSync(venvPy) ? venvPy : 'python3'
}

export async function POST(req: NextRequest) {
  let symbol: string
  let candle_index: number
  try {
    const body = await req.json()
    if (typeof body.symbol !== 'string' || !body.symbol.trim()) {
      return NextResponse.json({ error: 'symbol required' }, { status: 400 })
    }
    if (
      typeof body.candle_index !== 'number' ||
      !Number.isInteger(body.candle_index) ||
      body.candle_index < 0
    ) {
      return NextResponse.json(
        { error: 'candle_index must be a non-negative integer' },
        { status: 400 },
      )
    }
    symbol = body.symbol.trim().toUpperCase()
    candle_index = body.candle_index
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  const python = getPython()
  const payload = JSON.stringify({ symbol, candle_index })

  return new Promise<NextResponse>(resolve => {
    let stdout = ''
    let stderr = ''

    const child = spawn(python, ['replay_api.py', payload], { cwd: BOT_ROOT })

    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk })
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk })

    child.on('error', (err: Error) => {
      resolve(NextResponse.json({ error: `Failed to start Python: ${err.message}` }, { status: 500 }))
    })

    child.on('close', (code: number) => {
      if (code !== 0) {
        resolve(NextResponse.json(
          { error: stderr.trim() || `Python exited with code ${code}` },
          { status: 500 },
        ))
        return
      }
      try {
        const data = JSON.parse(stdout)
        if (data.error) {
          resolve(NextResponse.json({ error: data.error }, { status: 500 }))
          return
        }
        resolve(NextResponse.json(data))
      } catch {
        resolve(NextResponse.json(
          { error: 'Failed to parse Python output', raw: stdout.slice(0, 500) },
          { status: 500 },
        ))
      }
    })
  })
}
