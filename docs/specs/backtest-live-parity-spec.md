# Backtest–Live Parity Spec (Path B: Lift Live to Backtest Potential)

**Goal:** Converge live trading performance toward backtest predictions by doing two things simultaneously:
1. **Remove arbitrary live-side restrictions** that block profitable signals the backtest shows are viable
2. **Add real-world constraints to the backtest** (slippage, SL widening) so its projections are honest — not inflated

The guiding principle: **the backtest shows what a preset _can_ do. The live bot should be allowed to do the same thing. Where live will always differ from backtest (slippage, fees, execution lag), the backtest should model those differences honestly.**

What we are NOT doing:
- Importing live config restrictions into the backtest to make the numbers agree by making both worse
- Removing safety controls (hard stop, virtual_only_floor, can_open_sync)
- Over-fitting backtest to match a specific live session

---

## The Two Convergence Directions

```
Backtest (honest, no pink glasses)         Live trading (fewer restrictions)
         │                                          │
  + slippage model                         - arbitrary profit caps
  + SL widening behavior                   - disabled symbols restored
  + fee model (already done)               - per_symbol caps audited/removed
         │                                          │
         └──────────────── converge ────────────────┘
```

Everything that flows INTO the backtest makes it more realistic.
Everything that flows OUT of live config makes live more free to act on valid signals.
The VirtualOrderSimulator (efficiency scoring engine) then accurately reflects both.

---

## Section 1: Change Inventory

### Gap 1 — Arbitrary `per_symbol_settings` caps blocking live signals (CRITICAL)

**Problem:** `risk_config.json` → `per_symbol_settings` contains caps that were added experimentally and now permanently suppress profitable signals. The backtester has no such restrictions, so it promises returns the live bot can never achieve. The divergence is self-inflicted.

**Confirmed cases from decision log analysis:**
- `INJUSDT.max_profit_pct: 5` — blocks 100% of INJUSDT signals (all 289 logged as `skip_max_profit_pct`). The best INJUSDT backtest presets achieve TP distances of 17–22%, making every single signal unreachable at a 5% cap. The cap was set in a previous session to "be safe" but it achieves the opposite: zero INJUSDT trades, while the backtest promises +$117 on `r6_arm15_rr4` alone.

**Fix:** Audit every key in `per_symbol_settings` for each symbol. Categorize each override as:
- **Structural tuning** (e.g., wider SL floor for a volatile symbol, lower min_precision for a thin-market symbol) → keep, these reflect intentional behavior
- **Arbitrary cap** (e.g., max_profit_pct that's tighter than the preset's own cap, manually lowered leverage) → remove unless there is a documented reason it improved live results

**Action for INJUSDT:**
Remove `max_profit_pct` from `per_symbol_settings.INJUSDT` entirely (or set it to 0 = disabled). The preset's own `max_profit_pct` setting is the correct gate; a global override should only exist when there is live evidence that INJUSDT specifically benefits from one.

**Files:** `risk_config.json` (config only, no code change)

**Note:** `per_symbol_settings` is a valid mechanism — it stays in both the live path and the VirtualOrderSimulator. The problem is not the mechanism but specific values inside it.

---

### Gap 2 — Disabled/zero-weight symbols that were previously profitable (HIGH impact)

**Problem:** Three symbols are disabled (WLDUSDT, INJUSDT, 1000SHIBUSDT) and four have weight=0 (ETHFIUSDT, REZUSDT, JUPUSDT, APTUSDT) in `symbol_registry.json`. Some of these were disabled during a session for operational reasons; others have weight=0 because their backtest performance wasn't reviewed after the registry was updated. The decision log shows WLDUSDT was one of the most prolific real-order symbols before it was disabled.

