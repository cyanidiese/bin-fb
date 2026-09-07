# Code Analysis Protocol

**Load this doc when:** a protection mechanism is not working as designed, a bug is suspected, or this is a periodic full code review session (do this at minimum once every 3 profit sessions).

**Why code analysis is mandatory:** The worst losses in this project were caused by code bugs, not config gaps — the streak key resetting on preset switch, the locked preset not clearing after restart. These are invisible to log analysis and config tuning. Only reading the code finds them.

---

## Files to Review and What to Look For

### `main.py` — Signal pipeline and order gating

Trace the path: incoming kline → signal generation → gate checks → real order placement.

Key questions:
- **Streak key format**: Is `_streak_blocked` keyed on `symbol:side` (correct) or `symbol:preset:side` (bug — resets when preset switches)?
- **Streak save**: Is `_save_streak_state()` called after EVERY loss update, or only in some branches? Find every call site with `grep -n "_save_streak_state" main.py`.
- **Streak load**: Is `_load_streak_state()` called before the first candle is processed? Check startup order.
- **Blocklist check order**: Is `preset_blocklist` checked before `virtual_only`? If virtual_only is checked first, blocklisted presets may still reach the virtual tracker and affect scoring.
- **`global_min_sl_pct` coverage**: Does it apply to all order paths or only some? Check if there's a code path where SL is computed but the floor is not applied.
- **Restart state**: On restart, are open positions reconciled with the exchange, or does the bot start as if there are none?
- **`_update_loss_streak` call sites**: Is it called for both SL hits AND `max_losing_candles` exits AND `market_close` exits? Each of these is a real loss and should count toward the streak.

### `bot/virtual_tracker.py` — Preset selection and efficiency scoring

- **Score key**: Is the efficiency score keyed on `symbol:side` or `symbol:preset:side`? This was changed — verify it is consistent with how `main.py` reads it.
- **Tier transition**: When a symbol has fewer trades than `min_trades`, it uses the seeded backtest score (Tier 0). When it reaches `min_trades`, it switches to the live `recent_trades` window (Tier 1). Is this transition smooth, or does a big backtest score suddenly get replaced by a small live window score, causing wrong preset selection?
- **Window size**: What is `window_size` for `recent_trades`? Is it large enough to be meaningful (at least 10 trades) but small enough to respond quickly to a regime change?
- **Score update timing**: Is the virtual tracker score updated before or after the preset selection for the next candle? If after, the selection is always one candle stale.

### `bot/virtual_order_simulator.py` — Virtual P&L simulation

- **SL/TP logic parity**: Does the simulator use the same SL/TP calculation as the real order executor? Any divergence here creates a backtest-live gap where the virtual tracker selects presets that perform well in simulation but poorly in reality.
- **`global_min_sl_pct` in simulator**: Is the SL floor applied to virtual positions? If not, virtual scores are computed on tighter SLs than real orders use — the simulator sees fewer SL hits than reality.
- **Leverage**: Does the simulator use the correct leverage when computing virtual P&L? If it uses 1x and reality uses 5x, the virtual scores are 5x smaller than real outcomes — affecting which preset wins the efficiency competition.
- **Partial takes**: If a preset uses `partial_take_pct`, does the simulator correctly simulate a partial close at the first TP level and a trailing stop on the remainder?

### `bot/order_executor.py` — Real order placement

- **Balance source**: Where is the balance fetched for position sizing? Is it `account.balance` (real) or `virtual_tracker.balance` (virtual ~$2,875)? This is the known P3 issue.
- **`max_trade_pct` base**: Is the percentage applied to real or virtual balance?
- **Rejection handling**: If the exchange rejects an order (insufficient margin, min notional), is the state correctly rolled back? Or does the bot think an order was placed when it wasn't?
- **Rounding**: Are quantity and price rounded to exchange precision before submission? Off-by-one rounding is a silent failure source.

### `config/presets.py` — Protection coverage

Run this once to get a full coverage map:
```bash
python3 -c "
from config.presets import PRESETS
for name, p in PRESETS.items():
    streak = p.get('loss_streak_max', 'MISSING')
    dup = p.get('duplicate_skip_candles', 'MISSING')
    minsl = p.get('min_sl_pct', '-')
    print(f'{name:40} streak={streak:10} dup={dup:10} min_sl={minsl}')
"
```

Flag any preset where `loss_streak_max` is MISSING — these are unprotected.
The 6 known unprotected presets (as of 2026-06-10): `db_layer_0`, `db_layer_1`, `db_layer_3`, `sl_adjust_rr_tp95`, `trail_15_from_30_tp95`, `hl_buy_trail15`.

### `config/risk_config.py` and `bot/risk_manager.py`

- **Hot-reload timing**: Is the risk config re-read on every trade decision or cached? If cached, what is the TTL? (Known: 5s TTL — verify this is respected)
- **`preset_blocklist` check location**: Is it checked in `risk_manager.py` or `main.py`? Is it checked for every real order, or only at startup?
- **`per_symbol_settings.min_sl_pct` precedence**: When both `global_min_sl_pct` and `per_symbol_settings.SYMBOL.min_sl_pct` are set, which takes priority? The higher one should win — verify.

---

## Code Review Depth Rule

Do not do a surface read. When you open a function that touches order placement or gate logic, read the **entire function**, not just the line you're searching for. Off-by-one conditions, wrong dict key types, and silently swallowed exceptions have all caused real losses.

When you find a bug:
1. Confirm it is reachable (not dead code, not already fixed in a recent commit)
2. Find a specific real trade in `/tmp/bot_latest.log` where it manifested
3. Estimate USDT cost since the bug was introduced (approximate from log dates)
4. Scope the minimal fix — change the fewest lines needed to fix the specific bug
5. Check if fixing it could break anything else (especially state keys and dict lookups)

---

## Known Code Bugs (as of 2026-06-10)

### P1 — Streak persistence not writing
**Symptom:** `/opt/bot/data/streak_state_test.json` does not exist on server.  
**Hypothesis A:** `_save_streak_state()` is not called on all loss paths (e.g., `max_losing_candles` exit or `market_close` exit may not call it).  
**Hypothesis B:** The file path is built incorrectly at startup (wrong `current_mode` value).  
**Verification:** `grep -n "_save_streak_state\|_update_loss_streak\|streak_state_path" main.py`  
**Fix scope:** Add `_save_streak_state()` call to every branch that updates `_loss_streak`. Verify `_streak_state_path` is constructed before first use.

### P3 — Sizing uses virtual balance
**Symptom:** Orders sized at ~69% of correct size.  
**Verification:** `grep -n "balance" bot/order_executor.py | grep -i "virtual\|tracker\|rank"`  
**Fix scope:** Replace virtual balance reference with real account balance fetch. **Do not implement without user approval** — this changes position sizes on a live account.

### P6 — Code audit bugs from 2026-05-20
**Location:** Memory → `project_audit_bugs.md`  
**Status:** None fixed as of 2026-06-10.  
**Action:** Read that file before making any non-trivial code change. The 2 critical bugs may interact with changes being made.
