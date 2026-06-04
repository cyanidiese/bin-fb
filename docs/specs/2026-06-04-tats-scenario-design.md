# TATS Scenario Design Spec — "Took All The Shoes"

**Date:** 2026-06-04  
**Status:** Implemented — pending activation (set `"scenario": "tats"` in `risk_config.json`)

---

## 1. Overview

TATS ("Took All The Shoes") is a new scenario built on top of BGF (Best Gets First) with two important differences:

1. **Profitability gate** — only symbols whose best preset is profitable (or still in the seed phase) are allowed to place real orders. Proven losers are demoted to virtual-only trading.
2. **Full-budget single-signal rule** — when exactly one eligible symbol has a valid signal at candle close, it gets the entire deployable budget. When multiple eligible symbols signal simultaneously, allocation falls back to BGF proportional caps.

If a symbol had a valid signal but budget was exhausted: it simply waits. On the next candle, it re-evaluates naturally — if it has a signal and budget is available, it enters. No priority, no queue, no memory of the previous denial.

Virtual weight maintenance continues unchanged — `VirtualOrderSimulator` runs for all weight=1 symbols regardless of scenario.

---

## 2. Motivation

BGF already gives proportional advantage to the best scorer. The problem it doesn't solve: **on candles where only one symbol is ready to trade, it's still capped at its BGF fraction of the budget**, leaving capital idle. TATS converts that idle capital into an opportunity — the single ready symbol takes all of it.

The profitability gate enforces a hygiene rule: symbols that have proven consistently unprofitable in live trading stop consuming real capital. They continue running virtually so their efficiency scores keep updating.

---

## 3. Definitions

| Term | Meaning |
|---|---|
| **Eligible symbol** | Weight=1, not disabled, not paused, IDLE order state, and passes the TATS profitability gate |
| **Profitability gate** | Passed if: (a) symbol is still in seed phase (trade_count < min_trades_for_ranking), OR (b) best preset recent-window score ≥ `tats_min_profit_usdt` AND not degrading |
| **Single-signal candle** | Exactly 1 eligible symbol has a valid recommendation at candle close |
| **Multi-signal candle** | 2+ eligible symbols have valid recommendations simultaneously |

---

## 4. Allocation Rules

### 4.1 Single-signal candle
- One eligible symbol → receives `deployable` budget (= total balance minus reserve floor %)
- `trade_cap = deployable` and `bypass_pct_cap = True` are passed to `_try_place_order`
- `max_trade_pct` is **bypassed** in single-signal mode — the full deployable is the symbol's budget
- `min_balance_pct` reserve is still enforced (by `get_deployable_budget()`)

### 4.2 Multi-signal candle
- Same logic as BGF: proportional caps derived from efficiency scores
- `sym_cap = deployable × score / total_score` per symbol
- Candidates are sorted by score descending (same as BGF, no queue priority)

### 4.3 Budget-exhausted symbols
- If a symbol had a valid signal but `remaining ≤ 0` when its turn came, it gets no special treatment
- On the next candle it re-evaluates naturally: if it has a signal and budget is available, it enters
- No queue, no priority boost, no memory of the previous denial

---

## 5. Profitability Gate Detail

The gate must distinguish "currently profitable" from "was profitable but is now degrading." A flat threshold on cumulative score fails this — a symbol that made $50 early and is now losing $30 looks positive on total, but its trend is declining.

### 5.1 Two-part check (both must pass for Tier-1 eligibility)

**Part A — Recent window positive:**  
The best preset's recent-window score (sum of last `ranking_window_size` trades) must be ≥ `tats_min_profit_usdt`.  
This inherently uses *recent* performance, not cumulative total, so past wins don't mask present losses.

**Part B — Not degrading:**  
Split the recent window in half. Compare `second_half_sum` to `first_half_sum`.  
The symbol is degrading if: `second_half_sum < first_half_sum - abs(first_half_sum) × tats_degradation_max_drop_pct / 100`  
Default `tats_degradation_max_drop_pct = 50` — the second half of history can drop by up to 50% relative to the first half before the symbol is considered degrading.

**Example with window_size = 10:**
- First 5 trades: +$20 total. Second 5 trades: +$8 total. Drop = (20−8)/20 = 60% → degrading (>50%), excluded.
- First 5 trades: +$20 total. Second 5 trades: +$12 total. Drop = 40% → acceptable, eligible.
- First 5 trades: +$20 total. Second 5 trades: −$2 total. Drop = 110% → degrading, excluded.
- First 5 trades: +$20 total. Second 5 trades: +$25 total → improving, eligible.

The degradation check only runs when the window has ≥ 4 trades (need at least 2 per half). Fewer trades → skip Part B, rely on Part A alone.

### 5.2 Tier-0 symbols (seed phase) — BGF fallback

`trade_count < min_trades_for_ranking` → **BGF fallback**: symbol is included in the candidates pool (no gate applied) and receives proportional BGF allocation. It cannot trigger the single-signal full-budget rule alone — if any non-seed-phase candidate is present, normal TATS allocation applies to the pool as a whole.

