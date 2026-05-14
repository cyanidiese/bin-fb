# Leverage Scenarios Implementation Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single hardcoded cross-symbol leverage progression with a pluggable Scenario system supporting three strategies, switchable at runtime with no bot restart required.

**Architecture:** A new `bot/leverage_scenario.py` module defines a `LeverageScenario` protocol and three concrete implementations behind a factory. A `"scenario"` key in `risk_config.json` selects the active scenario. The main loop hot-reloads the scenario object when the config field changes. The Risk page gets a scenario dropdown; the Cross-Symbol Comparison widget gets scenario tabs for what-if analysis.

**Tech Stack:** Python 3.12 (bot), Next.js 15 / TypeScript (dashboard), JSON persistence (atomic tmp→replace writes).

---

## 1. Scenarios

### 1.1 Default — Cross-Symbol Progression (`"default"`)

Current behaviour, unchanged. A single **global level** (integer 1 → `max_level`) is shared by all active symbols. The level advances when every symbol has at least one closed real order recorded at the current level.

```
actual_lev = min(global_level, max_policy_lev, bracket_max)
```

Persistence: `data/leverage_state_default_{mode}.json`

### 1.2 Allocation — Per-Symbol Independent Progression (`"allocation"`)

Each symbol tracks its own level independently. A symbol's level advances when *that symbol* completes a real order at its current level. Symbols never wait for each other.

```
actual_lev = min(symbol_level[symbol], max_policy_lev, bracket_max)
```

The per-symbol level starts at 1 when a symbol is first seen. On scenario switch from Default, each symbol inherits `max(1, global_level - 1)` so it does not lose progress abruptly — but must still complete one order to advance further.

Persistence: `data/leverage_state_allocation_{mode}.json`

State file schema:
```json
{
  "symbol_levels": { "BTCUSDT": 3, "ETHUSDT": 2 },
  "completed": { "BTCUSDT": [1, 2], "ETHUSDT": [1] }
}
```

### 1.3 First Has the Most — Score-Based, No Gate (`"first_has_most"`)

No level tracking. Each symbol's leverage is computed immediately from its efficiency score (from `VirtualTracker.get_efficiency_score(symbol)`). More efficient symbols naturally earn higher leverage.

```
raw_lev    = base_leverage + floor(score × (max_policy_lev − base_leverage))
actual_lev = min(max(base_leverage, raw_lev), max_policy_lev, bracket_max)
```

A symbol with score 0.0 gets `base_leverage`; score 1.0 gets `max_policy_lev`. No cross-symbol dependency at all.

`record_closed()` is a no-op. No persistence file.

---

## 2. `bot/leverage_scenario.py` — New File

### 2.1 Protocol

```python
class LeverageScenario(Protocol):
    name: str

    def get_leverage(
        self, symbol: str, score: float,
        base: int, max_policy: int, bracket_max: int
    ) -> int: ...

    def record_closed(self, symbol: str, leverage: int) -> None: ...
    def add_symbol(self, symbol: str) -> None: ...
    def remove_symbol(self, symbol: str) -> None: ...
    def reset_for_mode(self, new_mode: str, data_path: Path) -> None: ...
```

### 2.2 Factory

```python
def create_scenario(
    name: str,
    mode: str,
    active_symbols: list[str],
    data_path: Path,
    max_level: int,
) -> LeverageScenario:
    if name == "allocation":
        return AllocationScenario(mode, active_symbols, data_path, max_level)
    elif name == "first_has_most":
        return FirstHasMostScenario()
    else:
        return DefaultScenario(mode, active_symbols, data_path, max_level)
```

### 2.3 DefaultScenario

Wraps the existing `LeverageTracker` logic (may inline or delegate — both are valid). `get_leverage` ignores `score`. State file: `leverage_state_default_{mode}.json`.

### 2.4 AllocationScenario

Maintains `_symbol_levels: dict[str, int]` and `_completed: dict[str, set[int]]`. Advancement check is per-symbol (single symbol, not all symbols). `get_leverage` ignores `score`. State file: `leverage_state_allocation_{mode}.json`.

