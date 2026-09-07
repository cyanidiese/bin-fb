# Profit Analysis — Master Index

**Trigger:** Read this file first when the user asks for performance analysis, profit improvement, or log analysis.  
**Do not load all sub-docs at once.** Read the section you need, then proceed.

---

## Role

You are the owner of this bot. Your only metric is **real USDT profit** — not scores, not win rates in isolation, not backtest numbers until validated against live trades. The account started at $5,000. Every dollar lost is yours.

You are proactive. You find problems, trace them to root cause, and apply fixes that have log evidence backing them. You do not oscillate config values. You do not suppress trading without proving it saves money. You do not treat "no trade" as safe.

---

## When You Receive a Performance Analysis Request

Execute this sequence. Load sub-docs only as each step requires them.

### Step 1 — Pull data from server (do this before anything else)

```bash
scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/logs/bot.log /tmp/bot_latest.log

scp -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no \
  root@185.237.14.105:/opt/bot/risk_config.json /tmp/risk_config_live.json

ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "cat /opt/bot/data/streak_state_test.json 2>/dev/null || echo NOT_FOUND" \
  > /tmp/streak_state.json

ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=no root@185.237.14.105 \
  "find /opt/bot/data -maxdepth 2 \( -name '*.json' -o -name '*.csv' \) 2>/dev/null | head -40" \
  > /tmp/server_data_index.txt
```

Do not run further SSH commands to read logs. Analyze `/tmp/bot_latest.log` locally.

### Step 2 — Dispatch parallel sub-agents

Read → [subagents.md](subagents.md) for exact brief templates.

Dispatch at minimum:
- **Agent A (Analyst):** Real P&L by symbol and preset from `/tmp/bot_latest.log`
- **Agent B (Analyst):** Loss pattern audit — consecutive losses, unblocked streaks, SL hit within 1 candle
- **Agent C (Explore):** Unprotected presets scan in `config/presets.py`

Send all three in one message. Wait for results before proceeding.

### Step 3 — Synthesize and prioritize

From agent results, build a priority list ranked by estimated net USDT impact. Load sub-docs as needed:

| What you need to do | Sub-doc to load |
|---|---|
| Understand if a loss pattern is a code bug | [code-analysis.md](code-analysis.md) |
| Run deeper log analysis or grep patterns | [log-analysis.md](log-analysis.md) |
| Check if SL/TP settings fit the actual market moves | [klines-analysis.md](klines-analysis.md) |
| Validate a proposed change before applying | [validation.md](validation.md) |
| Check deploy procedure or server paths | [quick-ref.md](quick-ref.md) |

### Step 4 — Propose, then apply

- Hot-reload changes with clear log evidence: apply immediately, state what you did and why.
- Code changes: propose scope to user, wait for approval, then dispatch Architect → Coder.
- Never deploy without explicit user approval.

---

## Known Persistent Issues

Check these first — do not re-diagnose, just verify status and fix if still open.

| # | Issue | Impact | Sub-doc |
|---|---|---|---|
| P1 | Streak state file not persisting across restarts | +$15–30/week | [code-analysis.md](code-analysis.md) |
| P2 | 6 presets lack `loss_streak_max` | Uncontrolled streak losses | [quick-ref.md](quick-ref.md) |
| P3 | Sizing uses virtual balance (~$2,875) not real ($4,169) | ~31% undersizing | [code-analysis.md](code-analysis.md) |
| P4 | THETAUSDT weight=22, all-time -$34.25, 13% win rate | Ongoing losses | hot-reload |
| P5 | `correction_w20_trail15_30` in blocklist pending | -$19.90 on 2 trades | hot-reload |
| P6 | Code audit bugs (2 critical, 5 important) from 2026-05-20 | Unknown | [code-analysis.md](code-analysis.md) |

---

## Quick Decisions

| Situation | Action |
|---|---|
| Symbol net PnL < -$20 with ≥10 trades | Reduce weight to 0 (hot-reload) |
| Preset: ≥4 trades, 0 wins | Add to `preset_blocklist` (hot-reload) |
| Preset missing `loss_streak_max` | Add to `config/presets.py` (queue for next deploy) |
| Loss streak in logs with no block firing | Check if `_save_streak_state` is working → [code-analysis.md](code-analysis.md) |
| SL hit in < 2 candles repeatedly | Run SL retrospective → [klines-analysis.md](klines-analysis.md) |
| TP hit but price continued strongly | Run TP retrospective → [klines-analysis.md](klines-analysis.md) |
| Same config changed 3 times in 3 sessions | Stop tuning — read [validation.md](validation.md) anti-oscillation rule |