If the ONLY signaling symbols are Tier-0 (all seed-phase), the pool behaves as pure BGF for that candle.

### 5.3 Locked preset gate

For symbols with a `locked_presets` entry, the gate must evaluate the **locked preset's performance**, not the best preset overall. Reason: the locked preset is what actually trades — a high-scoring unused preset cannot make an underperforming locked preset eligible.

- If the locked preset is **Tier-0** (trade_count < min_trades_for_ranking): BGF fallback (same as any seed-phase symbol)
- If the locked preset is **Tier-1**: apply Part A and Part B using the locked preset's stats

`is_tats_eligible` receives an optional `locked_preset: str | None` parameter. When provided, it uses that preset's stats instead of the best-overall.

### 5.4 Config keys

| Key | Default | Meaning |
|---|---|---|
| `tats_min_profit_usdt` | `0.0` | Min recent-window sum for eligibility (Part A). Recommended: 0.0 — the degradation check does the real filtering. |
| `tats_degradation_max_drop_pct` | `50.0` | Max % drop from first half to second half before "degrading" (Part B). Set to 0 to disable. |

**Rationale for `tats_min_profit_usdt = 0.0`:** With current live data and 50% degradation tolerance, the gate excludes all declining symbols (APTUSDT −194%, MEMEUSDT −116%, REZUSDT −109%, THETAUSDT −88%) even at threshold 0. The degradation check is the real filter; raising the threshold changes nothing for current data. Eligible symbols at these defaults: TIAUSDT, 1000PEPEUSDT, DOGEUSDT, ETHFIUSDT.

### 5.5 Interaction with other floors

- `virtual_only_floor` (−$5): separate, harder floor applied before TATS — still active, unchanged
- `locked_presets`: gate evaluates the locked preset specifically (Section 5.3)

### 5.6 `is_tats_eligible` implementation sketch

```python
def is_tats_eligible(self, symbol: str, locked_preset: str | None = None) -> bool:
    cfg = load_risk_config()
    min_trades = self._get_min_trades(symbol)
    window_size = int(cfg.get("ranking_window_size", 10))
    min_profit = float(cfg.get("tats_min_profit_usdt", 0.0))
    max_drop_pct = float(cfg.get("tats_degradation_max_drop_pct", 50.0))

    presets = self._efficiency.get(symbol, {})
    if not presets:
        return True  # no data → BGF fallback (eligible)

    # For locked symbols, evaluate the locked preset specifically
    if locked_preset and locked_preset in presets:
        check_stats = presets[locked_preset]
        tier, score = _score(check_stats, min_trades, window_size)
    else:
        # Use best preset by score
        best_name = max(presets, key=lambda n: _score(presets[n], min_trades, window_size))
        check_stats = presets[best_name]
        tier, score = _score(check_stats, min_trades, window_size)

    if tier == 0:
        return True  # seed phase → BGF fallback

    # Part A: recent window must be profitable enough
    if score < min_profit:
        return False

    # Part B: degradation check (only when window has enough data)
    if max_drop_pct > 0:
        recent = check_stats.get("recent_trades", [])[-window_size:]
        if len(recent) >= 4:
            mid = len(recent) // 2
            first_half = sum(recent[:mid])
            second_half = sum(recent[mid:])
            allowed_floor = first_half - abs(first_half) * max_drop_pct / 100.0
            if second_half < allowed_floor:
                return False

    return True
```

---

## 7. Config Keys

| Key | Default | Description |
|---|---|---|
| `scenario` | `"best_gets_first"` | Set to `"tats"` to activate |
| `tats_min_profit_usdt` | `0.0` | Min recent-window sum required. Seed-phase symbols exempt. |
| `tats_degradation_max_drop_pct` | `50.0` | Max % drop from first half to second half of recent window before "degrading". `0` disables degradation check. |

All existing keys (`bgf_top_n`, `max_trade_pct`, `min_balance_pct`, `virtual_only_floor`, etc.) continue to apply unchanged.

---

## 8. Files to Change

| File | Change |
|---|---|
| `config/risk_config.py` | Add `"tats_min_profit_usdt": 0.0` and `"tats_degradation_max_drop_pct": 50.0` to `DEFAULT_CONFIG` |
| `bot/leverage_scenario.py` | Add `TATSScenario` class (same leverage formula as BGF, `name="tats"`, `uses_weight_allocation=False`); register in `create_scenario` |
| `bot/virtual_tracker.py` | Add `is_tats_eligible(symbol, locked_preset=None) → bool` method |
| `main.py` | (1) TATS eligibility filter in candidates building loop (passing locked preset); (2) unchanged score-descending sort; (3) new TATS execution branch; (4) `bypass_pct_cap=True` in single-signal `_try_place_order` call |
| `main.py` `_try_place_order` | Add `bypass_pct_cap: bool = False` parameter; skip `max_trade_pct` check when True |

No changes to: `VirtualOrderSimulator`, `RiskManager`, `WeightRebalancer`, backtester, dashboard.

---

## 9. Implementation Plan (Task Breakdown)