**Fix:** Re-evaluate each disabled/zero-weight symbol using its backtest results:
- WLDUSDT: `placed` entries in decision log on Jun 2 morning. Backtest: `partial_70` +$235. Disabled mid-session, reason unclear. **Candidate for re-enabling.**
- ETHFIUSDT: weight=0. Last decision log entry Jun 1. **Review backtest — re-enable if profitable.**
- INJUSDT: disabled + weight=0. After Gap 1 fix, re-enable with weight=1.
- 1000SHIBUSDT, REZUSDT, JUPUSDT, APTUSDT: check backtest results. If no profitable presets, leave disabled. If profitable, re-enable.

**Evaluation criteria:** Re-enable a symbol if its backtest shows at least one preset with `win_rate ≥ 35%` and `total_profit_pct > 0` over the current backtest window.

**Files:** `symbol_registry.json` (config only)

**Note:** This must be done before running backtests under the new config, so the backtest reflects the actual active symbol set.

---

### Gap 3 — `global_min_sl_pct` absent from backtester (MEDIUM impact)

**Problem:** The live bot (`main.py` lines 520–543) widens any SL tighter than `global_min_sl_pct` (currently 0.3%) before placing an order. The backtester at lines 341–342 only checks the preset's own `min_sl_pct` and **skips** (not widens) signals where `sl_dist_pct < settings.min_sl_pct`. Two divergences:

1. The global floor is absent from the backtester entirely — tight-SL signals that would be widened live are not widened in backtest, producing different SL placement and trade outcomes.
2. Skip vs widen: for signals below the preset's own floor, backtester skips; live widens. Live takes trades the backtester counted as invalid.

**This is a real-world behavioral difference, not an arbitrary restriction.** The global SL floor exists to prevent stop hunts on very tight stops. Adding it to the backtester makes backtest results honest about the actual SL that will be used — it does not reduce live profitability.

**Fix in `bot/backtester.py`:** Replace the skip at lines 341–342 with the effective-floor widen matching live behavior:
```python
# Before:
if settings.min_sl_pct > 0 and sl_dist_pct < settings.min_sl_pct:
    continue

# After:
_effective_min_sl = max(settings.min_sl_pct if settings.min_sl_pct > 0 else 0.0, _global_min_sl_pct)
if _effective_min_sl > 0 and sl_dist_pct < _effective_min_sl:
    if side == 'BUY':
        sl = entry_price * (1.0 - _effective_min_sl / 100.0)
    else:
        sl = entry_price * (1.0 + _effective_min_sl / 1.5 / 100.0)
    sl_dist_pct = _effective_min_sl
    # Re-derive profit_dist after SL change if sl_adjust_to_rr is set
```
Also: update the `sl_adjust_to_rr` check (currently uses `settings.min_sl_pct`) to use `_effective_min_sl` instead.

`_global_min_sl_pct` is read from `_cfg` (which is already loaded in `_run_preset`). Before the `if self._risk_config_path:` block, initialize `_global_min_sl_pct = 0.0` as a safe default.

**Fix in `bot/virtual_order_simulator.py`:** The same gap exists in `_try_open`. At lines 294–299, replace the preset-only SL widen with the effective floor (identical formula). `global_min_sl_pct` must be read from `_risk_cfg` — see Gap 4 for the load-order fix.

---

### Gap 4 — `load_risk_config()` called too late in VirtualOrderSimulator (MEDIUM impact)

**Problem:** `VirtualOrderSimulator._try_open` calls `load_risk_config()` at line 361, which is AFTER all signal filtering. So `global_min_sl_pct` and any per-symbol config values are unavailable during the SL floor check (lines 294–299). The virtual order is evaluated with a stale or missing config.

**This is a correctness bug, not a philosophy question.** The VirtualOrderSimulator should behave exactly like `_try_place_order` for filters that also exist in the real path. If they diverge, the efficiency scores are tracking a different signal population than real orders — making preset rankings meaningless.

**Fix:** Move `_risk_cfg = load_risk_config()` from line 361 to before the `try:` block at the top of `_try_open`. All existing uses of `_risk_cfg` below line 361 remain valid. The `global_min_sl_pct` widen block in Gap 3's VirtualOrderSimulator fix then has access to the correct value.

