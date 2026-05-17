import { NextResponse } from 'next/server'
import { readRegistry, writeRegistry } from '../_registry'

/** POST /api/symbols/enable-all — clear all auto-disabled symbols. */
export async function POST() {
  const reg = readRegistry()
  const count = Object.keys(reg.disabled ?? {}).length
  delete reg.disabled
  writeRegistry(reg)
  return NextResponse.json({ ok: true, cleared: count })
}
