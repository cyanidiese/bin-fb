# Mean-Reversion Overlay — Design Spec

**Date:** 2026-08-13 (session 61)
**Status:** Design approved (premise validated + out-of-sample + tail-risk checked). NOT yet implemented.
**Author context:** Session-60 open item #4 — "highest-ceiling new-edge idea: a mean-reversion
overlay for the chop regime (the trend engine structurally loses in ranges)."

---

## 1. Problem & premise

The trend engine has **no positive edge in the current regime** (session 60: 311 real trades,
~28% WR, −$785 net; trailing exits are the only profit source). Its structural weakness is
**ranges/chop** — it fires continuation signals that get chopped up when price oscillates.

**Hypothesis:** conditional on a *confirmed oscillating range*, fading the range extreme toward
the mid has positive expectancy — the exact trade the trend engine gets wrong.

### Premise validation (before any code — this is the load-bearing evidence)

Naive fade (any decile touch) = **no edge** (51% WR, ~1.0 payoff, +4.3% over 1176 trades = noise).

Refined fade (require price to have tested *both* boundaries ≥2× in the window = a *genuine*
range; wick-rejection entry; fee-inclusive) = **real, out-of-sample-stable edge**:

| Config | OOS symbols + | IS net% | OOS net% | WR / payoff |
|---|---|---|---|---|
| fast (TP 0.6·→mid) | 6/8 | +137 | +94 | 62–74% / ~0.55 |
| **mid (TP=mid)** ← chosen | **7/8** | +95 | **+146** | 55–67% / ~0.9 |

**Tail-risk analysis (mid config, per symbol, un-sized net%-of-notional):** max consecutive
losses ~4; drawdown −8% to −34% against +8% to +68% net; **return/DD > 1.5 on 6/8 symbols.**
Weak links: DOGE (net-negative), MEME (thin, return/DD 0.73).

**Stop-width finding (corrected an initial wrong instinct):** a *wide* stop (`sl_buf=0.5`) is
**load-bearing** — tightening to 0.35/0.25 collapses net (+241 → +15/+10) and worsens drawdown,
because range noise needs room before the reversion completes. sl_buf=0.5 is the design value.

**Honest metric framing:** the headline numbers are the **sum of per-trade return-on-notional**
(~+0.2%/trade over ~1150 trades), NOT an account return. Real USDT impact depends on sizing,
one-position-per-symbol, and risk cooldowns — quantified only by the real backtester (Gate A below).

Probe scripts (reusable): `/tmp/s60/mr_feasibility.py` (naive), `/tmp/s60/mr_refine.py`
(refined + OOS + tail-risk). Data: `/tmp/s60/fullklines/*.json` (8 symbols, ~8100 15m candles).

---

## 2. Scope (YAGNI)

**In scope:** a regime-switched MR signal source that (a) detects confirmed ranges, (b) emits
fade `Recommendation`s routed through the existing order/risk/exit pipeline, (c) suppresses trend
continuation while a range is active, (d) is gated per-symbol and OFF by default, testnet-first.

**Out of scope (explicitly):** new market-data sources (funding/OI/orderbook — un-backtestable on
testnet), new order types, changes to order execution/sizing/risk internals, any live-mode change,
DOGE (excluded by evidence).

---

## 3. Architecture

Two **pure, independently testable** functions in a new module, plus thin wiring in the engine.

### 3.1 `bot/mean_reversion.py` (new)

```
detect_range(klines: list[Kline], cfg: MRConfig) -> Range | None
    # Rolling window W (default 48). range = (hi, lo) of window.
    # Qualifies ONLY if:
    #   - both boundaries tested >= min_touches (default 2): >=N candles' high near hi
    #     AND >=N candles' low near lo (within touch_tol * range_width, default 0.12)
    #   - band_min <= range_width/mid <= band_max (default 0.02..0.16): not squeeze, not blowout
    # Returns Range(hi, lo, mid) or None. PURE — no state, no I/O.

mr_signal(klines, rng: Range, cfg: MRConfig) -> MRSignal | None
    # On the just-closed candle:
    #   - pos = (close - lo) / (hi - lo)
    #   - near top (pos >= 1 - decile, default 0.15) + wick-rejection -> SELL
    #   - near bottom (pos <= decile) + wick-rejection -> BUY
    #   - wick-rejection: candle poked past the boundary but closed back inside
    #   entry = close ; tp = mid ; sl = boundary ± sl_buf * range_width (default 0.5)
    # Returns MRSignal(side, entry, tp, sl) or None. PURE.
```

`Range`, `MRSignal`, `MRConfig` are small dataclasses. Both functions are deterministic
functions of their inputs — no dependency on `Trend`, no mutation. This is the isolation boundary:
consumers can be tested with hand-built kline fixtures.

### 3.2 Engine wiring (`bot/recommendation_engine.py`)

- After the existing regime classification (`_get_regime` → ascending/descending/neutral):
  - If MR enabled for this symbol **and** `detect_range()` returns a range → **MR mode**:
    - Suppress trend continuation signals (return the MR `Recommendation` from `mr_signal()`
      if one fires this candle, else no signal).
    - Reversal signal types remain unaffected (they are not the loss source).
  - Else → trend mode, unchanged.
