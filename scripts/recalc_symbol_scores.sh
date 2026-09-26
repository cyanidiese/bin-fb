#!/usr/bin/env bash
# Recompute the Trades picker sort keys (top-preset Profit%) for every registered
# symbol × every date shortcut × both modes, into data/symbol_sort_scores_{mode}.json.
#
# Run on the server:   bash scripts/recalc_symbol_scores.sh [Europe/Kyiv]
#
# The computation itself is the dashboard's /api/trades/symbol-scores route, called
# once per symbol with ensure=SYM — exactly what a click on the picker does — so the
# script can never disagree with what the page shows. It runs
# inside the dashboard container, signs a 10-minute session token with the same
# DASHBOARD_SECRET the login route uses, and calls the route on localhost.
#
# The optional argument is the timezone whose midnight "Today" starts at — it must be
# the browser's, because the page asks for local midnight. Other shortcuts slide and
# are timezone-free. Harmless to re-run: entries that are already fresh are kept.
set -euo pipefail

TZ_NAME="${1:-Europe/Kyiv}"
CONTAINER="${DASHBOARD_CONTAINER:-dashboard}"

docker exec -e TZ="$TZ_NAME" -w /app/dashboard "$CONTAINER" node --input-type=module -e '
import { SignJWT } from "jose"
import fs from "fs"

// Every symbol the picker can show — the list /api/trades/symbols reads.
const symbols = JSON.parse(fs.readFileSync("/app/dashboard/public/symbols.json", "utf8")).symbols ?? []

const secret = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? "")
const token = await new SignJWT({ sub: "dashboard" })
  .setProtectedHeader({ alg: "HS256" }).setIssuedAt().setExpirationTime("10m").sign(secret)

// Same window starts the page sends (lib/tradesDateRange.ts presetRange), minute-rounded
// like its datetime-local inputs.
function fromFor(range) {
  const now = new Date()
  now.setSeconds(0, 0)
  if (range === "all") return ""
  if (range === "today") { now.setHours(0, 0, 0, 0); return String(now.getTime() / 1000) }
  const days = { "24h": 1, "7d": 7, "30d": 30 }[range]
  return String(now.getTime() / 1000 - days * 86400)
}

let failed = 0
for (const mode of ["test", "live"]) {
  for (const range of ["today", "24h", "7d", "30d", "all"]) {
    const t0 = Date.now()
    let scores = {}
    for (const sym of symbols) {
      const url = `http://localhost:3000/api/trades/symbol-scores?mode=${mode}&range=${range}&from=${fromFor(range)}&ensure=${sym}`
      try {
        const r = await fetch(url, { headers: { cookie: `auth_token=${token}` }, redirect: "manual" })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        scores = (await r.json()).scores
        if (!scores[sym]) throw new Error("no entry written")
      } catch (e) {
        failed++
        console.error(`${mode} ${range} ${sym} FAILED: ${e.message}`)
      }
    }
    const vals = symbols.map(s => scores[s]).filter(Boolean)
    const withPct = vals.filter(v => v.pct !== null).length
    console.log(`${mode.padEnd(4)} ${range.padEnd(5)} ${String(vals.length).padStart(3)}/${symbols.length} symbols, ${String(withPct).padStart(3)} with trades  (${Date.now() - t0} ms)`)
  }
}
process.exit(failed ? 1 : 0)
'