When a symbol is added via `add_symbol()`, its starting level is `1`. On `reset_for_mode()`, reload from the mode-specific file.

### 2.5 FirstHasMostScenario

All methods except `get_leverage` are no-ops. `get_leverage` applies the score formula above. No file I/O.

---

## 3. Config Changes

### 3.1 `config/risk_config.py`

Add to `DEFAULT_CONFIG`:
```python
"scenario": "default",
```

### 3.2 `dashboard/app/api/risk/route.ts`

Add to the TypeScript `DEFAULT_CONFIG`:
```typescript
scenario: 'default',
```

---

## 4. `main.py` Changes

### 4.1 Startup

Replace `LeverageTracker` instantiation with:
```python
_active_scenario_name: str = ""
scenario: LeverageScenario  # set in _build_scenario() below

def _build_scenario(name: str) -> LeverageScenario:
    data_path = _PROJECT_ROOT / "data" / f"leverage_state_{name}_{mode}.json"
    return create_scenario(
        name=name,
        mode=mode,
        active_symbols=symbol_registry.get_symbols(),
        data_path=data_path,
        max_level=risk_cfg.get("max_leverage_level", 5),
    )
```

### 4.2 Hot-Reload on Each Candle

At the top of the per-candle order check (before `try_open_order` is called for any symbol):
```python
scenario_name = risk_cfg.get("scenario", "default")
if scenario_name != _active_scenario_name:
    scenario = _build_scenario(scenario_name)
    _active_scenario_name = scenario_name
    logger.info(f"Scenario switched to: {scenario_name}")
```

This requires `risk_cfg` to be refreshed each candle. If it isn't already, add `risk_cfg = load_risk_config()` at the same point.

### 4.3 Leverage Computation (replacing lines 267–270)

```python
eff_score  = virtual_tracker.get_efficiency_score(symbol)
base_lev   = risk_cfg.get("base_leverage", 1)
max_policy_lev = risk_cfg.get("max_leverage_level", 5)
actual_lev = scenario.get_leverage(symbol, eff_score, base_lev, max_policy_lev, bracket_max)
if actual_lev <= 0:
    actual_lev = 1
```

### 4.4 On Order Close (replacing line 417)

```python
scenario.record_closed(c["symbol"], c.get("leverage", 1))
```

### 4.5 Mode Switch

```python
# inside on_switch_mode():
data_path = _PROJECT_ROOT / "data" / f"leverage_state_{_active_scenario_name}_{target_mode}.json"
scenario.reset_for_mode(target_mode, data_path)
```

### 4.6 Symbol Registry Changes

When a symbol is added or removed dynamically:
```python
scenario.add_symbol(symbol)    # or
scenario.remove_symbol(symbol)
```

---

## 5. `risk_state.json` — Extended Fields

The risk state writer must include:
```json
{
  "scenario": "default",
  "leverage_level": 3,
  "per_symbol": {
    "BTCUSDT": {
      "leverage_level": 3,
      "performance_score": 0.82,
      ...
    }
  }
}
```

- `leverage_level` (top-level) = global level for Default; meaningless for others but kept for backward compat.
- `per_symbol[sym].leverage_level` = per-symbol level for Allocation; for Default it is the same as the global; for First Has the Most it is the score-derived value.

The risk state is written by `bot/risk_manager.py` (or wherever it currently writes `risk_state.json`). That writer needs access to `scenario` to read per-symbol levels.

---

## 6. Dashboard — Risk Page

### 6.1 Scenario Selector (Leverage Controls section, above base_leverage)

A `<select>` dropdown with three options, saved immediately on change (same `handleSave` pattern used elsewhere):

```
Default (cross-symbol)   — All symbols advance together
Allocation               — Each symbol advances independently
First Has the Most       — Efficiency score sets leverage immediately
```

Short description text under the dropdown, rendered based on selected value.

No migration modal or confirmation needed — the bot picks up the change on the next candle.

