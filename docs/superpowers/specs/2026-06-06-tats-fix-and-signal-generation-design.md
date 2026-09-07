# TATS Scenario Fix & hl_buy Signal Generation Bug — Design Spec

**Date:** 2026-06-06  
**Status:** Approved, ready to implement

---

## Background

TATS ("Took All The Shoes") is a scenario where the bot places real orders for any enabled symbol with a valid signal, as long as the deployable budget has not been consumed by another symbol's open order. Symbols that should not trade are excluded by explicit disabling in the registry — not by automatic quality gates inside the scenario logic.

Two bugs were found that violated this intent:

---

## Bug 1 — TATS Added Unintended Quality Gates

### Problem
The candidates loop in `main.py` applied three filters that the TATS design never intended:

**Gate A — weight=0 check (line 985):**
```python
if symbol_registry.get_weight(sym) == 0.0:
    continue
```
`disable()` in SymbolRegistry always sets both `_disabled` AND `weight=0`. So any properly auto-disabled symbol is already caught by `is_disabled()`. The weight=0 gate is redundant and incorrectly blocks symbols that were manually set to weight=0 without being explicitly disabled.

Under TATS, weight has meaning only when multiple symbols signal simultaneously — it determines proportional budget allocation (BGF fallback). It should NOT gate candidacy.

**Gate B — `is_tats_eligible()` check (lines 989–992):**
```python
if _active_scenario_name == "tats":
    _tats_locked = risk_cfg.get("locked_presets", {}).get(sym)
    if not virtual_tracker.is_tats_eligible(sym, locked_preset=_tats_locked):
        continue
```
`is_tats_eligible()` blocks symbols whose best preset's recent-window performance is deteriorating (Part A: sum below `tats_min_profit_usdt`; Part B: second-half drop > 50% of first half). This is a performance quality gate — exactly what TATS was NOT designed to do. Symbols that are performing badly should be explicitly disabled via the registry.

**Gate C — `is_virtual_only()` inside `_try_place_order` (lines 437–439):**
```python
if virtual_tracker.is_virtual_only(symbol):
    logger.info(f"[{symbol}] Virtual-only floor active — skipping real order")
    return 0.0
```
`is_virtual_only()` blocks real orders for symbols below a cumulative loss floor. Same intent as `is_tats_eligible()` — automatic quality suppression that contradicts TATS. Under TATS, explicit registry disabling is the only control.

### Fix
- Gate A: Skip for TATS (`if _active_scenario_name != "tats" and ...`)
- Gate B: Remove entirely from TATS path
- Gate C: Skip for TATS (`if _active_scenario_name != "tats" and ...`)

### What Is NOT Changed
- `is_disabled()` gate — kept, the authoritative explicit block
- `is_symbol_paused()` gate — kept
- `get_state() != IDLE` gate — kept (one real order per symbol at a time)
- `is_tats_eligible()` and `is_virtual_only()` functions — kept in code, called in non-TATS scenarios
- n==1 → full budget + bypass_pct_cap — already correct, unchanged
- n>1 → BGF proportional split using `risk_config.symbol_weights` as multiplier — already correct, unchanged

---

## Bug 2 — Signal Generation Disconnect for hl_buy / lh_sell Presets

### Problem
Several presets (`hl_buy_trail15`, `hl_buy_prox15_trail15`, `lh_sell_trail15`, `pre_confirm_trail15`, etc.) require `higher_low_buy=True` or `lower_high_sell=True` in `Settings` to generate their signal type. These flags default to `False` in the base `Settings` (loaded from environment).

The candidates loop gates on `get_best_recommendation()`, which uses the base-settings engine:
```python
# In Analyzer._refresh_recommendations():
self._best_recommendation = self._engine.generate(self._trend, self._current_price)
# self._engine was constructed with base_settings where higher_low_buy=False
```

So for MEMEUSDT (best preset: `hl_buy_trail15`) — the base engine never produces a signal → `_best_recommendation = None` → MEMEUSDT never enters candidates → **zero live orders despite valid backtest signals**.

Meanwhile, inside `_try_place_order`, the engine IS re-run with the correct preset flags:
```python
overrides = all_presets.get(preset_name, {})          # {'higher_low_buy': True, ...}
preset_settings = dataclasses.replace(settings, **overrides)
best = RecommendationEngine(preset_settings).generate(_trend, _preset_entry_px)
```

So the final placement engine is correct — only the candidacy gate is wrong.

