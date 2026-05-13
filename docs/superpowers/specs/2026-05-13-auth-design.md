# Dashboard Auth Implementation Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add password-based login to the Next.js dashboard so no unauthenticated user can view or modify anything.

**Architecture:** JWT httpOnly cookie verified by Next.js middleware on every request. Single password stored in `dashboard/.env.local`. No database, no external auth service.

**Tech Stack:** Next.js 16 / TypeScript, `jose` (JWT, Edge-compatible), Tailwind CSS.

---

## 1. Credentials

`dashboard/.env.local` (git-ignored, already created):
```
DASHBOARD_PASSWORD=bimbabot!1
DASHBOARD_SECRET=7a04ad4a177b4c3f60821b42b73fa78a97d6e393ee99d8a485ea3d5d50ed17c3
```

- `DASHBOARD_PASSWORD` — plaintext password compared at login time
- `DASHBOARD_SECRET` — 64-char hex string used to sign/verify JWTs

---

## 2. New Files

| File | Purpose |
|---|---|
| `dashboard/middleware.ts` | Edge middleware: verify JWT on every request, redirect to `/login` if invalid |
| `dashboard/app/login/page.tsx` | Login form — password field + submit button, dark-themed |
| `dashboard/app/api/auth/login/route.ts` | POST: compare password, sign JWT, set cookie |
| `dashboard/app/api/auth/logout/route.ts` | POST: clear cookie, redirect to `/login` |

---

## 3. Dependency

Add `jose` to `dashboard/package.json`:
```bash
cd dashboard && npm install jose
```

`jose` is the standard JWT library for Next.js Edge Runtime. No Node.js built-ins required.

---

## 4. Middleware (`dashboard/middleware.ts`)

Runs on every request. Reads the `auth_token` cookie, verifies the JWT. Passes through if valid; redirects to `/login?next=<original-path>` if missing or invalid.

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { jwtVerify } from 'jose'

const SECRET = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? '')

export async function middleware(req: NextRequest) {
  const token = req.cookies.get('auth_token')?.value
  if (token) {
    try {
      await jwtVerify(token, SECRET)
      return NextResponse.next()
    } catch {}
  }
  const next = encodeURIComponent(req.nextUrl.pathname)
  return NextResponse.redirect(new URL(`/login?next=${next}`, req.url))
}

export const config = {
  matcher: [
    '/((?!login|api/auth|_next/static|_next/image|favicon.ico).*)',
  ],
}
```

---

## 5. Login API (`dashboard/app/api/auth/login/route.ts`)

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { SignJWT } from 'jose'

const SECRET = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? '')
const PASSWORD = process.env.DASHBOARD_PASSWORD ?? ''
const COOKIE_MAX_AGE = 60 * 60 * 24 * 7  // 7 days in seconds

export async function POST(req: NextRequest) {
  const { password } = await req.json()
  if (!password || password !== PASSWORD) {
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
```

---

## 6. Logout API (`dashboard/app/api/auth/logout/route.ts`)

```typescript
import { NextResponse } from 'next/server'

export async function POST() {
  const res = NextResponse.json({ ok: true })
  res.cookies.set('auth_token', '', { maxAge: 0, path: '/' })
  return res
}
```

---

## 7. Login Page (`dashboard/app/login/page.tsx`)

- Full-screen centered form on the dark background (`bg-gray-950`)
- Single password `<input type="password">` field + "Log in" button
- On submit: POST to `/api/auth/login`; on success redirect to `?next` param or `/`; on failure show "Invalid password" inline error
- No username field — single-user, password only
- Styled to match the rest of the dashboard (Tailwind, dark theme)

The form is a client component (`'use client'`) using `useState` for the password field and error state.

---

## 8. Logout Button (`dashboard/components/NavBar.tsx`)

Add a small "Logout" button to the existing NavBar (top-right or end of nav). On click, POST to `/api/auth/logout`, then `router.push('/login')`.

---

## 9. Files Touched

| File | Change |
|---|---|
| `dashboard/middleware.ts` | **Create** |
| `dashboard/app/login/page.tsx` | **Create** |
| `dashboard/app/api/auth/login/route.ts` | **Create** |
| `dashboard/app/api/auth/logout/route.ts` | **Create** |
| `dashboard/components/NavBar.tsx` | Add logout button |
| `dashboard/package.json` | Add `jose` dependency |
| `dashboard/.env.local` | Already created — credentials live here |

---

## 10. Edge Cases

- **`DASHBOARD_SECRET` not set:** `jose` will throw on sign/verify; middleware will redirect all requests to `/login` (safe failure — nothing is accessible)
- **`DASHBOARD_PASSWORD` not set:** login always fails with 401 (safe failure)
- **Expired token:** `jwtVerify` throws → redirect to `/login`; user re-authenticates
- **Direct navigation to `/login` while already logged in:** middleware passes through (user sees the login page; acceptable, no redirect loop)
- **`next` redirect param:** used only for same-origin paths; the login page reads `searchParams.get('next')` and redirects after success
