# Solution Validation and Anti-Patterns

**Load this doc when:** about to apply a config or code change, or when you notice the same setting has been changed multiple times.

---

## The Double-Check Rule

Every proposed change must be validated from two independent angles before applying:

**Angle 1 — Log evidence:** Quote specific log lines (timestamp, symbol, preset, PnL) that show the problem. If you cannot point to a specific trade in the log, the problem is not confirmed — it is hypothetical.

**Angle 2 — Root cause confirmation:** Verify the fix addresses the actual cause, not just the symptom. A config change that blocks a losing symbol does not fix the code bug that caused the losses. Treating symptoms without fixing root cause guarantees the problem recurs in a different form.

Both angles must pass. One is not enough.

---

## Validation Checklist

Before applying any change, check all boxes:

- [ ] ≥4 real trades show the problem, OR ≥1 trade with >$15 single-trade loss
- [ ] Specific log timestamps and USDT amounts quoted — not "several losses"
- [ ] Root cause identified: code bug / config gap / bad preset for this symbol / bad symbol weight
- [ ] Net USDT estimate: prevented losses minus blocked wins, positive over 7-day window
- [ ] Signal suppression impact: daily trade count reduction < 30% (> 50% needs very strong justification)
- [ ] Does not contradict a change made in the last 7 days (see anti-oscillation rule below)
- [ ] Hot-reload vs deploy decision made and stated explicitly
- [ ] If order execution path is affected: Trader agent consulted

### Minimum trade sample by change type

| Change type | Minimum sample |
|---|---|
| Global parameter (global_min_sl_pct, min_profit_factor) | 20 real trades across ≥3 symbols affected |
| Symbol weight reduction | 8 real trades on that symbol |
| Symbol weight increase | 12 real trades, win rate > 35% |
| Preset added to blocklist | 4 trades with 0 wins, OR 1 trade > $15 loss |
| New preset protection (streak, dup_skip) | No minimum — protection is always safe to add |
| Code change to gate logic | Full code review of affected function required |

---

## Anti-Patterns — Explicitly Prohibited

These behaviors have caused or worsened losses in this project.

### 1. Config oscillation

**Pattern:** Change a value → doesn't immediately help → change it back → repeat next session.

**Rule:** If a setting was changed in the last 7 days, do not reverse it without:
- At least 7 new real trades placed under the new value
- Explicit log evidence the new value is performing worse, not "I think it might be"

If the same parameter has been changed 3 times in 3 sessions: stop all config tuning. Run a full code analysis and klines retrospective instead. The signal is that config changes are not solving the real problem.

### 2. Silencing multiple symbols in one session

**Pattern:** THETA is losing, DOGE is losing, REZ is losing → set all three weights to 0.

**Why harmful:** Removing 30–40% of the weight pool means the remaining symbols cannot fill the gap if their signals are weak. Total daily trades drop, and the bot becomes progressively more idle — which looks like "fewer losses" but is actually fewer trades overall, including wins.

**Rule:** Reduce at most 2 symbol weights significantly in one session. If more than 2 symbols are clearly losing, the problem is a preset or code issue, not a symbol issue — fix the preset.

### 3. Locking a symbol to a preset without live evidence

**Pattern:** Backtest shows preset X is best for symbol Y → lock Y to X in `locked_presets`.

**Why harmful:** Live market regime may differ from backtest. A locked preset cannot be overridden by the virtual tracker even when it is actively losing — exactly what happened with THETA + pre_confirm_trail15 (-$34.97 in 24 hours).

**Rule:** Only lock a symbol to a preset if it has ≥8 live trades with win rate > 40% on that symbol. Prefer adjusting weights and per_symbol_settings over locking.

### 4. Compensating for a code bug with tighter config

**Pattern:** Streak state is not persisting because the file path is wrong → response is "raise loss_streak_max from 2 to 3 so it takes more losses to trigger."

**Why harmful:** Masks the bug permanently, creates wrong behavior when the state IS loaded, and increases false blocks.

**Rule:** If a protection mechanism is not working as designed, fix the code. Do not paper over broken code with config changes.

### 5. Acting on fewer than 48 hours of live data

**Pattern:** Bot restarted 3 hours ago, 2 trades placed, one lost → "we should adjust X."

**Rule:** Minimum 48 hours and 6 real trades for config changes. Exception: a single trade with loss > $15 on a preset with no protection — that warrants immediate blocklist addition.

### 6. Treating "no trade" as "no loss"

**Pattern:** Reducing signal frequency or raising filters until the bot barely trades → "losses are down!"

**Why harmful:** Wins are also down. A bot that doesn't trade doesn't make money. The goal is not to minimize losses — it is to maximize net USDT. Always estimate both sides: how many losses would be prevented AND how many wins would be blocked.

---

## Autonomy Rules

### Apply without asking (hot-reload changes only, clear evidence required)

- Symbol weight reduced by ≤50% when: net PnL < -$20 with ≥8 real trades
- Preset added to `preset_blocklist` when: ≥4 trades, 0 wins; OR 1 trade > $15 loss
- `global_min_sl_pct` raised by ≤0.2 percentage points when: log evidence shows sub-floor SLs consistently hitting
- `per_symbol_settings.SYMBOL.min_sl_pct` added when: SL retrospective shows structural narrowness for this symbol

Always state: what was changed, what evidence supports it, what the estimated impact is.

### Always ask before doing

- Any code change to `main.py`, `bot/*.py`, or `config/presets.py`
- Any deploy (stops the bot — real money implications)
- Setting a symbol weight to 0
- Locking a symbol to a preset
- Changing `min_profit_factor`
- Any change to order sizing or leverage
- Reverting a change made in the last 7 days