**Do NOT add `per_symbol_settings` to `_try_open` until Gap 1 is resolved.** Once per_symbol_settings is cleaned of arbitrary caps, it is safe to apply in `_try_open` so that virtual and real orders evaluate the same signal population.

**Files:** `bot/virtual_order_simulator.py`

---

### Gap 5 — Entry slippage absent from backtester (MEDIUM impact)

**Problem:** Backtester enters at exact candle close price. Live market orders fill at the exchange's best available price at the moment of execution — milliseconds to seconds after the candle close event. Measured slippage on liquid pairs: 0.02–0.10%. For presets with `min_profit_loss_ratio ≈ 1.5` and SLs at 0.3%, a 0.05% adverse fill consumes 17% of the SL budget before the trade begins.

**This is a real-world constraint that belongs in the backtest.** It does not change live behavior — it makes backtest projections honest about what live will actually earn.

**Fix in `bot/backtester.py`:** After the `entry_price` is set from candle close, apply optional adverse slippage before any SL/TP calculations:
```python
_slippage_pct = _cfg.get("backtest_entry_slippage_pct", 0.0) if _cfg else 0.0
if _slippage_pct > 0:
    if side == 'BUY':
        entry_price *= (1.0 + _slippage_pct / 100.0)
    else:
        entry_price *= (1.0 - _slippage_pct / 100.0)
```
This must come before `raw_tp`, `sl`, and all distance calculations so profit_dist_pct and sl_dist_pct use the slippage-adjusted entry.

**New config field:** `backtest_entry_slippage_pct: 0.0` (default preserves current behavior). Set to `0.05` in production to model typical liquid-pair fill. Do not set above `0.10` without measuring actual fills first.

**Note:** Assign `_cfg: dict = {}` before the risk_config guard block so `_cfg.get(...)` works when no path is given.

---

### Gap 6 — Leverage units mismatch in VirtualTracker seed scores (HIGH impact)

**Problem:** `VirtualTracker.seed_from_backtest` converts `total_profit_pct` to USD using:
```python
seeded = preset_data["total_profit_pct"] / 100.0 * balance_start  # balance_start = 1000.0
```
This produces seeded values in unleveraged $1,000 terms. Live accumulation runs at 5× leverage on a $4,300 account. After the Tier-0 period ends (enough live trades to cross `min_trades_for_ranking`), the seeded score is eclipsed by live data — but during Tier 0, the bot ranks presets using numbers that are structurally incomparable to live PnL. A preset seeded at $155 is not equivalent to one that earned $155 live.

**Fix in `bot/virtual_tracker.py`:**
```python
_leverage_factor = float(load_risk_config().get("backtest_seed_leverage_factor", 1.0))
seeded = preset_data["total_profit_pct"] / 100.0 * balance_start * _leverage_factor
```
Apply the same factor to the fallback trade-list path.

**New config field:** `backtest_seed_leverage_factor: 1.0` (default = no change). Set to the actual mean leverage used (5.0 for current config) after this code is deployed.

**Important sequence:** Set `backtest_seed_leverage_factor` in risk_config only AFTER code is deployed and bot is restarted with a fresh backtest run. Changing the factor without code changes achieves nothing.

---

### Gap 7 — `duplicate_skip_candles` / `loss_streak_max` absent from backtester (LOW impact, by design)

**Problem:** The live bot applies `duplicate_skip_candles` and `loss_streak_max` per preset. These skip re-entry after a recent SL hit or streak of losses. The backtester does implement these (confirmed in code review). This gap is already closed.

**No fix required.**

---

### Gap 8 — Analyzer state divergence (LOW impact, structural)

**Problem:** The backtester replays from a fixed kline window (1500 candles = ~26 days). The live analyzer builds swing structure incrementally from WebSocket candle closes and has seen the full session history. The same candle data produces slightly different swing structures depending on whether it was processed in a fresh batch or as an incremental addition.

**This gap cannot be fully closed.** It is structural. Partial mitigation: keep the backtest window aligned with the live analyzer's initial kline load count so they start from the same depth.

