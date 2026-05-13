import { NextRequest, NextResponse } from 'next/server'
import { jwtVerify } from 'jose'

const SECRET = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? '')

export async function proxy(req: NextRequest) {
  const token = req.cookies.get('auth_token')?.value
  if (token) {
    try {
      await jwtVerify(token, SECRET)
      return NextResponse.next()
    } catch {
      // token invalid or expired — fall through to redirect
    }
  }
  const next = encodeURIComponent(req.nextUrl.pathname + req.nextUrl.search)
  return NextResponse.redirect(new URL(`/login?next=${next}`, req.url))
}

export const config = {
  matcher: [
    '/((?!login|api/auth|_next/static|_next/image|favicon\\.ico).*)',
  ],
}
