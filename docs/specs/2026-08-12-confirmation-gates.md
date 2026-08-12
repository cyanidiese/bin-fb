# Spec: Confirmation Gates via Shared MarketContext

**Date:** 2026-08-12 (session 60)
**Status:** SPEC — implementation not started. Backtest-validate each gate before deploy.
**Depends on / pairs with:** docs/specs/2026-07-16-trend-structure-fixes.md (structural entry-quality fixes),
docs/2026-08-11-signal-precision-analysis.md (the analysis + panel that motivated this).

---

## Why (validated evidence, not the 64-trade join)

A no-lookahead counterfactual backtest over **311 real trades** (May–Aug 2026), every feature computed
from the last *closed* candle at signal time (`/tmp/s60/validate_nolook.py`):

| Filter | Book net | vs baseline −$785 | kept |
|---|---|---|---|
| Baseline (no filter) | −$785 | — | 311 |
| Taker-flow confirm (per side) | **−$10** | +$775 | 148 |
| EMA50 vs EMA200 directional | −$104 | +$681 | 119 |
| EMA200 + taker (AND) | −$27 | +$758 | 63 |
| BTC market regime | −$436 | weak | 142 |
| rel-volume > 1 | −$703 | noise | 171 |

**Honest framing:** the strategy has **no positive edge in this regime** (baseline −$785). These gates are
*loss-avoidance / stability* levers — they take a −$785 book to roughly breakeven by refusing ~half the
trades. They do NOT manufacture positive edge; the kept trades are still ~30% WR, carried by trailing
exits. Durable positive edge needs the structural entry-quality fixes (separate spec). Do not oversell.

**Two prior traps this evidence corrects:**
1. The panel ranked taker-flow #1 on a +$143 number that was **one-candle lookahead**. The honest,
   last-closed-candle value is −$10 (still the best single lever, but protective not profitable).
2. Direct structural proof the parent-alignment gate works: `l2_bos_entry` (`ignore_parent_alignment=True`)
   is −$100.91 all-time / 17% WR; its sibling `l2_bos_trend` (`ignore_parent_alignment=False`) is
   **+$137.71 / 47% WR**. Same engine, opposite alignment setting, opposite result.

`l2_bos_entry` was blocklisted via hot-reload on 2026-08-12 as an interim stopgap.

---

## What — three gates, ranked by (impact ÷ effort)

All gates are **swept per-preset knobs** (default off), never hard-coded constants. Each must be applied
in the live engine AND mirrored in the backtester + virtual simulator, or preset-efficiency scores drift
from live reality (the parity trap that produced the `ignore_parent_alignment` workarounds).

> **⚠️ VALIDATION RESULT (2026-08-12): Gate 1 does NOT pass — do not enable.**
> The mechanism was implemented (Settings field `enforce_parent_alignment_hard`, engine gate,
> hot-reloadable `global_enforce_parent_alignment`, 7 unit tests) and validated end-to-end with the
> real backtester (flag OFF vs ON, isolated on the same preset, 5 active symbols, ~8k candles):
> `l2_bos_entry` −10.99%→−47.20%, `l2_regime_aware` −14.32%→−46.81%, `l2_trend_buy` −64.43%→−62.42%.
> It is net-**negative** — mainly by cutting profitable MEMEUSDT continuation trades, plus strong
> path-dependence (blocking an early signal frees the engine to take many later ones).
> The original "natural experiment" (l2_bos_trend +$138 vs l2_bos_entry −$101) was **confounded** —
> those presets differ in several params, not just alignment. The mechanism is kept **dormant**
> (default off, set on no preset) as a tested, hot-reloadable lever should a future regime/evidence
> justify it. Lesson: the realized-trade counterfactual join and the full-kline backtester disagree
> because the join only sees post-gating placed trades; **backtest-validate every gate before enabling.**

### Gate 1 — Parent-alignment hard gate (cheapest, proven, no new data) — REJECTED BY VALIDATION
- **What:** when a preset has `ignore_parent_alignment=True`, still reject a signal whose bigger-trend
  parent explicitly opposes it. Reuse the existing `_parent_is_opposing(trend, side)` helper
  (`recommendation_engine.py:307`).
- **Why:** the l2_bos_entry vs l2_bos_trend result is direct proof. Targets the counter-trend BUY leak
  (−$436/11%WR recent) without touching the profitable, aligned flow.
