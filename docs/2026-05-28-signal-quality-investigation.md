# Signal Quality Investigation — 2026-05-28

Source data: May 23–25 real trades, decision logs, kline data for all 15 symbols.

---

## Summary Numbers

- 42 real trades analyzed
- Net PnL: -$85.80
- Every BUY trade lost: 6 entries, -$141.13
- SELL trades: 33% win rate, +$55.33
- One INJUSDT BUY was sized at 300% of balance — accounts for $128 of BUY losses

---

## Signal Type Performance (real trades)

| Signal Type | N | Win% | Net PnL |
|---|---|---|---|
| rising_below_last_high | 6 | 0% | -$141.13 |
| lowering_near_last_low | 2 | 0% | -$7.31 |
| lowering_above_last_low | 33 | 33% | +$14.82 |
| rising_above_supposed_high (reversal) | 1 | 100% | +$47.82 |

---

## Root Causes

### A — No hard directional gate when parent trend opposes signal

`rising_below_last_high` BUY fires regardless of L2/L3 trend direction. Parent alignment check
reduces the precision score but does NOT block the signal. INJUSDT BUY trades had alignment = 0.0
(parent was descending), precision = 0.39 — not blocked because there's no minimum precision floor
or alignment-based veto. The 3 INJUSDT BUYs that lost were all counter-trend entries in a descending
market.

### B — Stale swing levels persist indefinitely

ETHFIUSDT: 1,716 blocked signals with median projected profit 17.8% (level 17–20% from market price).
APTUSDT: same level generated every signal for 4 full days. Levels expire only on BoS crossing, never
by age or distance. In ranging/trending markets this produces massive noise in the signal stream and
increases chance of eventually executing on a stale level.

### C — Re-entry into a repeatedly failed zone (DOGE)

First DOGE SELL won, then 5 more entries fired at the same zone — all 5 lost (-$8.95). The
`duplicate_skip` filter only blocks while a position is open, not after SL is hit.

### D — Oversized positions in best_gets_first scenario

Two INJUSDT BUY trades: $9,367 and $6,686 notional on a $3,058 balance (306% and 219%).
Turned a ~$10 directional error into a -$128 loss. No per-trade notional cap exists.

---

## Missed Profits

- 65.8% of skips = `skip_max_profit_pct` (stale levels, correctly rejected, cost ≈ $0)
- AVAXUSDT: 1,034 signals, 0 executed — `max_sl_pct=1.5%` while AVAX min SL = 1.98%. Dead symbol.
- TIAUSDT RR filter: 13 signals at RR=1.43 vs threshold 1.5, expected value +$0.045/unit → ~$10–15 left on table
- INJUSDT max_profit_pct: 75 signals blocked at 4.3–5.0% (cap is 3%) → ~$71 potential (needs backtest)

---

## Proposed Improvements

| # | Proposal | Est. Impact | Effort | Status |
|---|----------|-------------|--------|--------|
| 1 | Hard block when parent alignment = 0 on continuation signals | ~$128+ | Low | pending |
| 2 | Minimum precision floor (`min_precision_score: 0.25`) | ~$7 + tuning lever | Low | pending |
| 3 | Zone-level consecutive-SL cooldown (block after 2 SL hits at same level) | ~$5–6 | Medium | pending |
| 4 | INJUSDT max_profit_pct override: 5.0% | ~$71 potential | Low | pending |
| 5 | Position size cap as % of balance in best_gets_first | catastrophic loss prevention | Medium | pending |

---

## Anomalies

1. **INJUSDT missing preset_name** on 3 `first_has_most` trade entries — efficiency tracking broken for that scenario
2. **Balance jump +$1,436** between two INJUSDT trades with no visible winning trade — investigate balance tracking
3. **AVAXUSDT effectively disabled** — needs either per-symbol `max_sl_pct` override (4.0%) or removal from active symbols

---

## Files to Change

- `bot/recommendation_engine.py` — Proposals 1, 2
- `main.py` — Proposal 3 (zone consecutive-SL cooldown)
- `risk_config.json` / `config/settings.py` — Proposal 4 (per-symbol max_profit_pct)
- `bot/leverage_scenario.py` — Proposal 5 (notional cap)
- `config/settings.py` — new params: `min_precision_score`, `max_notional_pct_of_balance`