**No code fix required.** Consider increasing `backtest_klines` from 1500 to 3000 after higher-priority tasks are stable, for broader regime coverage.

---

## Section 2: Config Changes

### `risk_config.json` (server-side, never committed)

| Field | Change | Reason |
|---|---|---|
| `per_symbol_settings.INJUSDT.max_profit_pct` | Remove entirely | Arbitrary cap blocking all INJUSDT signals |
| `per_symbol_settings` (all symbols) | Audit each key — remove caps with no documented live benefit | Path B cleanup |
| `backtest_seed_leverage_factor` | Add as `1.0` initially, set to `5.0` after T5 code deployed | Leverage-normalizes seed scores |
| `backtest_entry_slippage_pct` | Add as `0.0` initially, set to `0.05` after T3 code deployed | Honest fill price in backtest |

### `symbol_registry.json` (server-side)

| Change | Condition |
|---|---|
| Re-enable INJUSDT (weight=1, remove from disabled) | After Gap 1 fix |
| Re-enable WLDUSDT (weight=1, remove from disabled) | After reviewing backtest — if profitable |
| Re-evaluate ETHFIUSDT weight=0 | Set to 1 if backtest shows profit |
| Review 1000SHIBUSDT, REZUSDT, JUPUSDT, APTUSDT | Enable if backtest shows ≥ 35% win rate + positive PnL |

### `config/risk_config.py` DEFAULT_CONFIG (code)

Add new keys so they appear automatically in any existing config:
```python
"backtest_seed_leverage_factor": 1.0,
"backtest_entry_slippage_pct": 0.0,
```

---

## Section 3: Implementation Plan

Tasks are ordered by: (1) impact, (2) safety, (3) dependency. Each task leaves the bot deployable.

---

### T1 — Audit and clean `per_symbol_settings` in risk_config.json (S, config only)
**What:** Remove `INJUSDT.max_profit_pct: 5`. Audit all other per_symbol_settings entries. Remove any caps that have no documented live-trading justification.  
**How:** Edit `risk_config.json` on server. Stop bot first, edit, restart.  
**Risk:** Low. Removing a cap can only increase signal count — no trades are blocked that were previously taken.  
**Validates as:** INJUSDT signals start appearing in the decision log. `skip_max_profit_pct` count drops to near zero.

---

### T2 — Re-evaluate disabled symbols, re-enable profitable ones (S, config only)
**What:** Review backtest results for WLDUSDT, INJUSDT, ETHFIUSDT. Re-enable in `symbol_registry.json` if their best preset has `win_rate ≥ 35%` and `total_profit_pct > 0`.  
**How:** Read `dashboard/public/backtest_results_*.json` for each symbol. Edit `symbol_registry.json` on server.  
**Risk:** Low. Symbols with positive backtest will now trade. If they underperform live, they can be re-disabled. The efficiency score mechanism will down-rank them over time if they lose.  
**Dependency:** T1 should be done first so INJUSDT's backtest is not distorted by the max_profit_pct cap when you review it.

---

### T3 — Add DEFAULT_CONFIG keys for new backtest fields (S, code)
**What:** Add `backtest_seed_leverage_factor: 1.0` and `backtest_entry_slippage_pct: 0.0` to `DEFAULT_CONFIG` in `config/risk_config.py`.  
**How:** Two-line addition. Both default to current behavior — zero behavioral change.  
**Risk:** None.

---

### T4 — Fix `bot/backtester.py`: SL widening + slippage (M, code)
**What:** Two changes in `_run_preset`:

**Change A — SL widening (Gap 3):**
1. Initialize `_global_min_sl_pct = 0.0` and `_cfg: dict = {}` before the risk_config guard.
2. Inside the guard, read: `_global_min_sl_pct = float(_cfg.get("global_min_sl_pct", 0.0))`
3. Replace the skip at lines 341–342 with the effective-floor widen block.
4. Update the `sl_adjust_to_rr` minimum check to use `_effective_min_sl`.