- **Effort:** low. No data plumbing, no MarketContext. Ships first.
- **Touch points:** `recommendation_engine._score_and_filter` (~line 210, the existing `_CONTINUATION_TYPES`
  block) — split the `ignore_parent_alignment` short-circuit so a NEW preset flag
  `enforce_parent_alignment_hard` re-enables the opposing-parent reject regardless. Mirror in
  `backtester.py` and `virtual_order_simulator.py` (they call the same engine, so ideally zero extra work —
  verify they route through `RecommendationEngine`).
- **Validation:** backtest sweep — BUY net should improve toward the l2_bos_trend profile; SELL unchanged;
  confirm no new signal droughts.

### Gate 2 — EMA50/200 directional confirmation (no plumbing)
- **What:** block BUY unless close > EMA200 (and/or EMA50 > EMA200); block SELL unless below. Thresholds
  and which MA pair = preset knobs.
- **Why:** −$785 → −$104 on the full sample, no lookahead, close-based (data already present everywhere).
- **Effort:** low–medium (needs MarketContext for clean parity, but no data_feed change).
- **Touch points:** build EMA in the shared MarketContext (below); gate in `_score_and_filter`.
- **Validation:** backtest — book net improves ~$680; confirm SELL side (profitable) not degraded.

### Gate 3 — Taker-flow confirmation (biggest lever, needs plumbing fix)
- **What:** block BUY when last-closed-candle taker-buy ratio (idx9/idx5) < threshold; SELL when
  > (1−threshold). Single-candle beat the 3-candle average in testing (−$10 vs −$119) — use last closed
  candle, tunable lookback.
- **Why:** single strongest lever (−$785 → −$10). Data is free (already in every REST kline).
- **PREREQUISITE — live plumbing fix:** the websocket candle builder in `bot/data_feed.py`
  (~lines 192-200, 262-265, 346-349) truncates each candle to 7 fields, dropping taker fields (idx 7-10).
  The live path would silently no-op (taker ratio always undefined) unless these builders preserve the
  12-field kline. Must fix + add defensive handling for mixed 7/12-field rows.
- **Effort:** medium (plumbing + MarketContext + gate + parity).
- **Touch points:** `data_feed.py` candle builders; MarketContext taker term; gate in `_score_and_filter`;
  backtester/virtual-sim read taker from their 12-field klines (already present there).
- **Validation:** backtest — book net → ~breakeven; **critical:** assert the live MarketContext taker
  value is non-null in a smoke test after deploy (the plumbing-fix failure mode is a silent no-op).

---

## The enabler — shared MarketContext (idea A1)

A small object computed once from recent klines and passed into the engine, so every gate is written
once and replayed identically by backtester + virtual simulator.

- **Shape:** `MarketContext(ema50, ema200, taker_ratio, taker_ratio_3c, rel_volume, atr)` — all derived
  from the last N closed candles.
- **Built by:** `bot/analyzer.py` (live, from `self._klines`) and the `backtester.py` / 
  `virtual_order_simulator.py` per-candle loops (from their kline slice) — both from the SAME fields so
  parity holds by construction.
- **Consumed by:** `recommendation_engine.generate()/collect_all()/_score_and_filter()` gain an optional
  `ctx` arg. When `ctx is None` (callers not yet updated), all ctx-dependent gates are skipped — safe
  incremental rollout.
- **No-lookahead rule (non-negotiable):** MarketContext is built from candles strictly BEFORE the entry
  candle (last closed candle at signal time). Enforce with a unit test that feeds a known series and
  asserts the entry candle itself is excluded — this is the exact bug that inflated the taker edge.

---

## Build order

1. **Gate 1** (parent-alignment) — smallest, proven, no MarketContext. Ship + validate alone.
2. **MarketContext scaffold** + **Gate 2** (EMA) — no data_feed change.
3. **data_feed 12-field fix** + **Gate 3** (taker) — the plumbing-gated big lever.
4. Each gate: backtest sweep first, then deploy behind a per-preset flag, observe 1 week.

## Rejected / out of scope (from the panel, confirmed by data)
- Funding / open-interest / orderbook gates — un-backtestable on testnet (no replayable history),
  would corrupt preset selection. Revisit only on live.
- BTC market-regime gate — weak on the full sample (−$436). Not worth the cross-asset fetch now.
- rel-volume as a standalone gate — noise (−$703). May survive only as a MarketContext term inside a
  combined score, not a gate.
- Hard AND of all gates — over-filters (63 trades). Gates are independent swept knobs; let the
  efficiency sweep choose per-symbol strictness.

## Honest expected outcome
Book from ~−$785/311-trades toward breakeven; materially fewer, higher-conviction trades; improved
stability and drawdown. NOT a profit engine on its own — that needs the 2026-07-16 structural fixes.
