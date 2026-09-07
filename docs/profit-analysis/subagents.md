# Sub-Agent Strategy

**Load this doc when:** dispatching agents for log analysis, code diagnosis, or parallel investigation tasks.

---

## Core Principle

A vague brief produces vague output. Every agent brief must include:
1. Exact file path (local `/tmp/` path, not SSH commands)
2. The single specific question to answer
3. The expected output format (table, list, number)
4. What to exclude (e.g. "ignore lines containing 'virtual'")

After every agent returns: **evaluate output quality.** Did it quote actual numbers from the files? Did it answer the specific question? If the output is shallow or drifts to general observations, the brief was too vague — improve it and re-dispatch.

---

## Parallel Dispatch Pattern

Send independent agents in one message. Always do this for a full analysis session:

```
PARALLEL DISPATCH — send all three in one message:

Agent 1 (Analyst):
  File: /tmp/bot_latest.log
  Task: For every symbol, compute total real closed PnL, trade count, win count, loss count,
        avg win USDT, avg loss USDT. Separate BUY and SELL.
  Exclude: lines containing "virtual" or "VirtualOrder" or "sim"
  Output: table sorted by net PnL ascending (worst first)
  Time window: [DATE] to now

Agent 2 (Analyst):
  File: /tmp/bot_latest.log
  Task: List every real closed order with pnl < -$5, sorted by loss size descending.
  For each: timestamp, symbol, preset, side, PnL.
  Then: group by symbol+preset — how many consecutive losses occurred on same symbol:side?
  Flag: any group with 3+ consecutive same-side losses.
  Exclude: virtual, sim

Agent 3 (Explore):
  File: config/presets.py
  Task: List every preset dictionary and whether it contains:
        loss_streak_max, duplicate_skip_candles, min_sl_pct.
  Output: table — Preset | loss_streak_max | dup_skip | min_sl_pct
  Mark absent fields as MISSING.
```

After receiving results, synthesize, then dispatch follow-up if needed:

```
Agent 4 (Analyst) — based on Agent 2 results:
  File: /tmp/bot_latest.log
  Task: For presets [LIST FROM AGENT 2 RESULTS], find every log line showing a streak block,
        dup-skip, or profit_factor block on those same symbol:preset pairs.
  Were any of those protections actually firing, or were they all passing through?
  Time window: same as above
```

---

## Agent Type Selection

| Task | Agent type | Key instruction |
|---|---|---|
| P&L from logs | Analyst | Give local file path, specific time window, table format |
| Protection effectiveness | Analyst | Ask to count block events vs trades that passed |
| Find unprotected code paths | Architect | Give exact file + line range hypothesis, ask to confirm or refute |
| Find preset gaps | Explore | Ask for list format, mark missing fields explicitly |
| Implement a scoped fix | Coder | Give exact file, line number, what to change — never "figure out how to fix X" |
| Exchange/order constraints | Trader | Ask about specific order type or sizing formula |

---

## Brief Templates

### Full P&L analysis
```
Read /tmp/bot_latest.log. Compute real USDT P&L for every symbol since [DATE].
Columns: Symbol | Net PnL | Trades | Wins | Losses | Win% | Avg Win | Avg Loss | Best Preset | Worst Preset.
Real orders only — ignore lines containing "virtual", "VirtualOrder", or "sim".
Sort by Net PnL ascending.
```

### Loss pattern audit
```
Read /tmp/bot_latest.log. Find every real closed order with PnL < -$5 since [DATE].
For each: timestamp, symbol, preset, side, PnL.
Group by symbol:side — flag any group with 3+ consecutive losses.
Also flag: any trade placed within 2 candles of a previous loss on the same symbol:side.
Real orders only.
```

### Streak and protection audit
```
Read /tmp/bot_latest.log since [DATE].
Count: how many times did each of these fire per symbol?
  - loss streak block ("loss streak" in line)
  - dup-skip block ("dup" or "duplicate" in line)
  - profit_factor block ("profit_factor" or "pf=" in line with "below" or "blocked")
  - SL floored ("SL floored" in line)
Also: for any symbol that had 2+ consecutive real losses, was a streak block logged afterward?
If losses occurred with no streak block, report the symbol and preset.
```

### Code path trace (Architect)
```
In [FILE], trace the complete path from [TRIGGER EVENT] to [OUTCOME].
Specifically answer:
  1. [EXACT QUESTION 1]
  2. [EXACT QUESTION 2]
Read the full function bodies — do not summarize from partial context.
Report: exact dict key formats, exact call sites with line numbers, any path where [PROTECTION] could be skipped.
```

### Post-deploy verification
```
Read /tmp/bot_latest.log. The bot was redeployed at [TIME].
Find all log lines after [TIME] and confirm:
  1. "Streak state loaded" appears (or report its absence)
  2. All expected symbols appear as connected
  3. First real order placed after restart: which symbol, preset, side, PnL
  4. Any ERROR or WARNING in the first 30 minutes after restart
```

---

## Improving Agent Effectiveness

If an agent returns shallow output (vague, no numbers, drifts to general advice):
- The brief was too open-ended. Add: exact file path, exact output format, exact exclusion criteria.
- Split the task. One agent answering one question beats one agent answering five poorly.
- Provide the expected answer shape: "output as a table with columns X, Y, Z" forces structure.

If Architect cannot find a function:
- Provide the line number directly. `grep -n "function_name" main.py` first, then give Architect the line.
- Do not ask Architect to search — give it the coordinates and ask it to read and analyze.

If Analyst returns estimates instead of counts:
- The log format was ambiguous. Add a sample log line to the brief: "Lines look like: `[2026-06-09 14:30:00] Order placed: THETAUSDT SELL qty=3000 preset=pre_confirm_trail15`"