- The MR `Recommendation` uses a new `RecommendationType.MEAN_REVERT_FADE` and carries
  entry/tp/sl from `mr_signal()`. Everything downstream is unchanged.

### 3.3 Range-invalidation (improvement #2)

Two independent protections, no new state:
- **Open position:** the SL already sits beyond the boundary, so a decisive break exits it.
- **New fades:** once price breaks out, `detect_range()` **naturally stops qualifying** within a
  few candles — the rolling window's boundaries are no longer both tested and/or width leaves the
  band — so no fresh fades fire into a developing breakout. This is emergent from the pure
  detector, not a separate stateful tracker. The spec's only requirement is that the detector's
  `min_touches`/`band_max` are strict enough that a broken range de-qualifies promptly (verified
  by a Gate C unit test: feed a breakout series, assert `detect_range` returns `None`).

### 3.4 Preset & routing

New preset `mr_fade` in `config/presets.py` carrying MR-appropriate risk/exit fields. The MR
`Recommendation` already carries explicit tp/sl (geometry-based), so the preset mainly supplies
sizing/leverage/cooldown knobs consistent with other presets. Routed through the **existing**
`order_manager` → `risk_manager` → `FakeOrder` path — no changes there.

---

## 4. Config & safety

- `enable_mean_reversion` (global, **default False**) in settings + `risk_config.json`.
- Per-symbol enable list (improvement #1): initial allow-list from evidence =
  **TIA, EIGEN, INJ, THETA, PEPE, SOL** (return/DD ≥ 1.0 and OOS-positive). **DOGE excluded.**
  **MEME probationary** (virtual-only until it proves out).
- All detector/exit params (`W, min_touches, touch_tol, band_min, band_max, decile, sl_buf`)
  exposed as settings/preset knobs with the validated defaults above.
- `max_sl_pct=8.0` guard still applies. Testnet-only; no live-mode path touched.

---

## 5. Validation gates (must pass IN ORDER before any live/testnet enablement)

- **Gate A — real backtester parity.** Wire `mr_signal` into `bot/backtester.py` and re-run the
  winning config with real sizing + fees + cooldowns across the allow-list symbols. The toy-sim
  edge (~+0.2%/trade, 7/8 OOS) must reproduce within a reasonable margin. This also produces the
  first honest **USDT** projection. (Chicken-and-egg: requires the generator implemented first —
  so this is the first post-implementation step, not a pre-code gate.)
- **Gate B — risk-throttled robustness.** Confirm the edge survives the real one-position-per-
  symbol + loss-streak cooldowns (may thin trade count; must stay net-positive & OOS-stable).
- **Gate C — unit tests.** `detect_range`/`mr_signal` covered with fixtures (range qualifies /
  rejects; both sides; wick-rejection; invalidation).
- **Gate D — testnet observation.** Enable on allow-list symbols on testnet for a set window;
  compare live MR fills vs backtest expectation before any scale-up. No live-money path.

---

## 6. Rejected alternatives

- **Naive fade (any extreme touch):** no edge (51% WR / ~1.0 payoff). Rejected by data.
- **Tight stop (sl_buf 0.25–0.35):** collapses the edge (+241 → +10/+15) and worsens drawdown.
  Rejected by tail-risk analysis.
- **Always-parallel / MR-only add-on coexistence:** keeps taking losing trend trades in ranges;
  the regime-switch is the whole point. Rejected.
- **New indicators (Bollinger/RSI/z-score bands):** unnecessary — swing/kline structure already
  carries the signal and is fully backtestable. Rejected (YAGNI).
- **Enable-all-symbols:** edge is robust but not uniform (DOGE negative, MEME thin). Rejected in
  favor of evidence-based per-symbol gating.

---

## 7. Touch points (files)

- **New:** `bot/mean_reversion.py` (detect_range, mr_signal, dataclasses); tests
  `tests/test_mean_reversion.py`.
- **Edit:** `bot/recommendation_engine.py` (regime-switch wiring + MR gate),
  `config/presets.py` (`mr_fade` preset), `config/settings.py` (MR flags/params),
  `bot/backtester.py` (route MR signal for Gate A), `bot/recommendation.py`
  (`MEAN_REVERT_FADE` type). Possibly `main.py` (decision-log entry for MR signals/invalidation).
- **No change:** `order_manager.py`, `risk_manager.py`, `fake_order.py` (reused as-is).

---

## 8. Risk flags

- Toy-sim ≠ real backtester until Gate A — do not project USDT before it.
- Testnet klines only; live regime may differ (mechanism is regime-driven, not testnet-specific,
  which mitigates but does not eliminate).
- High signal frequency (~2/day/symbol) — Gate B must confirm cooldowns don't erase the edge.
- Mild parameter data-snooping (winning config partly chosen on OOS) — mitigated by many configs
  passing, not a knife-edge; Gate A on the real engine is the final arbiter.
