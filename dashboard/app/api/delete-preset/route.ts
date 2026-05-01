import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const RESULTS_PATH = path.join(process.cwd(), 'public', 'backtest_results.json')

export async function DELETE(req: NextRequest) {
  try {
    const { name } = await req.json()
    if (!name?.trim()) {
      return NextResponse.json({ error: 'name is required' }, { status: 400 })
    }
    if (!fs.existsSync(RESULTS_PATH)) {
      return NextResponse.json({ error: 'backtest_results.json not found' }, { status: 404 })
    }
    const data = JSON.parse(fs.readFileSync(RESULTS_PATH, 'utf-8'))
    if (!data.presets[name]) {
      return NextResponse.json({ error: `Preset "${name}" not found` }, { status: 404 })
    }
    delete data.presets[name]
    fs.writeFileSync(RESULTS_PATH, JSON.stringify(data, null, 2))
    return NextResponse.json({ ok: true })
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