### T1 — `config/risk_config.py`
Add `"tats_min_profit_usdt": 0.0` and `"tats_degradation_max_drop_pct": 50.0` to `DEFAULT_CONFIG`.

### T2 — `bot/leverage_scenario.py`
Add `TATSScenario` (identical leverage formula to `BestGetsFirstScenario`, different `name`). Register in `create_scenario`.

### T3 — `bot/virtual_tracker.py`
Add `is_tats_eligible(symbol: str, locked_preset: str | None = None) -> bool` per Section 5.6.
- If `locked_preset` given and present in `_efficiency`: evaluate that preset's stats
- Else: evaluate best preset's stats
- Tier-0 → True (BGF fallback); Tier-1 → Part A (score ≥ `tats_min_profit_usdt`) and Part B (degradation check)

### T4 — `main.py` `_try_place_order` signature
Add `bypass_pct_cap: bool = False` parameter. Inside the `max_trade_pct` block:
```python
_max_trade_pct = float(risk_cfg.get("max_trade_pct", 0.0))
if _max_trade_pct > 0 and not bypass_pct_cap:   # ← add `and not bypass_pct_cap`
    ...
```

### T5 — `main.py` candidates loop
After `if order_executor.get_state(sym) != OrderState.IDLE: continue`, add:
```python
if _active_scenario_name == "tats":
    _locked = risk_cfg.get("locked_presets", {}).get(sym)
    if not virtual_tracker.is_tats_eligible(sym, locked_preset=_locked):
        continue
```

### T6 — `main.py` sort
No change — standard score-descending sort already applies. TATS uses the same ordering as BGF.

### T7 — `main.py` execution branch
Change the existing `if/else` at the execution branch:
```python
if scenario.uses_weight_allocation:
    ...
elif _active_scenario_name == "tats":
    # TATS execution block (see Section 10)
else:
    # BGF (unchanged)
    ...
```

---

## 10. TATS Execution Block (T7 detail)

```python
elif _active_scenario_name == "tats":
    deployable = risk_manager.get_deployable_budget()
    n = len(candidates)

    if n == 0:
        virtual_order_simulator.set_candle_alloc_context(False, {})
    elif n == 1:
        # Single eligible signal → full deployable budget, bypass max_trade_pct
        sym, best, sym_s, _ = candidates[0]
        virtual_order_simulator.set_candle_alloc_context(False, {sym: 1.0})
        await _try_place_order(sym, best, sym_s, deployable, candle_ts,
                               trade_cap=deployable, bypass_pct_cap=True)
    else:
        # Multiple eligible signals → BGF proportional allocation
        total_score = sum(max(0.0, s) for _, _, _, s in candidates)
        bgf_fractions = {
            sym: (max(0.0, s) / total_score if total_score > 0 else 1.0 / n)
            for sym, _, _, s in candidates
        }
        virtual_order_simulator.set_candle_alloc_context(False, bgf_fractions)
        deployed = 0.0
        for sym, best, sym_s, score in candidates:
            remaining = max(0.0, deployable - deployed)
            if remaining <= 0:
                break
            sym_cap = (
                deployable * max(0.0, score) / total_score
                if total_score > 0
                else deployable / n
            )
            if sym_cap <= 0:
                continue
            used = await _try_place_order(sym, best, sym_s, remaining, candle_ts,
                                          trade_cap=sym_cap)
            deployed += used
```

---

## 11. Resolved Decisions

1. **`tats_min_profit_usdt` = `0.0`** — degradation check does the real filtering. With current live data + 50% degradation tolerance, eligible set is: TIAUSDT, 1000PEPEUSDT, DOGEUSDT, ETHFIUSDT.
2. **Seed phase** → BGF fallback. Tier-0 symbols participate as normal BGF candidates (no gate). Only Tier-1 symbols are subject to profitability + degradation gate.
3. **Locked presets** → gate checks locked preset specifically. If locked preset is Tier-0, BGF fallback. If Tier-1, standard gate applies to that preset's stats.
4. **`max_trade_pct` in single-signal mode** → **bypassed**. Full deployable is the budget. Reserve floor (`min_balance_pct`) is still protected by `get_deployable_budget()`.
5. **Activation** — TBD by user after implementation is verified locally.

## 12. Remaining Open Question

**`tats_degradation_max_drop_pct` when `first_half ≤ 0`:**  
The formula `abs(first_half) × 50%` gives a very small tolerance when first_half is near zero or negative. However, Part A already requires `score ≥ 0` for the full window (first_half + second_half). If first_half is negative, second_half must be sufficiently positive for Part A to pass, which implicitly constrains the degradation anyway. No special handling needed — the two-part check is self-consistent.

---

## 13. What TATS Does NOT Change

- Leverage formula (same score-derived formula as BGF)
- `virtual_only_floor` hard stop (still active, separate from profitability gate)
- `locked_presets` (still applies; locked symbol still must pass profitability gate)
- `VirtualOrderSimulator` (runs for all weight=1 symbols regardless of scenario)
- Backtest engine (no backtest changes needed)
- Dashboard (no UI changes needed; scenario name will show as "tats" in existing scenario display)
