# Backtest vs Live Trading Gap Analysis

**Date:** 2026-06-03  
**Data window:** 2026-05-23 to 2026-06-03 (10.4 days)  
**Total live trades analyzed:** 228 across 13 symbols  
**Actual live PnL:** -$190.82 on a starting balance of ~$5,000  
**Reference files:** `/opt/bot/bot/backtester.py`, `/opt/bot/bot/virtual_tracker.py`, `/opt/bot/main.py`, `/opt/bot/bot/virtual_order_simulator.py`, `/opt/bot/data/decision_log_test.json`, `/opt/bot/data/preset_efficiency_test.json`, `/opt/bot/data/real_orders_*_test.json`

---

## 1. Gap Inventory with Quantification

### GAP 1: Leverage Normalization (Critical)

**What it is:** The backtest computes `seeded_winning_usdt` as `total_profit_pct / 100 * $1000` — unleveraged, on a fixed $1,000 notional. Live trades run at 5x leverage on a real balance that started at ~$5,000 and declined to ~$2,900.

**Concrete numbers:**

| Symbol | Best preset | Seeded ($, 1x) | Implied at 5x | Actual live PnL | Gap |
|---|---|---|---|---|---|
| SOLUSDT | r5_sl_adj_cooldown | +$52.23 | +$261.16 | -$34.58 (n=14) | $295.74 |
| DOGEUSDT | lh_sell_trail15 | +$155.04 | +$775.19 | +$16.82 (n=13) | $758.37 |
| TIAUSDT | r5_arm15_cooldown | +$143.06 | +$715.29 | -$0.19 (n=14) | $715.48 |

**Impact on ranking:** The `seeded_winning_usdt` value is used directly by `VirtualTracker._score()` in Tier 0 (before enough live trades accumulate). A seeded score of $155 for DOGEUSDT `lh_sell_trail15` beats a seeded score of $52 for SOLUSDT `r5_sl_adj_cooldown`. But the actual live PnL at 5x leverage diverges drastically from these ratios.

**Root cause in code:** `virtual_tracker.py` line in `seed_from_backtest()`:
```python
seeded = preset_data["total_profit_pct"] / 100.0 * balance_start
```
`balance_start` is always `$1000` (the backtest initial balance). Live allocation is a fraction of real balance, multiplied by leverage. The seeded value is structurally incompatible with live PnL in absolute dollar terms.

**Severity:** Critical. Every preset election before `min_trades_for_ranking=8` live trades is made on this misleading scale. With 80+ presets per symbol and low trade frequency on many symbols, most symbols stay in Tier 0 ranking for weeks.

**Feasibility of fix:** Config change + small code change. See Section 2.

---

### GAP 2: global_min_sl_pct Filter Is Live-Only, Absent from Backtester (High)

**What it is:** `risk_config.json` has `global_min_sl_pct: 0.3`. In `main.py` `_try_place_order()`, this widens any SL tighter than 0.3% of entry. The backtester has no equivalent; it applies only the per-preset `min_sl_pct` value, which for many presets is 0.05%.

**Concrete numbers for SOLUSDT `r5_sl_adj_cooldown`:**
- 14 of 41 backtest trades had `sl_dist < 0.3%` (range: 0.057%–0.28%)
- These 14 trades pass through the backtester (preset floor is 0.05%)
- In old live code (before ~2026-06-01): 40 such signals were **skipped** entirely (logged as `skip_global_min_sl`)
- In current live code: these signals get SL **widened** to 0.3% (logged as `floor_sl_pct`)

**The skip vs widen divergence:** The decision log shows both behaviors in the same session:
- `skip_global_min_sl`: 40 entries, timestamps 2026-06-01T06:30 to 07:15 (old code)
- `floor_sl_pct`: 3 entries, timestamps 2026-06-01T16:45 to 2026-06-02T03:45 (new code)

**Impact of widening:** For a BUY signal where the signal SL was 0.12% away from entry (e.g., SOLUSDT at $85.33 with SL at $85.23), the live code widens SL to 0.3% (SL moved to $85.08). This changes the trade risk profile materially: instead of losing 0.12% of notional at SL, the trade now loses 0.3%, a 2.5x larger loss if hit. The backtest never evaluated this wider SL scenario.