**Change B — Slippage (Gap 5):**
1. Read: `_slippage_pct = float(_cfg.get("backtest_entry_slippage_pct", 0.0))`
2. After `entry_price` is set from candle close, apply adverse slippage before any SL/TP calculations.

**What NOT to add here:** Do NOT apply `per_symbol_settings` to the backtester. The backtest shows what the preset can achieve without arbitrary live config restrictions. It is the ceiling, not the current floor.

**Risk:** Medium. This changes backtest trade counts and PnL values. A few currently-passing tight-SL trades will now run with a wider SL — some that were winners may become losers if the wider SL changes the RR filter outcome.  
**Validates as:** Run backtest before/after. Expect: `total_profit_pct` changes slightly for presets that had tight-SL trades. No preset should flip dramatically. The trade populations should be stable.

---

### T5 — Fix `bot/virtual_order_simulator.py`: `load_risk_config()` load order + global SL floor (M, code)
**What:**
1. Move `_risk_cfg = load_risk_config()` to before the `try:` block in `_try_open` (currently called after all filters at line 361).
2. In the SL floor block (lines 294–299), replace the preset-only check with the effective floor using `global_min_sl_pct` from `_risk_cfg`.
3. Update `sl_adjust_to_rr` check to use `_effective_min_sl`.

**Do NOT add `per_symbol_settings` application to `_try_open` yet.** Wait until T1 is confirmed working (per_symbol_settings is clean of arbitrary caps). Once clean, it is safe to apply in virtual orders — both real and virtual will then evaluate the same signal population.

**Risk:** Medium. Moving the `load_risk_config()` call earlier changes when config is read. The SL floor change affects virtual trade outcomes, which resets efficiency score accuracy going forward.  
**Validates as:** Virtual order SL floors match real order SL floors in logs.

---

### T6 — Apply `per_symbol_settings` to VirtualOrderSimulator (S, code, after T1+T5)
**What:** Inside `_try_open`, after the preset overrides are applied to `preset_settings`, apply per_symbol_settings from `_risk_cfg`:
```python
_per_sym = _risk_cfg.get("per_symbol_settings", {}).get(symbol, {})
if _per_sym:
    preset_settings = dataclasses.replace(preset_settings, **{
        k: v for k, v in _per_sym.items() if hasattr(preset_settings, k)
    })
```
**Dependency:** T1 must be complete first. If per_symbol_settings still has blocking caps when this is deployed, virtual orders will be blocked for those symbols — and efficiency scores stop accumulating for them.  
**Risk:** Low after T1. The virtual order population will now match the real order population exactly.  
**Validates as:** INJUSDT virtual orders appear in the simulator output. Skip reasons in virtual path match skip reasons in real path for the same signals.

---

### T7 — Fix `bot/virtual_tracker.py`: leverage-normalized seed scores (S, code)
**What:** In `seed_from_backtest`, multiply `seeded` by `_leverage_factor` read from risk_config (default 1.0).  
**Risk:** Low. Default of 1.0 means no behavioral change until T8 config change is applied.  
**Validates as:** Code review only. Run test: seed a symbol with known backtest data, confirm seeded value = `total_profit_pct / 100 * 1000 * leverage_factor`.

---

### T8 — Set leverage factor and slippage in risk_config.json (S, config)
**What:** After T7 (leverage) and T4 (slippage) code are deployed:
```json
"backtest_seed_leverage_factor": 5.0,
"backtest_entry_slippage_pct": 0.05
```
Trigger a full backtest re-run for all symbols. Restart bot so `seed_from_backtest` picks up the new factor.  
**Sequence:** Set leverage factor first, restart, confirm seeded values are 5× larger. Then set slippage, re-run backtest, confirm slight degradation in `total_profit_pct` (honest fill cost).  
**Risk:** Low for slippage (backtest-only change). Medium for leverage factor: seeded scores jump 5×, which may trigger Tier-0 → Tier-1 transitions differently on next restart. Monitor which preset gets selected as `best_preset()` for each symbol after restart.