### Fix
In the candidates loop, after the base-settings `get_best_recommendation()` returns `None`, fall back to running the engine with the symbol's best preset's overrides. The `Analyzer` class already has `get_recommendation_for_preset(overrides: dict)` for exactly this purpose (line 140–146 of `bot/analyzer.py`).

```python
_sym_az = analyzer if sym == symbol else analyzers.get(sym)
if _sym_az is None:
    continue
best_sym = _sym_az.get_best_recommendation()
if best_sym is None:
    _bp = virtual_tracker.best_preset(sym)
    _bp_ovr = all_presets.get(_bp or '', {})
    if _bp_ovr:
        best_sym = _sym_az.get_recommendation_for_preset(_bp_ovr)
if best_sym is None:
    continue
```

The `best_sym` value passing the gate is only used as:
1. A non-None sentinel confirming a signal exists
2. An entry-price fallback (`best.getEntryPrice()`) — only used if `_current_px == 0`, which never happens in practice

`_try_place_order` re-runs the engine with full preset settings at line 466 regardless, so no double-counting or incorrect signal is used.

---

## Confirmed Decisions (from user Q&A)

| Question | Answer |
|---|---|
| Should weight=0 gate remain for TATS? | No. Weight only matters for n>1 proportional allocation. |
| Should `is_virtual_only()` block real orders in TATS? | No. All non-explicitly-disabled symbols may place real orders. |
| For n>1 BGF fallback, use `risk_config.symbol_weights` as score multiplier? | Yes, this is correct proportional allocation. |
| Fix hl_buy signal generation bug in same pass? | Yes. |
| Can weights be set by drag-and-drop on Risk page? | Already implemented — no new work needed. |

---

## Implementation — Four Changes to `main.py`

### Change 1 — Weight gate (line 985)
```python
# Before:
if symbol_registry.get_weight(sym) == 0.0:
    continue

# After:
if _active_scenario_name != "tats" and symbol_registry.get_weight(sym) == 0.0:
    continue
```

### Change 2 — Remove `is_tats_eligible()` gate (lines 989–992)
```python
# DELETE:
if _active_scenario_name == "tats":
    _tats_locked = risk_cfg.get("locked_presets", {}).get(sym)
    if not virtual_tracker.is_tats_eligible(sym, locked_preset=_tats_locked):
        continue
```

### Change 3 — Preset-fallback for `best_sym is None` (replaces lines 993–998)
```python
# Before:
best_sym = (
    best_for_this if sym == symbol
    else (analyzers[sym].get_best_recommendation() if sym in analyzers else None)
)
if best_sym is None:
    continue

# After:
_sym_az = analyzer if sym == symbol else analyzers.get(sym)
if _sym_az is None:
    continue
best_sym = _sym_az.get_best_recommendation()
if best_sym is None:
    _bp = virtual_tracker.best_preset(sym)
    _bp_ovr = all_presets.get(_bp or '', {})
    if _bp_ovr:
        best_sym = _sym_az.get_recommendation_for_preset(_bp_ovr)
if best_sym is None:
    continue
```

### Change 4 — Skip `is_virtual_only()` for TATS (lines 437–439 in `_try_place_order`)
```python
# Before:
if virtual_tracker.is_virtual_only(symbol):
    logger.info(f"[{symbol}] Virtual-only floor active — skipping real order")
    return 0.0

# After:
if _active_scenario_name != "tats" and virtual_tracker.is_virtual_only(symbol):
    logger.info(f"[{symbol}] Virtual-only floor active — skipping real order")
    return 0.0
```

---

## Files Modified
- `main.py` — all four changes above

## Files NOT Modified
- `bot/virtual_tracker.py` — `is_tats_eligible()` and `is_virtual_only()` kept intact for non-TATS use
- `bot/analyzer.py` — `get_recommendation_for_preset()` already exists, no change needed
- `config/settings.py` — base settings unchanged
- Dashboard — drag-and-drop already implemented

---

## Testing Notes
After deploy, verify in logs:
- MEMEUSDT appears as BEST (not just CANDIDATE) when hl_buy trend structure is valid
- Symbols that previously failed `is_tats_eligible()` are now placing real orders
- n==1 TATS: full budget, bypass_pct_cap in log
- n>1 TATS: BGF proportional split in log
- Non-TATS scenario (BGF): weight=0 still blocks, `is_virtual_only()` still blocks