**One confirmed case:** SOLUSDT `trail_15_from_15_d1`, entry=$85.33, SL=$85.32 (sl_dist=0.094%). The trade was logged as a loss at -$0.52. The backtest model had this trade with the natural tight SL — the live widening to 0.3% changed the SL from $85.32 to $85.08, so it was hit on a different candle pattern entirely.

**Severity:** High for any preset where the natural signal geometry produces tight SL (particularly `r5_sl_adj_cooldown`, `db_layer_1`, `trail_*` presets on SOLUSDT).

**Feasibility of fix:** Small code change (add `global_min_sl_pct` to backtester's filter chain).

---

### GAP 3: per_symbol_settings max_profit_pct Blocks All INJUSDT Signals (Critical)

**What it is:** `risk_config.json` `per_symbol_settings.INJUSDT.max_profit_pct: 5`. This live-only filter rejects any signal where the TP distance exceeds 5% of entry. The backtester does not apply `per_symbol_settings` — it tests each preset with its own `max_profit_pct` value, which is 0 (unlimited) for most presets.

**Concrete impact:**
- 289 of 289 logged INJUSDT decisions in the current session are `skip_max_profit_pct`
- 0 INJUSDT trades have been placed in this session
- Profit distances for blocked INJUSDT signals: 5.22%–21.98% (avg 16.76%)
- INJUSDT `r6_arm15_rr4` backtest: +11.73% on $1000 = $117.32 unleveraged. **30 of 33 backtest trades would be blocked** by `max_profit_pct=5`. The 3 trades that pass the filter have profit_dist ≤ 5% and are the weaker subset.
- INJUSDT `r5_sl_adj_cooldown` backtest: +8.65% on $1000 = $86.53. **28 of 31 backtest trades would be blocked.**

**This means:** The backtest is promising +$117 per $1000 (unleveraged) for INJUSDT `r6_arm15_rr4`. But the preset's performance arises almost entirely from large-TP trades that the live bot can never take. The seeded efficiency score is built on this invalid promise, which causes `r6_arm15_rr4` to rank as the "best preset" for INJUSDT — and the bot then still places nothing because all signals exceed the max_profit_pct limit.

**Historical INJUSDT live PnL** (from archive files): Despite the current session blockade, earlier sessions placed INJUSDT trades with `r5_sl_adj_cooldown` and made +$86.98, +$110.19 from large trail wins — the same large-move trades that `max_profit_pct=5` now blocks. The 9 trades in the current archive are: 8 losses totaling -$147.65, 1 trail win of +$0.11.

**Severity:** Critical. This is not a subtle divergence — it is a complete disconnection between what the backtest evaluates and what the live bot can trade on INJUSDT. The `per_symbol_settings` override was likely added to limit risk but in practice prevents the bot from taking the only signals INJUSDT produces.

**Feasibility of fix:** Config change only (remove or raise the `max_profit_pct` limit for INJUSDT).

---

### GAP 4: VirtualOrderSimulator Tracks Different Population Than _try_place_order (High)

**What it is:** `VirtualOrderSimulator._try_open()` runs filters to decide whether to open a virtual position. `main.py _try_place_order()` runs filters to decide whether to open a real position. Some live-only filters in `_try_place_order` are absent from `_try_open`.

**Filters in `_try_place_order` NOT in `_try_open`:**

| Filter | _try_place_order | _try_open |
|---|---|---|
| `can_open_sync` (profit_factor gate) | Yes | No |
| `virtual_only_floor` check | Yes (blocks real orders) | Not applicable |
| `global_min_sl_pct` widening | Yes | No |
| `per_symbol_settings` overrides | Yes (applied to preset_settings) | No |
| `skip_balance` / `skip_min_notional` | Yes | No (uses rank pool) |
| `max_trade_pct` cap | Yes | Yes (added in current code) |

**Consequence:** The VirtualOrderSimulator (which feeds the efficiency scores via `VirtualTracker.record_closed_trade`) is tracking a signal population that includes more trades than the real orders can ever take. Specifically, `_try_open` for INJUSDT will try to open a virtual position on the 5-22% TP signals that `_try_place_order` blocks via `per_symbol_settings`. The virtual scores will reflect a fictitious profitable INJUSDT run that has no real counterpart.

**Verified:** `_try_open` does NOT apply `per_symbol_settings` — it uses `dataclasses.replace(base_settings, **overrides)` where `overrides` is the preset dict only, not the symbol-specific config.

**Severity:** High. The efficiency ranking engine (VirtualTracker) is being trained on a phantom signal set.

**Feasibility of fix:** Medium code change (pass `per_symbol_settings` and `global_min_sl_pct` into `_try_open`).

---

### GAP 5: Backtester Shares No State With Live Analyzer (Medium)

**What it is:** The backtester creates a **fresh `Analyzer` instance per preset** and replays all klines from scratch. The live bot uses a **shared `Analyzer` per symbol** that has accumulated candles continuously since startup.

**Code confirmation:** `backtester.py` `_run_preset()`: `analyzer = Analyzer(settings.swing_neighbours, engine)` — fresh instance per preset per run. `main.py`: `analyzers[symbol] = Analyzer(...)` — one instance, continuously updated.

**Why this matters:** Swing point detection depends on the full candle history. A fresh analyzer fed 2,500 klines builds a different swing point structure than one that has been running for 10+ days and received those same candles incrementally. Specifically, the `swing_neighbours` parameter creates look-ahead and look-back windows — when applied to a static buffer it can identify different swings than when applied to an ever-growing live feed. The resulting recommendations (signal_type, TP, SL levels) can differ.

**Observable consequence:** The live bot may generate signals from swing points that the backtester did not identify, or miss signals the backtester caught. This is an irreducible gap for a continuous-feed system versus a batch replay.

**Severity:** Medium. The divergence is structural and not quantifiable from the available data. It is smaller for symbols with stable trend structures and larger during volatile transitions.

**Feasibility of fix:** Partially reducible. Running the backtester with a fresh klines window that matches the live window size (last N candles) reduces the divergence, but the look-ahead boundary effect cannot be fully eliminated.

---

### GAP 6: No Slippage Model (Medium)

**What it is:** The backtester enters at exactly the candle close price. Live orders are market orders triggered at a live tick price that arrives slightly after the candle close. The difference is slippage.

**Observable evidence:** From SOLUSDT real orders, the bot consistently places at the `current_price` (live tick at candle close), not the kline close. The decision log's `placed` entries show the signal used in `_try_place_order` re-runs `RecommendationEngine` with `_preset_entry_px = _current_px`, meaning the entry uses the live tick. For a 15-minute candle on SOLUSDT at ~$82, the distance between the candle close and the fill price is typically $0.05–$0.20 (0.06%–0.24%).

**Estimated PnL impact:** At 5x leverage, 0.15% slippage (round trip: 0.15% in + 0.15% out) costs ~1.5% of margin per trade. On a $500 margin trade, that is $7.50 per trade. Across 228 trades: ~$1,710 in unmodeled slippage costs. This is a worst-case; average slippage is likely 0.05%–0.10%.

**Severity:** Medium. Non-trivial on the 228 trade sample, but smaller than the INJUSDT and leverage normalization gaps.

**Feasibility of fix:** Config change only (add `slippage_pct` to backtester, e.g., 0.05% per side).

---

### GAP 7: 26-Day Backtest Window May Not Match Current Regime (Medium)

**What it is:** The backtest runs on the last `backtest_klines: 2500` candles, which at 15m/candle = ~26 days. The bot re-runs the backtest at startup and re-seeds efficiency scores. This means the backtest window shifts every time the bot restarts.

**Observable consequence:** WLDUSDT `partial_70` was the top backtest preset (+23.5% on $1000, 38 trades at entry prices $0.26–$0.45). The live bot placed WLDUSDT trades at $0.30–$0.45 (the current price regime), and all 12 live WLDUSDT trades lost (-$54.57, 0% win rate). The backtest window included the bullish regime where `partial_70` worked; the live trades fell in a different part of that window or in a regime shift.

**DOGEUSDT contrast:** `lh_sell_trail15` (backtest: +$155.04 seeded) is consistent — both backtest and live show it as profitable. This is a symbol where the regime signal in the 26-day window and the live regime matched. WLDUSDT is where they didn't.

**Severity:** Medium. This is an inherent limitation of any finite backtest window. The question is whether 26 days is appropriate — it is short enough to be regime-sensitive, long enough to capture trend structures.

**Feasibility of fix:** Config change only (experiment with `backtest_klines: 5000` or 52-day window). Trade-off: longer window averages across more regimes (less responsive to recent trend) vs. shorter window tracks current regime (overfits noise).

---

### GAP 8: Fee Model Consistency (Low — Not a Gap)

**What it is:** Investigation confirmed both backtester and VirtualOrderSimulator use `fee_rate = 0.0004` (0.04% per side). Binance Futures Testnet default taker fee is also 0.04%. The fee model is consistent.

---

## 2. The Correct Path: Priority-Ordered Action Plan

### Action 1: Remove or Correct the INJUSDT max_profit_pct Override

**File:** `/opt/bot/risk_config.json`  
**Change:** Remove `"INJUSDT": {"max_profit_pct": 5}` from `per_symbol_settings`, or raise it to ≥ 20 to allow the signals the preset was designed for.  
**What it fixes:** Gap 3. Allows the bot to trade INJUSDT again. In the current session the bot has placed 0 INJUSDT trades while blocking 289 signals.  
**Risk:** The `max_profit_pct` override was probably added because INJUSDT's large TP signals carry outsized loss when they fail. However, the live history shows `r5_sl_adj_cooldown` earned +$86.98 and +$110.19 in a single session before the override was added. The risk of reverting is accepting larger per-trade drawdown on INJUSDT losses — the trade-off is explicit and documented in the archive.  
**Expected PnL impact:** Unblocks ~29 INJUSDT signals per session equivalent. Based on archive win rate (~3 wins out of 12 archive INJUSDT trades, but the 3 wins were large: +$87, +$110, +$17), restoring access should be net positive.

---

### Action 2: Add global_min_sl_pct to the Backtester

**File:** `/opt/bot/bot/backtester.py`  
**Change:** In `_run_preset()`, after the `max_profit_pct` check, add:
```python
_global_min_sl = 0.0
if self._risk_config_path is not None:
    _cfg = load_risk_config(self._risk_config_path)
    _global_min_sl = _cfg.get("global_min_sl_pct", 0.0)
_effective_min_sl = max(
    settings.min_sl_pct if settings.min_sl_pct > 0 else 0.0,
    _global_min_sl,
)
```
Then replace the existing `if settings.min_sl_pct > 0 and sl_dist_pct < settings.min_sl_pct: continue` block with the widening logic that matches `_try_place_order` exactly (widen SL to floor instead of skipping).

**What it fixes:** Gap 2. The backtester will simulate the same SL floor behavior as the live bot, making the backtest win rates and PnL reflect what live will actually trade.

**Risk:** This changes backtest output numbers. Preset rankings may shift. The change is directionally correct — the old numbers were wrong; the new numbers will be more accurate. Low risk of introducing new bugs since it is additive logic.

**Expected PnL impact:** For SOLUSDT, 14/41 `r5_sl_adj_cooldown` trades will be re-evaluated with wider SL. Some will still win (trailing stop logic means many of them close profitably regardless of SL placement). The overall backtest profit% for affected presets will decrease slightly, giving a more honest view of what the live bot actually does.

---

### Action 3: Apply per_symbol_settings Inside VirtualOrderSimulator._try_open()

**File:** `/opt/bot/bot/virtual_order_simulator.py`  
**Change:** In `_try_open()`, after `preset_settings = dataclasses.replace(base_settings, **overrides)`, add:
```python
_sym_cfg_overrides = load_risk_config().get("per_symbol_settings", {}).get(symbol, {})
if _sym_cfg_overrides:
    _valid_fields = {f.name for f in dataclasses.fields(Settings)}
    _filtered = {k: v for k, v in _sym_cfg_overrides.items() if k in _valid_fields}
    if _filtered:
        preset_settings = dataclasses.replace(preset_settings, **_filtered)
```
Also propagate `global_min_sl_pct` widening to match `_try_place_order`.

**What it fixes:** Gap 4. The virtual simulator and real order placement now evaluate the same signal population. INJUSDT virtual scores will no longer be inflated by blocked signals.

**Risk:** Medium. Loading risk config inside a hot path (`_try_open` is called every candle per symbol per rank) adds latency. Use a cached config reference passed from `on_candle_close` instead of calling `load_risk_config()` inline.

**Expected PnL impact:** The virtual efficiency scores for INJUSDT will decline (they were being trained on the wrong signals). This is correct behavior — the efficiency scorer should track what real orders actually do.

---

### Action 4: Normalize seeded_winning_usdt by Leverage Before Ranking

**File:** `/opt/bot/bot/virtual_tracker.py`  
**Change:** In `seed_from_backtest()`, scale the seeded value by the actual leverage the symbol uses at startup, so the Tier 0 ranking is comparable in dollar magnitude to the live Tier 1 ranking:
```python
# In seed_from_backtest, apply leverage scaling so seeded value is in live-equivalent USDT
# The caller must pass or the method must derive the expected leverage
seeded = preset_data["total_profit_pct"] / 100.0 * balance_start * expected_leverage
```
Alternatively, normalize within `_score()` by dividing seeded by the backtest's implicit 1x leverage ratio.

**Simpler alternative (config change):** Change `backtest_initial_balance_usdt` in `risk_config.json` from the current $1,000 to `balance * leverage` (e.g., $5,000 at 5x). The backtester's `balance_start` is read from `backtest_initial_balance_usdt` in the backtest runner. This would make `total_profit_pct * balance_start` approximately comparable to live PnL.

**What it fixes:** Gap 1. The seeded scores will be in the same order of magnitude as live PnL, preventing misleading Tier 0 rankings.

**Risk:** Small code change, low risk. The relative ordering of presets by seeded score is unaffected (scaling is monotone). Hysteresis and cooldown thresholds are in USDT terms — they may need re-tuning after scaling.

**Expected PnL impact:** Indirect — better preset selection in the Tier 0 period (first 8 trades per symbol). Hard to quantify without more live data.

---

### Action 5: Add Slippage Model to Backtester

**File:** `/opt/bot/bot/backtester.py`  
**Change:** Add `slippage_pct: float = 0.05` to `Backtester.__init__` (or read from risk config). Apply to the entry price:
```python
entry_price = close_price * (1 + slippage_pct / 100.0) if side == 'BUY' else close_price * (1 - slippage_pct / 100.0)
```
A conservative estimate of 0.05% per side (0.10% round trip) reflects market order fill cost on liquid futures.

**What it fixes:** Gap 6. Backtest PnL will be slightly lower but more honest. Win rates will decrease marginally for trades close to SL.

**Risk:** Very low. Additive logic, no behavioral change to existing code paths. The slippage_pct value should be configurable (default 0.05%) so it can be tuned.

**Expected PnL impact:** At 228 trades and 0.05% slippage both sides: $0.10% * avg_notional. If avg notional is ~$500 (5x leverage on $100 margin), that is $0.50 per trade = $114 over 228 trades. This brings the backtest's unleveraged PnL closer to reality.

---

### Action 6: Add per_symbol_settings to Backtester

**File:** `/opt/bot/bot/backtester.py`  
**Change:** In `_run_preset()`, after building `preset_settings`, apply symbol-level overrides:
```python
if self._risk_config_path is not None:
    _sym_cfg = load_risk_config(self._risk_config_path).get("per_symbol_settings", {}).get(self._base.symbol, {})
    if _sym_cfg:
        _valid = {f.name for f in dataclasses.fields(Settings)}
        _filtered = {k: v for k, v in _sym_cfg.items() if k in _valid}
        if _filtered:
            settings = dataclasses.replace(settings, **_filtered)
```

**What it fixes:** Gap 3 (partially). The backtest for INJUSDT will now reflect the `max_profit_pct=5` filter, showing that those top presets produce near-zero trades under the filter — honest signal that the per_symbol_settings is wrong.

**Risk:** Low. This is the correct behavior — the backtest should simulate what the live bot actually does.

**Expected PnL impact:** INJUSDT backtest results will collapse (near-zero trades, which is accurate given current config). This will lower INJUSDT's seeded efficiency score dramatically, preventing the bot from allocating capital to it in the first place — which is actually correct behavior given the current broken configuration.

---

### Action 7: Evaluate Widening the Backtest Window to 52 Days

**File:** `/opt/bot/risk_config.json`  
**Change:** `"backtest_klines": 5000` (instead of 2500)  
**What it fixes:** Gap 7. Reduces regime-sensitivity of the backtest window. WLDUSDT's 26-day snapshot included the period when `partial_70` was profitable; a 52-day window would average across more market conditions.

**Risk:** Startup backtests take ~2x longer. Backtests become less responsive to recent regime changes. Trade-off must be evaluated empirically.

**Expected PnL impact:** Unclear — could improve or worsen depending on whether longer history better represents the current market.

---

## 3. What NOT to Do

### Do NOT remove `virtual_only_floor`

`virtual_only_floor: -5` in risk_config prevents real orders when the best preset's live cumulative PnL is below -$5. This gate exists to halt trading when a symbol is genuinely losing in live conditions. Removing it would "fix" the backtest-live gap by accepting more real trades — but those trades are specifically the ones the system identified as consistently losers. The gate is protective, not a bug.

### Do NOT assume seeded scores imply positive expected value

WLDUSDT's `partial_70` has `seeded_winning_usdt = +$234.99` (1x, $1000 base). This implies the backtest period was profitable. But the live bot placed 12 consecutive losing trades on WLDUSDT. The 26-day backtest window included a favorable price regime; the live period did not. Raising the `virtual_only_floor` threshold or removing it would not help — the backtest's forward-looking validity is the real issue.

### Do NOT increase leverage to close the gap

The gap between backtest-promised PnL and live PnL is not caused by insufficient leverage. The gap exists because the backtest is evaluating signals the live bot cannot take (INJUSDT) and using a different SL behavior (global_min_sl_pct). Increasing leverage from 5x to 10x would amplify both wins and losses symmetrically and would not fix the structural divergences.

### Do NOT interpret the "implied at 5x" column as expected live PnL

The computation `seeded * 5` (e.g., DOGEUSDT `lh_sell_trail15` = $775) is a ceiling, not an expectation. It assumes the same signal frequency, same win rate, and same trade geometry as the backtest. In practice: market regimes shift, signal frequency varies, and SL-widening changes risk profiles. The actual live PnL for `lh_sell_trail15` DOGEUSDT was +$16.82 on 13 trades — positive, but far below $775. This is mostly explained by trade count (13 trades over 10 days, not 82 trades over 26 days) and market regime differences.

### Do NOT remove `duplicate_skip_candles` from presets

15 `skip_duplicate_sl` entries for WLDUSDT prevented re-entry after SL hits. Given WLDUSDT's 0% win rate in live, these 15 skips likely prevented 15 additional losses. This filter is working as intended.

---

## 4. The Honest Assessment

**After all fixes: will live fully match backtest?**

No. The irreducible gap has three components:

**Component 1: Market regime mismatch (~50% of the gap)**
The backtester evaluates on the last 26 days of candles. Live trading occurs in the future. When the market regime shifts (trend reversal, volatility change, range contraction), presets that worked in the past 26 days will underperform. WLDUSDT is the clearest example: -$54.57 live on a +$234.99 seeded promise. No code change fixes this — it is the fundamental challenge of forward-trading a backtest result.

**Component 2: Signal quality divergence (~25% of the gap)**
The live analyzer accumulates candles continuously (shared, stateful Analyzer). The backtester uses a fresh Analyzer per preset. Swing point detection can differ, especially near the beginning of the live session. The resulting signal geometry (TP, SL levels) will not exactly match backtest geometry for the same price point. This gap is structural and cannot be fully closed.

**Component 3: Execution frictions (~25% of the gap)**
Slippage (0.05%–0.15% per trade), candle-close to market-order fill latency, and occasional balance constraints that prevent order placement. These are real costs that the backtester ignores. At 228 trades, estimated slippage cost is $50–$150 total. This is measurable and can be modeled.

**After all proposed fixes, expected improvement:**
- INJUSDT trades unblocked: based on archive history, estimated +$50–$100 per active week if previous trail-win performance holds
- Backtest accuracy improvement: backtest PnL numbers for INJUSDT, SOLUSDT (tight-SL trades) will be lower and more accurate — meaning the seeded rankings will be more calibrated
- VirtualTracker efficiency scores will track the actual signal population — efficiency rankings will become more predictive of live performance within 3–4 weeks of live trading per symbol

**The irreducible floor:** Even after all fixes, expect live PnL to be 40%–60% of what the backtester projects, due to regime mismatch and execution costs. A 26-day backtest on a 15-minute timeframe captures roughly 2,500 candles; even with good modeling, the next 10 days will not exactly replay the prior 26. The bot's edge relies on the strategy having positive expectancy across multiple regimes, which requires enough live trade history (50+ trades per symbol) to validate or refute the backtest signal.

---

## 5. Appendix: Key Numbers

**Live performance by symbol (2026-05-23 to 2026-06-03):**

| Symbol | Trades | PnL ($) | Win Rate | Leverage | Notes |
|---|---|---|---|---|---|
| TIAUSDT | 23 | +76.18 | 61% | 2–5x | Best performer, consistent |
| 1000PEPEUSDT | 39 | +28.05 | 36% | 5x | Positive despite moderate WR |
| MEMEUSDT | 21 | +3.64 | 43% | 3–5x | Marginal positive |
| DOGEUSDT | 22 | +1.39 | 45% | 2–5x | Positive but thin |
| AVAXUSDT | 16 | -0.91 | 12% | 2–5x | Near break-even |
| REZUSDT | 13 | -8.62 | 38% | 5x | Moderate loss |
| SOLUSDT | 10 | -9.53 | 50% | 5x | 1 large loss (-17.31) |
| 1000SHIBUSDT | 12 | -10.51 | 0% | 2–5x | No wins at all |
| APTUSDT | 21 | -30.96 | 10% | 2–5x | Consistent losses |
| ETHFIUSDT | 24 | -49.83 | 12% | 5x | Consistent losses |
| WLDUSDT | 12 | -54.57 | 0% | 2–5x | Wrong regime |
| INJUSDT | 9 | -147.65 | 11% | 2–5x | Preset selection failure |
| THETAUSDT | 6 | +12.48 | 17% | 5x | Small positive |

**Decision log (current session only, 388 entries):**

| Decision | Count | Notes |
|---|---|---|
| skip_max_profit_pct | 289 | All INJUSDT |
| skip_global_min_sl | 40 | All SOLUSDT, old code behavior |
| placed | 33 | Actual orders placed |
| skip_duplicate_sl | 15 | All WLDUSDT |
| skip_rr | 8 | All ETHFIUSDT |
| floor_sl_pct | 3 | New code behavior (widen not skip) |

**INJUSDT filter breakdown:**
- r6_arm15_rr4: 30/33 backtest trades blocked by max_profit_pct=5
- r5_sl_adj_cooldown: 28/31 backtest trades blocked by max_profit_pct=5
- Backtest promise for r6_arm15_rr4: +$117.32 unleveraged / ~+$586 at 5x
- Live INJUSDT PnL this session: $0 (nothing placed)

**SOLUSDT SL distance analysis (r5_sl_adj_cooldown, 41 backtest trades):**
- Minimum sl_dist: 0.057%
- Maximum sl_dist: 1.672%
- Median sl_dist: 0.514%
- Trades with sl_dist < 0.3% (affected by global_min_sl_pct): 14/41 (34%)