---

## 7. Dashboard — Cross-Symbol Comparison Widget

### 7.1 Scenario Tabs

A 3-tab row above the existing mode tabs (Total USDT / Side-by-side / Best per symbol):

```
[ Default ] [ Allocation ] [ First Has the Most ]
```

Default selection: whichever scenario is currently active in config. Switching tabs is local to the widget — it does NOT change the bot's active scenario. It is purely for what-if profit projection.

### 7.2 `computeSizing` Per Scenario

**Default tab:**
```typescript
function computeSizingDefault(symbol, balance, config, riskState) {
  // Use the per-symbol leverage_level written by the bot into risk_state.json
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage_level ?? 1)
  // Margin: equal share of deployable pool (consistent with how the bot sizes)
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = Object.keys(riskState?.per_symbol ?? {}).length || 1
  const margin = pool / numSymbols
  return { margin, lev }
}
```

**Allocation tab** (existing `computeSizing` logic, already correct):
```typescript
function computeSizingAllocation(symbol, balance, config, riskState) {
  // Already implemented as computeSizing() in the widget
  // Uses weight-based pool split + score-based leverage
}
```

**First Has the Most tab:**
```typescript
function computeSizingFirstHasMost(symbol, balance, config, riskState) {
  const score = riskState?.per_symbol[symbol]?.performance_score ?? 0
  const base = config.base_leverage ?? 1
  const maxLev = config.max_leverage_level ?? 5
  const lev = Math.max(base, Math.min(maxLev, base + Math.floor(score * (maxLev - base))))
  // Margin: equal share of deployable pool
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = Object.keys(riskState?.per_symbol ?? {}).length || 1
  const margin = pool / numSymbols
  return { margin, lev }
}
```

### 7.3 Profit Calculation

Unchanged formula: `(total_profit_pct / 100) × margin × lev`. Only `margin` and `lev` change per scenario tab.

### 7.4 Widget State

```typescript
const [scenarioTab, setScenarioTab] = useState<'default' | 'allocation' | 'first_has_most'>(
  (config?.scenario as any) ?? 'default'
)
```

`config` is already loaded by the widget. Initialize from the active scenario, allow local tab switching.

---

## 8. Files Touched

| File | Change |
|---|---|
| `bot/leverage_scenario.py` | **Create** — protocol + 3 implementations + factory |
| `bot/leverage_tracker.py` | **Keep as-is** (DefaultScenario may delegate to it) |
| `config/risk_config.py` | Add `"scenario": "default"` to DEFAULT_CONFIG |
| `main.py` | Replace `LeverageTracker` with scenario factory; hot-reload on candle |
| `bot/risk_manager.py` | Pass `scenario` to risk_state writer for per-symbol leverage_level |
| `dashboard/app/api/risk/route.ts` | Add `scenario: 'default'` to TS defaults |
| `dashboard/app/risk/page.tsx` | Add scenario dropdown in Leverage Controls section |
| `dashboard/components/CrossSymbolComparison.tsx` | Add scenario tabs + 3 sizing variants |
| `tests/test_leverage_scenario.py` | **Create** — unit tests for all three scenario classes |

---

## 9. Error Handling & Edge Cases

- **Unknown scenario name in config:** factory falls back to `DefaultScenario` and logs a warning.
- **Score is None / not yet computed:** `get_efficiency_score` returns 0.0 if no data; First Has the Most gives `base_leverage` (safe minimum).
- **Switching from Allocation to Default:** existing per-symbol progress is retained in the Allocation state file; it is reloaded if the user switches back.
- **Switching from Default (level 3) to Allocation:** each symbol inherits `max(1, global_level - 1)` = 2, must complete one order to reach 3. This prevents instant high leverage on switch but avoids starting from scratch.
- **Switching to First Has the Most mid-session:** takes effect on the next candle; any currently open orders are unaffected (they keep their placed leverage).
- **No symbols active:** all scenarios return `base_leverage` when symbol list is empty.
