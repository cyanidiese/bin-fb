import { NextRequest, NextResponse } from 'next/server'
import { SignJWT } from 'jose'

const _secret = process.env.DASHBOARD_SECRET ?? ''
const PASSWORD = process.env.DASHBOARD_PASSWORD ?? ''
if (!_secret || !PASSWORD) {
  console.error('[auth] DASHBOARD_SECRET or DASHBOARD_PASSWORD is not set — login will be rejected')
}
const SECRET = new TextEncoder().encode(_secret)
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7  // 7 days

export async function POST(req: NextRequest) {
  let body: { password?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Bad request' }, { status: 400 })
  }

  if (!PASSWORD || !_secret) {
    return NextResponse.json({ error: 'Auth not configured — set DASHBOARD_PASSWORD and DASHBOARD_SECRET in .env' }, { status: 503 })
  }
  if (!body.password || body.password !== PASSWORD) {
    return NextResponse.json({ error: 'Invalid password' }, { status: 401 })
  }

  const token = await new SignJWT({ sub: 'dashboard' })
    .setProtectedHeader({ alg: 'HS256' })
    .setExpirationTime('7d')
    .sign(SECRET)

  const res = NextResponse.json({ ok: true })
  res.cookies.set('auth_token', token, {
    httpOnly: true,
    sameSite: 'lax',
    maxAge: COOKIE_MAX_AGE,
    path: '/',
  })
  return res
}
