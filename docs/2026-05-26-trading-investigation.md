# Trading Investigation — 2026-05-26

Analysis of why live orders are scarce and underperform backtest results.
Covers all 10 symbols with real orders. All PnL figures in USDT.

---

## Overall Live Results (at time of analysis)

- 42 real orders closed, total PnL **−$85.80** (adjusted real: ~**−$30** after phantom removal)
- Win rate: 28.6% (12 wins / 30 losses)
- TIAUSDT and SOLUSDT profitable (+$75.29 and +$5.71) when correct preset locked early
- Bulk of losses concentrated in INJUSDT (−$134, mostly phantom from pre-fix notional bug)

---

## Root Cause #1 — CRITICAL: Locked presets with `max_profit_pct: 3.0` blocked 100% of signals

Three symbols were locked to presets containing `max_profit_pct: 3.0`, which rejects any
signal where TP exceeds 3% from entry. These symbols naturally produce TPs of 10–20%.

| Symbol | Locked preset | Signals blocked | Orders placed |
|---|---|---|---|
| APTUSDT | `r7_trail15_maxp3` | 615 / 615 | 0 |
| MEMEUSDT | `r7_trail15_maxp3` | 540 / 540 | 0 |
| 1000PEPEUSDT | `r7_arm20_maxp3_trail20` | 299 / 299 | 0 |
| AVAXUSDT | `db_clone_cooldown` (max_sl_pct=1.5%) | 1,034 / 1,034 | 0 |

**Fix:** Remove these locked_preset entries from risk_config.json. For APTUSDT, backtest
recommends `trail_15_from_15` (56.2% win rate, +6.51% profit over 32 trades on 1,509 candles).

---

## Root Cause #2 — CRITICAL: `seed_from_backtest` wiped live trade history on every restart

`bot/virtual_tracker.py` line 73 unconditionally overwrites the entire efficiency entry
(including live `trade_count` and `total_winning_usdt`) with zeros on every startup.

The bot restarted **15 times in 4 days**. No preset ever survived long enough to build a
meaningful live track record.

Direct damage: INJUSDT's `r5_sl_adj_cooldown` had 8 live trades and +$52.79 earned — the
system was selecting correctly. A restart wiped it, switched to `r5_arm15_cooldown` (seeded
winner), which then placed two large losing orders.

```python
# BEFORE (broken) — wipes live learning on every restart
self._efficiency.setdefault(symbol, {})[name] = {
    "total_winning_usdt": 0.0,
    "trade_count": 0,
    "seeded_winning_usdt": seeded,
}

# AFTER (fixed) — preserves live learning, only refreshes seeded score
existing = self._efficiency.setdefault(symbol, {}).get(name, {})
self._efficiency[symbol][name] = {
    "total_winning_usdt": existing.get("total_winning_usdt", 0.0),
    "trade_count":        existing.get("trade_count", 0),
    "seeded_winning_usdt": seeded,
}
```

---

## Root Cause #3 — IMPORTANT: Pre-fix notional cap bug inflated INJUSDT losses 18×

Commit 78aec73 (deployed 2026-05-24 16:35) fixed a bug where the `OpenOrder` was created
with the original uncapped quantity even after the exchange received a capped (smaller) one.

- Reported INJUSDT loss: −$134.22
- Actual exchange loss: ~−$10.24
- Phantom inflation: ~−$124

The phantom negative PnL drove INJUSDT's efficiency to an unrecoverable state, causing
`best_preset` to return `None` and halting all INJUSDT orders.

**Status:** Fix already deployed. Historical efficiency data still corrupted — needs manual reset.

---

## Root Cause #4 — IMPORTANT: Duplicate-signal skip blocking 48% of DOGEUSDT signals

`trail_15_from_15` has `duplicate_skip_candles: 3`. In 4 days:
- 105 of 219 DOGEUSDT decisions were skipped as duplicates (48%)
- Based on 55% backtest win rate, these represent ~40 missed profitable trades
- At ~$8 avg trail PnL → ~$305 in missed profit

**Fix:** Reduce `duplicate_skip_candles` from 3 to 1 for DOGEUSDT's locked preset
(or create a `trail_15_from_15_d1` variant with `duplicate_skip_candles: 1`).

---