---

### T9 — Evaluate `backtest_klines: 3000` (S, config, deferred)
After T4–T8 are stable, consider doubling the backtest window for more regime coverage. Check runtime: if a full sweep exceeds 120 seconds, stay at 1500.

---

## Section 4: What the Backtest Should and Should Not Model

| Factor | In backtest? | Reason |
|---|---|---|
| `global_min_sl_pct` SL widening | **Yes (after T4)** | Real behavior difference — live always widens |
| Entry slippage 0.05% | **Yes (after T4+T8)** | Real-world fill cost |
| Fee model | **Yes (already)** | Real cost |
| `per_symbol_settings` caps | **No** | Arbitrary config choices; backtest shows preset ceiling |
| `per_symbol_settings` structural tuning | **No** | Backtest uses preset-native settings only |
| `duplicate_skip_candles` / `loss_streak_max` | **Yes (already)** | Correctly implemented in both |
| `can_open_sync` / hard_stop | **No** | Operational safety controls; irrelevant to preset evaluation |
| `virtual_only_floor` | **No** | Capital allocation control, not signal quality |
| BGF fractions / max_trade_pct | **No** | Sizing, not signal selection |

The backtest is a **preset quality oracle**, not a live session simulator. It answers: "How good is this preset at finding and trading valid signals?" — not "How much would the bot have earned in this exact configuration?"

---

## Section 5: What NOT to Change

- **`virtual_only_floor`** — intentional safety mechanism for losing symbols
- **`can_open_sync` / hard_stop / daily loss limit** — operational safety, not parity bugs
- **`duplicate_skip_candles` / `loss_streak_max` / `zone_sl_max`** — already correct in both paths
- **Leverage level** — don't change leverage to fix the seed units mismatch; fix the seed calculation instead (T7)
- **`FakeOrder` SL/TP check logic** — reasonable proxy for live fills; not a material gap
- **BGF allocation, balance tiers, weight allocation** — sizing mechanisms; separate concern
- **WebSocket reconnect, mode persistence, order state machine** — unrelated to parity
- **The `per_symbol_settings` mechanism itself** — valid and useful; clean the values inside it, not the mechanism

---

## Section 6: Validation Plan

### After T1+T2 (config cleanup):
- Decision log: `skip_max_profit_pct` for INJUSDT drops to near zero within 1–2 candle cycles
- INJUSDT appears in `best_preset()` output if its efficiency score is non-zero
- WLDUSDT (if re-enabled) produces `placed` entries again

### After T4 (backtester fix):
- Run `python backtest.py` before and after
- SOLUSDT `r5_sl_adj_cooldown`: expect `total_profit_pct` decreases slightly (tight-SL trades now run with 0.3% SL instead of tighter)
- No preset should flip from profitable to unprofitable — if one does, it was marginal and should be flagged, not hidden
- Slippage still zero at this stage (T8 not applied yet)

### After T5+T6 (VirtualOrderSimulator fix):
- INJUSDT virtual order log: signals now appear (after T1) or are blocked (before T1, confirming T6 should wait for T1)
- Virtual skip reasons match real order skip reasons for the same candles

### After T7+T8 (leverage + slippage in config):
- `seeded_winning_usdt` in preset efficiency file: all values ~5× larger than before T8
- Relative preset ordering: unchanged (the factor scales all equally)
- Backtest `total_profit_pct` after slippage: decreases 0.3–1.5% absolute per symbol. Acceptable. If > 5%, reduce `backtest_entry_slippage_pct` to 0.02.

### End-to-end (72h after all tasks):
- Compare INJUSDT decision log before/after: placed entries should appear regularly
- Compare total real orders placed per 24h: should increase from baseline (more active symbols, no INJUSDT cap)
- Monitor VirtualTracker efficiency file: INJUSDT presets accumulate live scores, previously-dominant presets for other symbols remain ranked correctly
- Check that `best_preset()` selections are stable across restarts (Tier-0 seed → Tier-1 live transition happens cleanly)
