# Profit Improvement Analysis — 2026-06-09

**Date:** 2026-06-09  
**Status:** Implementing  
**Real balance:** $4,228 (started $5,000 — net -$772)

---

## Previous Session Changes (already live)

- `trail_15_from_15`: `loss_streak_max: 2`, `cooldown: 5`, `dup_skip: 3→4`
- `trail_15_from_15_d1`: `loss_streak_max: 2`, `cooldown: 5`
- `lh_sell_trail15` + `lh_sell_prox15_trail15`: `loss_streak_max: 2`, `cooldown: 5`, `min_sl_pct: 0.40`, `dup_skip: 2`
- `preset_blocklist: ["r6_arm15_rr4"]` in risk_config + enforced in main.py
- TIAUSDT weight: 25 → 12
- SOLUSDT weight: 0 → 6
- INJUSDT unlocked from locked_presets
- `min_profit_factor`: 1.20 → 1.15
- `global_min_sl_pct: 0.3`
- `oscillating_zone` preset created

---

## Findings

### CRITICAL

**Finding 1: Loss streak guard bypassed by preset switching — worst structural bug**

The streak key is `symbol:preset:side`. When VirtualTracker switches from `trail_15_from_15_d1` to `trail_15_from_15`, the key changes and the counter resets to 0. TIAUSDT had this on June 7: d1 accumulated 2 losses → switch → fresh counter → 4 more losses, ~-$20 in bypassed entries.

Two compounding issues:
1. `_loss_streak` and `_streak_blocked` are in-memory dicts — any bot restart wipes them
2. A preset switch mid-session resets them even without a restart

**Fix:** 
- Change streak key from `symbol:preset:side` to `symbol:side` in both gate check and update
- Check `_streak_blocked[symbol:side]` ALWAYS at gate (not gated by current preset's `loss_streak_max`) — once a block is set, it applies to all presets for that symbol:direction
- Persist `_loss_streak` and `_streak_blocked` to `data/streak_state_<mode>.json` on each update, load on startup

**Files:** `main.py`  
**Estimated impact:** +$20/session on streak-bypass situations

---

### HIGH

**Finding 2: `correction_w20_trail15_30` has zero protection, lost -$19.90 on PEPE in 2 trades**

PEPE's current best-ranked preset has no `loss_streak_max`, no `duplicate_skip_candles`. Two wide-SL SELL trades (-$6.58, -$13.31) fired back-to-back. The Tier 1 score is inflated by historical wins masking recent losses.

**Fix:** Add `loss_streak_max: 2, loss_streak_cooldown_candles: 5, duplicate_skip_candles: 3, duplicate_skip_pct: 2.0`  
**Files:** `config/presets.py`  
**Estimated impact:** +$15–$25/session on PEPE

**Finding 3: THETAUSDT switched from `pre_confirm_trail15` (+$48 live, 13 trades) to `r8_sol_hlbuy_cooldown` (1 trade, -$10)**

After a single big loss on `lh_sell_prox15_trail15` (-$19.74), tracker switched to an unvalidated preset. `pre_confirm_trail15` is the well-evidenced choice: 13 trades, live-inferred +$48.16.

**Fix:** Lock THETAUSDT to `pre_confirm_trail15` in `locked_presets` in risk_config.json  
**Estimated impact:** +$5–$10/trade vs current unvalidated preset

**Finding 4: REZUSDT — 5 real trades, 0 wins, -$22.55, weight=15 (over-represented)**

Every REZUSDT real trade has lost. Weight=15 wins TATS slots too often for a 0% win-rate symbol.

**Fix:** Lower REZUSDT weight: 15 → 8 in risk_config.json  
**Estimated impact:** +$5–$10/week (fewer slot losses)

---

### MEDIUM

**Finding 5: AVAXUSDT — 713 profit_factor skips, 1 win in 16 trades (6%)**
Gate correctly blocking it. Weight=1 appropriate. No action needed.

**Finding 6: MEMEUSDT (103 streak blocks) and INJUSDT (29 dup-skip blocks) generating no real trades**
Both correctly suppressed — their live scores don't win TATS competition. No change needed.

**Finding 7: SOLUSDT weight=6 still not winning any slots**
Weight too low to outcompete higher-weighted symbols consistently. Would need ≥10 to reliably trade.

**Finding 8: Hour 22 UTC and Hour 00 UTC negative (small sample — not yet actionable)**
Dominated by 2 catastrophic trades. Not a structural time pattern.

**Finding 9: 5 of 11 trail exits were negative (trailing stop fires before price moves)**
Software trailing stop polling-based; fires on micro-ticks. Low per-instance cost but repeated.

**Finding 10: Efficiency scoring Tier 1 uses `recent_trades` window when warmed up**
Already implemented via `window_size` param — not a bug. Preset routing is correct once window fills.

**Finding 11: `market_close` exits cost -$19.68 total from bot restarts closing positions**
3 restarts on June 7. Restarts should be minimized; changes that don't require restart should use hot-reload where possible.

---

## Priority Action List

| # | Action | File | Restart? | Est. weekly USDT |
|---|---|---|---|---|
| 1 | Streak key → symbol:side + always-check + persist | `main.py` | Yes | +$20/session |
| 2 | Add protection to `correction_w20_trail15_30` | `config/presets.py` | Yes | +$15–$25/session |
| 3 | Lock THETAUSDT → `pre_confirm_trail15` | `risk_config.json` | No | +$5–$10/trade |
| 4 | REZUSDT weight: 15 → 8 | `risk_config.json` | No | +$5–$10/week |

Items 3+4 applied immediately via hot-reload. Items 1+2 deployed together.
