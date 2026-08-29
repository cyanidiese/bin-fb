import { NextRequest, NextResponse } from 'next/server'
import { jwtVerify } from 'jose'

const SECRET = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? '')

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

/**
 * Local development bypasses the login screen.
 *
 * SECURITY: the hostname alone is NOT sufficient. req.nextUrl.hostname is derived
 * from the Host header, which any client can set — so a bare localhost check would
 * let anyone bypass auth on the public server (185.237.14.105:3000) just by sending
 * `Host: localhost`. The bypass therefore ALSO requires a non-production build.
 *
 * The server runs `next start` (NODE_ENV=production) so the bypass can never engage
 * there, regardless of what Host header arrives. Local `npm run dev` is development,
 * so localhost:3000 is logged in automatically.
 *
 * If you ever want to run a production build locally without logging in, set
 * DASHBOARD_LOCAL_NO_AUTH=1 in the local environment only — never in the server .env.
 */
function isTrustedLocal(req: NextRequest): boolean {
  if (!LOOPBACK_HOSTS.has(req.nextUrl.hostname)) return false
  return process.env.NODE_ENV !== 'production'
      || process.env.DASHBOARD_LOCAL_NO_AUTH === '1'
}

export async function proxy(req: NextRequest) {
  if (isTrustedLocal(req)) return NextResponse.next()

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
