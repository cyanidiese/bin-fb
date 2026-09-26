#!/usr/bin/env bash
# Rebuild the preset Profit% store — every preset × date shortcut × registered symbol —
# for both modes: data/preset_profit_test.json and data/preset_profit_live.json.
# Spec: docs/specs/2026-09-26-preset-profit-store.md
#
# Run on the server:   bash scripts/recalc_symbol_scores.sh
#
# Normally unnecessary: the dashboard's background worker (instrumentation.ts) refreshes
# stale symbols every 30 s. Use this after a deploy that changes the formula, or to force
# everything at once. The computation is the dashboard's own (POST /api/trades/preset-profit),
# so it can never disagree with the page. It runs inside the dashboard container, signs a
# 10-minute session token with the DASHBOARD_SECRET the login route uses, and calls the
# route on localhost. "Today" starts at midnight in PROFIT_TZ (default Europe/Kyiv),
# set on the dashboard, not here.
set -euo pipefail

CONTAINER="${DASHBOARD_CONTAINER:-dashboard}"

docker exec -w /app/dashboard "$CONTAINER" node --input-type=module -e '
import { SignJWT } from "jose"

const secret = new TextEncoder().encode(process.env.DASHBOARD_SECRET ?? "")
const token = await new SignJWT({ sub: "dashboard" })
  .setProtectedHeader({ alg: "HS256" }).setIssuedAt().setExpirationTime("10m").sign(secret)
const headers = { cookie: `auth_token=${token}`, "content-type": "application/json" }

let failed = 0
for (const mode of ["test", "live"]) {
  try {
    const r = await fetch("http://localhost:3000/api/trades/preset-profit",
      { method: "POST", headers, redirect: "manual", body: JSON.stringify({ mode, force: true }) })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const { recomputed, ms } = await r.json()
    const s = await (await fetch(`http://localhost:3000/api/trades/preset-profit?mode=${mode}`,
      { headers, redirect: "manual" })).json()
    const syms = Object.values(s.symbols)
    const ranges = Object.keys(syms[0]?.ranges ?? {})
    const cells = syms.reduce((n, x) => n + Object.values(x.ranges)
      .reduce((m, rg) => m + Object.keys(rg.presets).length, 0), 0)
    console.log(`${mode.padEnd(4)} ${recomputed.length} symbols x ${ranges.length} shortcuts (${ranges.join(", ")}): ${cells} preset numbers stored in ${ms} ms`)
  } catch (e) {
    failed++
    console.error(`${mode} FAILED: ${e.message}`)
  }
}
process.exit(failed ? 1 : 0)
'
