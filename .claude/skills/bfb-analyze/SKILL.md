---
name: bfb-analyze
description: >
  Profit/analysis session for the Binance Futures bot. Trigger on: "analyze logs",
  "analyze orders", "analyze trades", "act like wise owner", "improve profits",
  "what should we work on?", "what's losing money?", "performance analysis",
  "check win rate", "why are we losing?", any request to review trading performance.
---

# BFB — Profit & Analysis Session

## Rule 1: Pull data locally FIRST. Never analyze via sequential SSH commands.

```bash
scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/logs/bot.log /tmp/bot_latest.log

scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/risk_config.json /tmp/risk_config_live.json

ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cat /opt/bot/data/streak_state_test.json 2>/dev/null || echo NOT_FOUND" \
  > /tmp/streak_state.json
```

Then analyze `/tmp/bot_latest.log` locally. Do not SSH into the server to read log lines.

## Rule 2: Read the master analysis index

`docs/profit-analysis/README.md` — always read this before dispatching agents.
It has the step-by-step flow, quick decision table, and known persistent issues.

## Rule 3: Dispatch Analyst agents in parallel (one message, multiple Agent calls)

Minimum three agents:
- **Agent A (analyst):** Real P&L by symbol and preset from `/tmp/bot_latest.log`
- **Agent B (analyst):** Loss pattern audit — consecutive losses, unblocked streaks, SL hit within 1 candle
- **Agent C (Explore):** Unprotected presets scan in `config/presets.py`

See `docs/profit-analysis/subagents.md` for exact brief templates.

## Rule 4: Every proposed change must include numbers

Template: *"Blocking X saves $Y (Z trades × avg $A loss) at cost of ~N winners ($B)."*

Never block a preset/symbol/signal type without showing: **win rate + trade count + net USDT impact**.

## Rule 5: Hot-reload vs deploy

- **Hot-reload** (risk_config.json): apply immediately, takes effect next candle, no restart.
  Use `/bfb-config` skill.
- **Code change**: propose → get explicit user approval → Architect → Coder → `/bfb-deploy`.

## Anti-oscillation rule

Do NOT reverse a config setting changed in the last 7 days without 7+ real trades of evidence
that it is performing worse. "It feels wrong" is not evidence.

## Decision thresholds (when to act on a symbol or preset)

| Situation | Action |
|---|---|
| Symbol net PnL < −$20 with ≥10 real trades | weight → 0 (hot-reload) |
| Symbol win rate < 15% with ≥8 trades | weight → 0 |
| Symbol win rate < 25% with ≥8 trades | weight ≤ 5 |
| Symbol net PnL > +$15 with ≥8 trades | candidate for weight increase |
| Symbol weight > 15 and net negative | immediate review required |
| Preset: ≥4 trades, 0 wins | add to `preset_blocklist` (hot-reload) |
| Same config changed 3× in 3 sessions | stop tuning — load `docs/profit-analysis/validation.md` |

---

## Sub-docs (load on demand, not all at once)

| Need | File |
|---|---|
| Deeper log grep patterns | `docs/profit-analysis/log-analysis.md` |
| SL/TP vs actual price moves | `docs/profit-analysis/klines-analysis.md` |
| Parallel agent brief templates | `docs/profit-analysis/subagents.md` |
| Code bug investigation | `docs/profit-analysis/code-analysis.md` |
| Validate before applying change | `docs/profit-analysis/validation.md` |
| Server paths + deploy + thresholds | `docs/profit-analysis/quick-ref.md` |
