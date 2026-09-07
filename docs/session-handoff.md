# Session Handoff — 2026-05-28

## Status: All work from this session is complete and deployed.

---

## What was done this session

### Items 1–4 and 7 (from previous approval)
1. ETHFIUSDT disabled in symbol_registry.json (consistent losses -$47 total)
2. APTUSDT lock removed from locked_presets
3. Trail/partial exits with negative PnL now count toward loss streak (commit 6392635)
4. Backtests run for TIAUSDT/THETAUSDT/INJUSDT/EIGENUSDT; scores updated (18.4% / 13.2% / 10.4% / 24.6%)
7. use_allocation_weighting=true, symbol weights rebalanced (TIAUSDT:20 top)

### Signal quality improvements (commit 21e480e)
Investigation saved to: `docs/2026-05-28-signal-quality-investigation.md`

**Proposal 1 — Hard alignment gate** (`bot/recommendation_engine.py`)
- RISING_BELOW_LAST_HIGH and LOWERING_ABOVE_LAST_LOW signals are now blocked when the parent trend explicitly opposes the signal direction
- Reversal types still fire freely
- Fixes: every BUY trade in the May 23-25 analysis lost (counter-trend continuations)

**Proposal 2 — Minimum precision floor** (`config/settings.py` + `bot/recommendation_engine.py`)
- New Setting: `min_precision_score` (default 0.0 = disabled)
- Per-preset tuning knob — set to e.g. 0.20 on presets where low-precision entries lose

**Proposal 3 — Zone SL cooldown** (`main.py` + `bot/backtester.py` + `config/settings.py`)
- New Settings: `zone_sl_max` (default 0 = disabled), `zone_sl_cooldown_candles` (default 16)
- After N consecutive SL hits at same SL level → block that side for N candles
- Fixes: DOGEUSDT re-entered same losing zone 5 times (-$8.95)
- Enable for DOGE: set `zone_sl_max: 2` in the `trail_15_from_15_d1` preset on the server

**Proposal 4 — Per-symbol Settings overrides** (`main.py` + server `risk_config.json`)
- `risk_config.json` now has `"per_symbol_settings": {"INJUSDT": {"max_profit_pct": 5.0}}`
- INJUSDT had 75 signals blocked at 4.3-5.0% TP; now those pass

**Proposal 5** — Already implemented (max_order_notional_usdt=500 in order_executor.py)

---

## Current server state
- Bot running, started 07:48 UTC 2026-05-28
- risk_config.json: per_symbol_settings added, use_allocation_weighting=true
- symbol_registry.json: ETHFIUSDT disabled
- locked_presets: REZUSDT + DOGEUSDT only (APTUSDT removed)

---

## Suggested next actions (for next session)

1. **Enable zone_sl_max on DOGE preset** — set `zone_sl_max: 2` in the `trail_15_from_15_d1` preset config on the server. DOGE is the symbol this was designed for.

2. **Monitor alignment gate effectiveness** — check decision log for how many BUY signals are now being dropped by the alignment gate (look for signals that no longer appear — the gate drops them silently before they reach decision logging). Consider adding a `skip_parent_alignment` decision log entry for visibility.

3. **Tune min_precision_score** — once we have a few days of live data post-alignment gate, analyze what precision scores the remaining winning vs losing trades have, then set a floor.

4. **Add decision log entry for alignment gate rejections** — currently the alignment gate drops candidates silently inside the engine (they never reach the decision logger in main.py). Adding a log entry would make it observable how many signals it's blocking per symbol.

5. **Fix pre-existing test failures** (from 2026-05-20 audit):
   - test_can_open_passes_with_zero_size
   - 3 leverage tests
   - test_perf_cache_ttl
   - 2 virtual_order_simulator tests (method rename)

---

## Notes
- All documentation updated (CLAUDE_NOTES.md, TODO.md, FEATURES.md) by Librarian
- Investigation doc: `docs/2026-05-28-signal-quality-investigation.md`
- No open questions requiring user input