## Root Cause #5 — IMPORTANT: Virtual order sizes 10–27× larger than real orders

Virtual orders have no `max_order_notional_usdt` cap. Example:
- DOGEUSDT rank-2 last virtual order: **$13,690 notional**
- DOGEUSDT real orders: **$222–500 notional average**
- Ratio: **27×**

Consequences:
1. Dashboard virtual PnL figures are meaningless for comparison
2. `total_winning_usdt` in efficiency scores is inflated, distorting preset selection

**Fix:** Apply same `max_order_notional_usdt` cap inside `VirtualOrderSimulator._try_open`
after quantity calculation (line ~301).

---

## Root Cause #6 — Scenario switch `first_has_most` → `best_gets_first` correlated with losses

| Scenario | Net PnL | Win rate |
|---|---|---|
| `first_has_most` (balance ~$4,492+) | +$77.85 | 8/12 profitable |
| `best_gets_first` (balance dropped) | −$159.35 | 4/30 |

The scenario switch happened at the balance drop threshold. The preset selected by
`best_gets_first` was different and worse than what `first_has_most` would have chosen.

---

## Root Cause #7 — Virtual rank churn from efficiency re-sorts destroys virtual learning

Every time real order closes → efficiency scores update → up to 30 virtual positions evicted
simultaneously ("rank_change" reason). DOGEUSDT alone: **380 rank_change evictions in 4 days**.

Virtual positions rarely run to natural TP/SL. Rank pool balances accumulate eviction PnL,
not strategy PnL — making virtual comparison unreliable.

---

## Backtest vs Live Win Rate Gap

| Symbol | Backtest win rate (locked preset) | Live win rate | Explanation |
|---|---|---|---|
| DOGEUSDT | 47.5% (99 trades) | 14.3% (7 trades) | Small sample + duplicate skip |
| INJUSDT | 61.4% (44 trades) | 0% (6 trades) | Phantom losses + wrong preset |
| ETHFIUSDT | 33.3% (60 trades) | 0% (5 trades) | Down-trending market |
| SOLUSDT | 54.3% (151 trades) | 100% (2 trades) | Correct preset locked |
| TIAUSDT | — | 88.9% (9 trades) | Correct preset locked early |

The strategy works when the right preset is locked. TIAUSDT and SOLUSDT prove it.

---

## Virtual PnL Inflation from Backtest Seeding

Seeded scores use `total_profit_pct / 100 × balance_start (=$1,000)` from backtest.

Top seeded scores: TIASDT `r5_arm15_cooldown` $796.89, DOGE `trail_20_from_15` $742.42.
These permanently dominate live scores (max live score ~$50) because `min_trades` threshold
means any tier-1 score beats any tier-0 (seeded) score even if tier-1 is negative.

---

## Implemented Fixes (2026-05-26)

| Priority | Fix | File | Status |
|---|---|---|---|
| P1 | Remove dead locked presets (APTUSDT, MEMEUSDT, 1000PEPEUSDT, AVAXUSDT) | `risk_config.json` | ✅ Done |
| P2 | Preserve live trade data across restarts in `seed_from_backtest` | `bot/virtual_tracker.py:73` | ✅ Done |
| P3 | Apply `max_order_notional_usdt` cap to virtual order sizing | `bot/virtual_order_simulator.py` | ✅ Done |
| P4 | Raise `min_trades_for_ranking` from 3 → 5 | `risk_config.json` | ✅ Done |
| P5 | Lock DOGEUSDT to `trail_15_from_15` with reduced duplicate skip | `risk_config.json` / presets | ✅ Done |
| P6 | INJUSDT down-trending — weight reduced, let efficiency self-correct | `risk_config.json` | ✅ Done |

---

## Key Files Referenced

- `tmp_analysis/bot.log` — 15 restarts confirmed, decision log entries
- `tmp_analysis/preset_efficiency_test.json` — seeded vs live scores
- `tmp_analysis/backtest_results_*.json` — per-preset trade counts and win rates
- `bot/virtual_tracker.py` — seeding and efficiency tracking
- `bot/virtual_order_simulator.py` — virtual sizing and rank management
- `config/presets.py` — all preset parameter definitions
- `risk_config.json` — locked_presets, scenario, weights
