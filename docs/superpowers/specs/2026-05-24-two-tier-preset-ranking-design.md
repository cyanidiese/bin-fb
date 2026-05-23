# Two-Tier Preset Ranking Design

**Date:** 2026-05-24  
**Status:** Approved for implementation

---

## Problem

The current preset ranking system uses `seeded_winning_usdt` (derived from backtest `total_profit_pct`) as the ranking score until a preset accumulates `_MIN_TRADES = 8` real+virtual trades. After 8 trades it switches to `total_winning_usdt` (live accumulated P&L).

This causes a critical failure mode demonstrated on TIAUSDT:
- `trail_15_from_15` has a backtest profit of +69.4% over 157 historical trades → seeded score = **$694**
- `pre_confirm_prox15_trail15` has backtest profit of +20.6% → seeded score = **$206**
- In live trading: `trail_15_from_15` has 4 real trades at **-6.3% / -$14** (25% win rate)
- In live trading: `pre_confirm_prox15_trail15` has 5 virtual trades at **+29.9% / +$66** (60% win rate)
- Because neither has 8 trades, the $694 seed dominates — the losing preset keeps real orders

A secondary issue: even if `_MIN_TRADES` were simply lowered to 3, a never-traded preset with a $500 seed would still outrank a live-proven +$66 challenger, since all presets compete in the same pool regardless of whether they have live evidence.

---

## Design

### Core principle

Backtest seed = **bootstrapping only**. Once a preset accumulates `N` real+virtual trades, its live accumulated P&L determines its rank. A live-proven preset (≥ N trades) always beats a backtest-only preset (< N trades), regardless of seed magnitude.

### Two-tier scoring

Replace the single numeric score with a tuple `(tier, value)`. Python tuple comparison means Tier 1 always beats Tier 2:

```python
def _score(stats: dict, min_trades: int) -> tuple:
    count = stats.get("trade_count", 0)
    if count >= min_trades:
        return (1, stats.get("total_winning_usdt", 0.0))   # Tier 1: live-proven
    return (0, stats.get("seeded_winning_usdt", 0.0))       # Tier 2: backtest-only
```

Applied to `best_preset()`, `get_efficiency_score()`, and `get_preset_efficiency()`.

### Configurable threshold per symbol

The threshold `N` is configurable globally with per-symbol overrides, stored in `risk_config.json`:

```json
{
  "min_trades_for_ranking": 3,
  "min_trades_for_ranking_per_symbol": {}
}
```

- **Global default:** 3 (enough signal to establish directional confidence)
- **Per-symbol override:** e.g. `{"TIAUSDT": 5}` for symbols where more evidence is preferred
- Lookup: per-symbol value if set, otherwise global default

`N = 3` means: after 3 combined real+virtual trades, a preset's live P&L determines whether it can challenge or hold the top rank.

---

## Files Changed

### `config/risk_config.py`
- Add to `DEFAULT_CONFIG`:
  ```python
  "min_trades_for_ranking": 3,
  "min_trades_for_ranking_per_symbol": {},
  ```
- Add helper method:
  ```python
  def get_min_trades(symbol: str) -> int:
      per_sym = cfg.get("min_trades_for_ranking_per_symbol", {})
      return per_sym.get(symbol, cfg.get("min_trades_for_ranking", 3))
  ```

### `bot/virtual_tracker.py`
- Remove module-level `_MIN_TRADES = 8` constant
- Add `get_min_trades: Callable[[str], int]` parameter to `__init__`
- Store as `self._get_min_trades = get_min_trades`
- Update `_score()` inside `best_preset()` to use `self._get_min_trades(symbol)` and return a tuple
- Update `get_efficiency_score(symbol)` same way (returns `float` — take `score[1]` from tuple or keep max of Tier 1 first)
- Update `get_preset_efficiency(symbol, preset_name)` same way

### `main.py`
- Pass callable when constructing both `VirtualTracker` instances:
  ```python
  get_min_trades=risk_config.get_min_trades
  ```

### `dashboard/lib/risk-types.ts`
- Add to `RiskConfig`:
  ```typescript
  min_trades_for_ranking?: number;
  min_trades_for_ranking_per_symbol?: Record<string, number>;
  ```

### `dashboard/app/api/risk/route.ts`
- Include both new fields in GET defaults and POST passthrough

### `dashboard/app/risk/page.tsx`
- New "Preset Ranking" config section containing:
  - **Global min trades input** — number field, label "Min trades before live ranking", default 3, min 1
  - **Per-symbol override table** — symbol column + number input column; add/remove rows; same visual pattern as per-symbol leverage section

---

## Behaviour After Change

| Scenario | Before | After |
|---|---|---|
| No preset has ≥ 3 trades | Seed dominates (correct — bootstrapping) | Same |
| Champion has ≥ 3 trades, no challenger does | Champion uses live score | Same — champion still holds |
| Challenger has ≥ 3 trades, better live P&L than champion | Champion keeps rank (seed wins) | Challenger promoted |
| Never-traded preset vs live-proven challenger | Seed could win if high enough | Live-proven always wins |
| Both have ≥ 3 trades, champion winning | No change needed | Champion keeps rank |

---

## What Does Not Change

- `record_closed_trade` — both real and virtual trades still increment `trade_count`
- Seeding logic — `seed_from_backtest` unchanged; seed still written at startup
- Persistence format — same JSON keys in `preset_efficiency_{mode}.json` and `risk_config.json`
- All other bot logic, order placement, virtual pool mechanics

---

## Trade-offs

**Risk of N = 3:** Three trades can still be noisy on volatile symbols. A preset could hit 3 early losses by bad timing and lose rank prematurely. The per-symbol override exists specifically to raise the threshold for symbols where this is a concern.

**Why not profit% per trade instead of total USDT?** Total USDT is proportional to profit% within the same symbol since virtual pools start from the same balance. It also naturally rewards presets that are both profitable AND active (more trades = more opportunity captured). Can be revisited later.
