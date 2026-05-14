# Leverage Scenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Review cadence (per AGENTS.md):** Skip per-task spec/quality review loops. Run one final review after ALL tasks are complete.

**Goal:** Replace the single hardcoded `LeverageTracker` with a pluggable Scenario system (Default, Allocation, First Has the Most) switchable at runtime with no bot restart.

**Architecture:** New `bot/leverage_scenario.py` defines a protocol + three concrete classes + factory. `main.py` hot-reloads the active scenario from `risk_config.json` on each candle. Dashboard Risk page gains a scenario dropdown; Cross-Symbol Comparison gains scenario tabs for what-if projection.

**Tech Stack:** Python 3.12 (bot), Next.js 15 / TypeScript (dashboard), JSON persistence (atomic tmp→replace).

**Spec:** `docs/superpowers/specs/2026-05-13-scenarios-design.md`

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `bot/leverage_scenario.py` | **Create** | Protocol + 3 scenario classes + factory |
| `tests/test_leverage_scenario.py` | **Create** | Unit tests for all three scenarios |
| `bot/virtual_order_simulator.py` | **Modify** (lines 17, 40–48, 106–108) | Replace `leverage_tracker` param with `get_leverage` callable |
| `bot/risk_manager.py` | **Modify** (lines 300–334) | Add `set_scenario_info()`, extend snapshot with `scenario`/`leverage_level` |
| `config/risk_config.py` | **Modify** (line 37) | Add `"scenario": "default"` to DEFAULT_CONFIG |
| `dashboard/app/api/risk/route.ts` | **Modify** (line 25) | Add `scenario: 'default'` to TS DEFAULT_CONFIG |
| `main.py` | **Modify** (lines 26, 97, 150–167, 267–270, 417, 474–486) | Full wiring: factory, hot-reload, closure, mode-switch |
| `dashboard/app/risk/page.tsx` | **Modify** (lines 404–418) | Add scenario selector dropdown in Section C |
| `dashboard/components/CrossSymbolComparison.tsx` | **Modify** (lines 1–62, 114–165, 236–310) | Add scenario tabs + 3 sizing functions |

---

## Task 1: `bot/leverage_scenario.py` — Core module

**Files:**
- Create: `bot/leverage_scenario.py`
- Create: `tests/test_leverage_scenario.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_leverage_scenario.py
import json
import math
import pytest
from pathlib import Path
from bot.leverage_scenario import (
    DefaultScenario, AllocationScenario, FirstHasMostScenario, create_scenario
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / 'lev.json'


# ── DefaultScenario ────────────────────────────────────────────────────────── #

def test_default_starts_at_level_1(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1


def test_default_does_not_advance_until_all_symbols_close(path):
    s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1


def test_default_advances_when_all_close(path):
    s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    s.record_closed('ETHUSDT', 1)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2


def test_default_capped_by_max_policy(path):
    s = DefaultScenario('test', ['BTCUSDT'], path, max_level=5)
    s.record_closed('BTCUSDT', 1)  # advances to 2
    # max_policy=2 caps even though level is 2
    assert s.get_leverage('BTCUSDT', 0.0, 1, 2, 10) == 2


def test_default_get_global_level(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_global_level() == 2


def test_default_get_symbol_level_equals_global(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_symbol_level('BTCUSDT') == s.get_global_level()


def test_default_persists_and_reloads(path):
    s1 = DefaultScenario('test', ['BTCUSDT'], path)
    s1.record_closed('BTCUSDT', 1)
    s2 = DefaultScenario('test', ['BTCUSDT'], path)
    assert s2.get_global_level() == 2


# ── AllocationScenario ─────────────────────────────────────────────────────── #

def test_allocation_each_symbol_tracks_independently(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    # BTCUSDT should advance; ETHUSDT stays at 1
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2
    assert s.get_leverage('ETHUSDT', 0.0, 1, 5, 10) == 1


def test_allocation_get_symbol_level(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_symbol_level('BTCUSDT') == 2
    assert s.get_symbol_level('ETHUSDT') == 1


def test_allocation_get_global_level_is_min(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_global_level() == 1  # min of [2, 1]


def test_allocation_persists_and_reloads(path):
    s1 = AllocationScenario('test', ['BTCUSDT'], path)
    s1.record_closed('BTCUSDT', 1)
    s2 = AllocationScenario('test', ['BTCUSDT'], path)
    assert s2.get_symbol_level('BTCUSDT') == 2


def test_allocation_new_symbol_starts_at_1(path):
    s = AllocationScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)  # BTCUSDT at level 2
    s.add_symbol('ETHUSDT')
    assert s.get_symbol_level('ETHUSDT') == 1


# ── FirstHasMostScenario ───────────────────────────────────────────────────── #

def test_first_has_most_score_0_gives_base(path):
    s = FirstHasMostScenario()
    assert s.get_leverage('BTCUSDT', 0.0, 2, 5, 10) == 2


def test_first_has_most_score_1_gives_max_policy(path):
    s = FirstHasMostScenario()
    assert s.get_leverage('BTCUSDT', 1.0, 2, 5, 10) == 5


def test_first_has_most_score_half(path):
    s = FirstHasMostScenario()
    # base=2, max_policy=6 → range=4, floor(0.5*4)=2 → 2+2=4
    assert s.get_leverage('BTCUSDT', 0.5, 2, 6, 10) == 4


def test_first_has_most_capped_by_bracket_max(path):
    s = FirstHasMostScenario()
    # score=1.0, base=2, max_policy=10, bracket_max=3 → min(10, 3)=3
    assert s.get_leverage('BTCUSDT', 1.0, 2, 10, 3) == 3


def test_first_has_most_record_closed_is_noop(path):
    s = FirstHasMostScenario()
    s.record_closed('BTCUSDT', 5)  # must not raise


def test_first_has_most_get_global_level_returns_0(path):
    s = FirstHasMostScenario()
    assert s.get_global_level() == 0


# ── Factory ────────────────────────────────────────────────────────────────── #

def test_create_scenario_default(path):
    s = create_scenario('default', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, DefaultScenario)


def test_create_scenario_allocation(path):
    s = create_scenario('allocation', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, AllocationScenario)


def test_create_scenario_first_has_most(path):
    s = create_scenario('first_has_most', 'test', [], path, 5)
    assert isinstance(s, FirstHasMostScenario)


def test_create_scenario_unknown_falls_back_to_default(path):
    s = create_scenario('bogus', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, DefaultScenario)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_leverage_scenario.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'bot.leverage_scenario'`

- [ ] **Step 3: Create `bot/leverage_scenario.py`**

```python
# bot/leverage_scenario.py
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Protocol, runtime_checkable

from bot.leverage_tracker import LeverageTracker

logger = logging.getLogger(__name__)


@runtime_checkable
class LeverageScenario(Protocol):
    name: str

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int: ...

    def record_closed(self, symbol: str, leverage: int) -> None: ...
    def add_symbol(self, symbol: str) -> None: ...
    def remove_symbol(self, symbol: str) -> None: ...
    def reset_for_mode(self, new_mode: str, data_path: Path) -> None: ...
    def get_global_level(self) -> int: ...
    def get_symbol_level(self, symbol: str) -> int: ...


class DefaultScenario:
    """All active symbols must complete level N before any advances to N+1."""
    name = "default"

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
    ) -> None:
        self._tracker = LeverageTracker(
            mode=mode,
            active_symbols=active_symbols,
            data_path=data_path,
            max_level=max_level,
        )

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        lev = min(self._tracker.get_current_level(), max_policy, bracket_max)
        return max(1, lev)

    def record_closed(self, symbol: str, leverage: int) -> None:
        self._tracker.record_closed(symbol, leverage)

    def add_symbol(self, symbol: str) -> None:
        self._tracker.add_symbol(symbol)

    def remove_symbol(self, symbol: str) -> None:
        self._tracker.remove_symbol(symbol)

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        self._tracker.reset_for_mode(new_mode, data_path)

    def get_global_level(self) -> int:
        return self._tracker.get_current_level()

    def get_symbol_level(self, symbol: str) -> int:
        return self._tracker.get_current_level()


class AllocationScenario:
    """Each symbol advances its own level independently."""
    name = "allocation"

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
    ) -> None:
        self._data_path = data_path
        self._max_level = max_level
        self._symbol_levels: dict[str, int] = {s: 1 for s in active_symbols}
        self._completed: dict[str, set[int]] = {}
        self._load()

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        lev = min(self._symbol_levels.get(symbol, 1), max_policy, bracket_max)
        return max(1, lev)

    def record_closed(self, symbol: str, leverage: int) -> None:
        self._completed.setdefault(symbol, set()).add(leverage)
        self._advance(symbol)
        self._save()

    def add_symbol(self, symbol: str) -> None:
        if symbol not in self._symbol_levels:
            self._symbol_levels[symbol] = 1

    def remove_symbol(self, symbol: str) -> None:
        self._symbol_levels.pop(symbol, None)
        self._completed.pop(symbol, None)

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        self._data_path = data_path
        self._symbol_levels = {}
        self._completed = {}
        self._load()

    def get_global_level(self) -> int:
        levels = list(self._symbol_levels.values())
        return min(levels) if levels else 1

    def get_symbol_level(self, symbol: str) -> int:
        return self._symbol_levels.get(symbol, 1)

    def _advance(self, symbol: str) -> None:
        current = self._symbol_levels.get(symbol, 1)
        done = self._completed.get(symbol, set())
        while current < self._max_level and current in done:
            current += 1
            logger.info(f"AllocationScenario: {symbol} advanced to level {current}")
        self._symbol_levels[symbol] = current

    def _save(self) -> None:
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol_levels": self._symbol_levels,
            "completed": {s: sorted(ls) for s, ls in self._completed.items()},
        }
        tmp = self._data_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._data_path)

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text())
            self._symbol_levels = {k: int(v) for k, v in data.get("symbol_levels", {}).items()}
            for sym, levels in data.get("completed", {}).items():
                self._completed[sym] = set(int(l) for l in levels)
        except Exception as exc:
            logger.warning(f"AllocationScenario: failed to load state: {exc}")


class FirstHasMostScenario:
    """Leverage is derived immediately from efficiency score. No cross-symbol dependency."""
    name = "first_has_most"

    def get_leverage(
        self, symbol: str, score: float, base: int, max_policy: int, bracket_max: int
    ) -> int:
        raw = base + math.floor(score * (max_policy - base))
        return min(max(base, raw), max_policy, bracket_max)

    def record_closed(self, symbol: str, leverage: int) -> None:
        pass

    def add_symbol(self, symbol: str) -> None:
        pass

    def remove_symbol(self, symbol: str) -> None:
        pass

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        pass

    def get_global_level(self) -> int:
        return 0  # N/A; widget uses score formula instead

    def get_symbol_level(self, symbol: str) -> int:
        return 0  # N/A


def create_scenario(
    name: str,
    mode: str,
    active_symbols: list[str],
    data_path: Path,
    max_level: int = 5,
) -> LeverageScenario:  # type: ignore[return-value]
    if name == "allocation":
        return AllocationScenario(mode, active_symbols, data_path, max_level)
    if name == "first_has_most":
        return FirstHasMostScenario()
    if name != "default":
        logger.warning(f"Unknown scenario '{name}', falling back to 'default'")
    return DefaultScenario(mode, active_symbols, data_path, max_level)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_leverage_scenario.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/leverage_scenario.py tests/test_leverage_scenario.py
git commit -m "feat: add leverage scenario engine (Default, Allocation, FirstHasMost)"
```

---

## Task 2: `bot/virtual_order_simulator.py` — Replace leverage_tracker with callable

**Files:**
- Modify: `bot/virtual_order_simulator.py`
- Modify: `tests/test_virtual_order_simulator.py` (the `make_simulator` helper)

- [ ] **Step 1: Update `make_simulator` in the test to use a `get_leverage` callable**

In `tests/test_virtual_order_simulator.py`, find the `make_simulator` function (lines 80–98) and replace it:

```python
def make_simulator(tmp_path, mode='test'):
    all_presets = {'preset_x': {}, 'preset_y': {}}
    vt = MagicMock()
    vt.get_preset_efficiency.return_value = 0.0
    return VirtualOrderSimulator(
        mode=mode,
        all_presets=all_presets,
        project_root=tmp_path,
        get_leverage=lambda sym: 1,      # replaces leverage_tracker
        initial_balance=1000.0,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
    )
```

Also remove the now-unused import on line 81: `from bot.leverage_tracker import LeverageTracker` (it was inside `make_simulator`).

- [ ] **Step 2: Run tests to verify they fail (the constructor still expects leverage_tracker)**

```bash
python -m pytest tests/test_virtual_order_simulator.py -v 2>&1 | head -20
```

Expected: `TypeError: __init__() got an unexpected keyword argument 'get_leverage'`

- [ ] **Step 3: Update `bot/virtual_order_simulator.py`**

Replace the `TYPE_CHECKING` import block for LeverageTracker and the constructor. The file currently has:

```python
# line 17
    from bot.leverage_tracker import LeverageTracker
```
```python
# lines 40–48
    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        leverage_tracker: 'LeverageTracker',
        initial_balance: float,
        virtual_tracker: 'VirtualTracker',
        min_notionals: dict[str, float],
    ) -> None:
        ...
        self._leverage_tracker = leverage_tracker
```
```python
# line 106
        lev = self._leverage_tracker.get_current_level()
```

Change to:

```python
# Remove the TYPE_CHECKING import for LeverageTracker entirely.
# Add Callable to the typing imports at the top of the file:
from typing import TYPE_CHECKING, Callable, Optional
```

```python
    def __init__(
        self,
        mode: str,
        all_presets: dict,
        project_root: Path,
        get_leverage: Callable[[str], int],   # replaces leverage_tracker
        initial_balance: float,
        virtual_tracker: 'VirtualTracker',
        min_notionals: dict[str, float],
    ) -> None:
        ...
        self._get_leverage = get_leverage
```

```python
        lev = self._get_leverage(symbol)   # replaces self._leverage_tracker.get_current_level()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_virtual_order_simulator.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/virtual_order_simulator.py tests/test_virtual_order_simulator.py
git commit -m "refactor: replace leverage_tracker with get_leverage callable in VirtualOrderSimulator"
```

---

## Task 3: `bot/risk_manager.py` — Extend snapshot with scenario info

**Files:**
- Modify: `bot/risk_manager.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_risk_manager.py` (or create a standalone test at the end of the file):

```python
def test_set_scenario_info_appears_in_snapshot(tmp_path):
    from bot.risk_manager import RiskManager
    rm = RiskManager(
        mode='test',
        initial_balance=1000.0,
        notifier=None,
        config_path=tmp_path / 'risk_config.json',
        state_path=tmp_path / 'risk_state.json',
        results_dir=tmp_path,
    )
    rm.set_scenario_info(
        name='allocation',
        global_level=3,
        per_symbol={'BTCUSDT': 3, 'ETHUSDT': 2},
    )
    snap = rm.snapshot()
    assert snap['scenario'] == 'allocation'
    assert snap['leverage_level'] == 3
    assert snap['per_symbol']['BTCUSDT']['leverage_level'] == 3
    assert snap['per_symbol']['ETHUSDT']['leverage_level'] == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_risk_manager.py::test_set_scenario_info_appears_in_snapshot -v
```

Expected: FAIL — `AttributeError: 'RiskManager' object has no attribute 'set_scenario_info'`

- [ ] **Step 3: Add `set_scenario_info` and update `_build_snapshot` in `bot/risk_manager.py`**

In `__init__`, add these three attributes after `self._last_notify_time`:

```python
self._scenario_name: str = "default"
self._scenario_global_level: int = 1
self._scenario_per_symbol: dict[str, int] = {}
```

Add this new public method after `reset_for_mode_switch`:

```python
def set_scenario_info(
    self,
    name: str,
    global_level: int,
    per_symbol: dict[str, int],
) -> None:
    with self._lock:
        self._scenario_name = name
        self._scenario_global_level = global_level
        self._scenario_per_symbol = dict(per_symbol)
```

In `_build_snapshot`, update the `per_symbol` loop and the returned dict. The current `per_symbol` loop (lines 303–311) becomes:

```python
per_symbol: dict = {}
for sym in symbols:
    cached = self._perf_cache.get(sym)
    score = cached[0] if cached else 0.0
    per_symbol[sym] = {
        "allocation_usdt": round(self._calc_allocation(sym, cfg), 2),
        "leverage": self._calc_leverage(sym, cfg),
        "performance_score": round(score, 3),
        "leverage_level": self._scenario_per_symbol.get(sym, self._scenario_global_level),
    }
```

And the returned dict (currently starting at line 312) gains two new top-level keys:

```python
return {
    "scenario": self._scenario_name,
    "leverage_level": self._scenario_global_level,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "mode": self._mode,
    "balance": round(self._balance, 2),
    "peak_balance": round(self._peak_balance, 2),
    "drawdown_pct": round(self._last_drawdown_pct, 2),
    "warning_active": self._warning_active,
    "hard_stop_active": self._hard_stop_active,
    "active_tier": self._get_tier(cfg),
    "last_event": self._last_notify_event,
    "last_event_time": self._last_notify_time,
    "per_symbol": per_symbol,
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_risk_manager.py -v
```

Expected: all tests pass including the new one.

- [ ] **Step 5: Commit**

```bash
git add bot/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: add set_scenario_info to RiskManager, extend snapshot with scenario/leverage_level"
```

---

## Task 4: Config defaults

**Files:**
- Modify: `config/risk_config.py`
- Modify: `dashboard/app/api/risk/route.ts`

- [ ] **Step 1: Add `"scenario"` to Python DEFAULT_CONFIG**

In `config/risk_config.py`, the `DEFAULT_CONFIG` dict ends with `"telegram_notify_interval_s": 120`. Add after it:

```python
    # Leverage scenario: "default" | "allocation" | "first_has_most"
    "scenario": "default",
```

- [ ] **Step 2: Add `scenario` to TypeScript DEFAULT_CONFIG**

In `dashboard/app/api/risk/route.ts`, the `DEFAULT_CONFIG` object (lines 9–26) ends with `telegram_notify_interval_s: 120`. Add after it:

```typescript
  scenario: 'default',
```

- [ ] **Step 3: Run Python config tests**

```bash
python -m pytest tests/test_risk_config.py -v
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add config/risk_config.py dashboard/app/api/risk/route.ts
git commit -m "feat: add scenario field to risk_config defaults"
```

---

## Task 5: `main.py` — Full wiring

**Files:**
- Modify: `main.py`

This task replaces all `leverage_tracker` usage with the scenario system. Read `main.py` in full before starting.

- [ ] **Step 1: Update imports at the top of `main.py`**

Find this import (around line 26):
```python
from bot.leverage_tracker import LeverageTracker
```

Replace with:
```python
from bot.leverage_scenario import LeverageScenario, create_scenario
```

- [ ] **Step 2: Replace `LeverageTracker` instantiation with scenario factory**

Find this block (around lines 150–154):
```python
    leverage_tracker = LeverageTracker(
        mode=current_mode,
        active_symbols=list(symbols),
        data_path=_PROJECT_ROOT / "data" / f"leverage_state_{current_mode}.json",
    )
```

Replace with:

```python
    def _scenario_data_path(scenario_name: str, mode: str) -> Path:
        if scenario_name == "default":
            return _PROJECT_ROOT / "data" / f"leverage_state_{mode}.json"
        return _PROJECT_ROOT / "data" / f"leverage_state_{scenario_name}_{mode}.json"

    _active_scenario_name: str = risk_cfg.get("scenario", "default")
    scenario = create_scenario(
        name=_active_scenario_name,
        mode=current_mode,
        active_symbols=list(symbols),
        data_path=_scenario_data_path(_active_scenario_name, current_mode),
        max_level=risk_cfg.get("max_leverage_level", 5),
    )
```

- [ ] **Step 3: Replace `leverage_tracker=leverage_tracker` in `VirtualOrderSimulator` constructor**

Find this block (around lines 159–167):
```python
    virtual_order_simulator = VirtualOrderSimulator(
        mode=current_mode,
        all_presets=all_presets,
        project_root=_PROJECT_ROOT,
        leverage_tracker=leverage_tracker,
        initial_balance=0.0,
        virtual_tracker=virtual_tracker,
        min_notionals=min_notionals,
    )
```

**Before** this block, define the virtual leverage closure:
```python
    def _virtual_lev(sym: str) -> int:
        score = virtual_tracker.get_efficiency_score(sym)
        return scenario.get_leverage(
            sym, score,
            risk_cfg.get("base_leverage", 1),
            risk_cfg.get("max_leverage_level", 5),
            125,
        )
```

Then in the `VirtualOrderSimulator(...)` call, replace `leverage_tracker=leverage_tracker` with `get_leverage=_virtual_lev`.

- [ ] **Step 4: Add helper to push scenario info to RiskManager**

After the `virtual_order_simulator` block, add:

```python
    def _push_scenario_info() -> None:
        syms = symbol_registry.get_symbols()
        risk_manager.set_scenario_info(
            name=_active_scenario_name,
            global_level=scenario.get_global_level(),
            per_symbol={s: scenario.get_symbol_level(s) for s in syms},
        )

    _push_scenario_info()  # write initial scenario info to risk_state.json
```

- [ ] **Step 5: Add hot-reload block at the top of `on_candle_close`**

In `on_candle_close` (around line 346), add `nonlocal` declarations and a hot-reload block right after the `STOP` file check:

```python
    async def on_candle_close(symbol: str, kline: list) -> None:
        nonlocal risk_cfg, _active_scenario_name, scenario

        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        # Hot-reload config and switch scenario if changed
        risk_cfg = load_risk_config()
        new_scenario_name = risk_cfg.get("scenario", "default")
        if new_scenario_name != _active_scenario_name:
            _active_scenario_name = new_scenario_name
            scenario = create_scenario(
                name=new_scenario_name,
                mode=mode_manager.current_mode,
                active_symbols=symbol_registry.get_symbols(),
                data_path=_scenario_data_path(new_scenario_name, mode_manager.current_mode),
                max_level=risk_cfg.get("max_leverage_level", 5),
            )
            logger.info(f"Scenario switched to: {new_scenario_name}")
            _push_scenario_info()

        # ... rest of on_candle_close unchanged
```

- [ ] **Step 6: Replace `leverage_tracker` leverage computation in `_try_place_order`**

Find this block (around lines 267–270):
```python
        current_lev = leverage_tracker.get_current_level()
        bracket_max = order_executor.get_bracket_max(symbol)
        max_policy_lev = risk_cfg.get('max_leverage_level', 5)
        actual_lev = min(current_lev, bracket_max, max_policy_lev)
        if actual_lev <= 0:
            actual_lev = 1
```

Replace with:
```python
        bracket_max = order_executor.get_bracket_max(symbol)
        max_policy_lev = risk_cfg.get('max_leverage_level', 5)
        base_lev = risk_cfg.get('base_leverage', 1)
        eff_score = virtual_tracker.get_efficiency_score(symbol)
        actual_lev = scenario.get_leverage(symbol, eff_score, base_lev, max_policy_lev, bracket_max)
        if actual_lev <= 0:
            actual_lev = 1
```

- [ ] **Step 7: Replace `leverage_tracker.record_closed` in `on_price_update`**

Find this line (around line 417):
```python
            leverage_tracker.record_closed(c['symbol'], c.get('leverage', 1))
```

Replace with:
```python
            scenario.record_closed(c['symbol'], c.get('leverage', 1))
            _push_scenario_info()
```

- [ ] **Step 8: Update `on_switch_mode` — replace leverage_tracker.reset_for_mode**

Find this block (around lines 474–476):
```python
        leverage_tracker.reset_for_mode(
            target_mode,
            _PROJECT_ROOT / "data" / f"leverage_state_{target_mode}.json",
        )
```

Replace with:
```python
        scenario.reset_for_mode(
            target_mode,
            _scenario_data_path(_active_scenario_name, target_mode),
        )
```

Also find the `VirtualOrderSimulator(...)` recreation (around lines 478–486) and update `leverage_tracker=leverage_tracker` → `get_leverage=_virtual_lev`.

- [ ] **Step 9: Run the full test suite**

```bash
python -m pytest tests/ -v --ignore=tests/test_data_feed.py 2>&1 | tail -20
```

Expected: all previously passing tests still pass.

- [ ] **Step 10: Commit**

```bash
git add main.py
git commit -m "feat: wire scenario system into main loop with hot-reload"
```

---

## Task 6: `dashboard/app/risk/page.tsx` — Scenario selector

**Files:**
- Modify: `dashboard/app/risk/page.tsx`

- [ ] **Step 1: Add scenario selector to Section C (Leverage Controls)**

In Section C, find the `use_allocation_weighting` checkbox block (lines 405–415):

```tsx
          <div className="flex items-center gap-3">
            <label className="text-xs text-gray-500 w-52 shrink-0" title="When disabled, position size = min_notional / leverage. Enable to use per-symbol weighted allocation.">
              Use allocation weighting
            </label>
            <input
              type="checkbox"
              checked={config.use_allocation_weighting ?? false}
              onChange={e => patchConfig({ use_allocation_weighting: e.target.checked })}
              className="accent-indigo-500 h-4 w-4 cursor-pointer"
            />
          </div>
```

**Replace** that entire block with the scenario selector (the old checkbox becomes irrelevant because scenarios supersede it):

```tsx
          <div className="flex items-center gap-3">
            <label
              className="text-xs text-gray-500 w-52 shrink-0"
              title="Controls how each symbol's leverage is determined and when it can increase."
            >
              Leverage scenario
            </label>
            <select
              value={(config.scenario as string) ?? 'default'}
              onChange={e => patchConfig({ scenario: e.target.value })}
              className="bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-indigo-500 cursor-pointer"
            >
              <option value="default">Default — cross-symbol progression</option>
              <option value="allocation">Allocation — per-symbol independent</option>
              <option value="first_has_most">First Has the Most — score-based, instant</option>
            </select>
          </div>
          <p className="text-[10px] text-gray-500 font-mono">
            {(config.scenario as string) === 'allocation'
              ? 'Each symbol advances independently after 1 close at its current level.'
              : (config.scenario as string) === 'first_has_most'
              ? 'Leverage = base + floor(score × (max − base)). No cross-symbol wait.'
              : 'All symbols must complete level N before any advances to N+1.'}
          </p>
```

- [ ] **Step 2: Build check**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -20
```

Expected: no errors (or only pre-existing errors unrelated to this change).

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/risk/page.tsx
git commit -m "feat: add scenario selector dropdown to Risk page Leverage Controls"
```

---

## Task 7: `dashboard/components/CrossSymbolComparison.tsx` — Scenario tabs

**Files:**
- Modify: `dashboard/components/CrossSymbolComparison.tsx`

Read the full component before starting. Key sections: interfaces (lines 10–28), `computeSizing` (lines 39–62), component state (lines 114–125), `sizingBySymbol` useMemo (lines 130–140), tab row JSX (lines 236–310).

- [ ] **Step 1: Update the `RiskConfig` and `RiskStateSnapshot` interfaces**

The current `RiskConfig` interface (lines 10–16) does not include `scenario` or `max_leverage_level`. Replace it with:

```typescript
interface RiskConfig {
  balance_tiers: Array<{ min_balance_usdt: number; max_deploy_pct: number; max_leverage_ceiling: number }>
  base_leverage: number
  max_leverage: number
  max_leverage_level?: number
  min_balance_pct?: number
  symbol_weights?: Record<string, number>
  scenario?: string
}
```

The current `RiskStateSnapshot` interface (lines 18–22) does not include `leverage_level`. Replace it with:

```typescript
interface RiskStateSnapshot {
  balance: number
  leverage_level?: number
  per_symbol: Record<string, {
    allocation_usdt: number
    leverage: number
    leverage_level?: number
    performance_score: number
  }>
}
```

- [ ] **Step 2: Add scenario type and three sizing functions above `activeTier`**

After the `LEVERAGES` constant (line 30) and before `activeTier`, add:

```typescript
type ScenarioId = 'default' | 'allocation' | 'first_has_most'

// ── Per-scenario sizing ───────────────────────────────────────────────────────

function computeSizingDefault(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
): { margin: number; lev: number } {
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage_level ?? riskState?.leverage_level ?? 1)
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = Object.keys(riskState?.per_symbol ?? {}).length || 1
  return { margin: pool / numSymbols, lev }
}

function computeSizingAllocation(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
): { margin: number; lev: number } {
  // Uses per-symbol allocation weight + per-symbol leverage_level
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const weights = config.symbol_weights ?? {}
  const totalW = Object.values(weights).reduce((a, b) => a + b, 0) || 1
  const w = weights[symbol] ?? 1
  const margin = pool * (w / totalW)
  const lev = Math.max(1, riskState?.per_symbol?.[symbol]?.leverage_level ?? 1)
  return { margin, lev }
}

function computeSizingFirstHasMost(
  symbol: string,
  balance: number,
  config: RiskConfig,
  riskState: RiskStateSnapshot | undefined,
): { margin: number; lev: number } {
  const score = riskState?.per_symbol?.[symbol]?.performance_score ?? 0
  const base = config.base_leverage ?? 1
  const maxLev = config.max_leverage_level ?? 5
  const raw = base + Math.floor(score * (maxLev - base))
  const lev = Math.max(base, Math.min(maxLev, raw))
  const tier = activeTier(config, balance)
  const reserve = balance * (config.min_balance_pct ?? 0) / 100
  const pool = Math.max(0, balance - reserve) * tier.max_deploy_pct / 100
  const numSymbols = Object.keys(riskState?.per_symbol ?? {}).length || 1
  return { margin: pool / numSymbols, lev }
}
```

Note: `activeTier` is defined AFTER these functions in the original file (line 34). Move the `activeTier` definition to come **before** these three functions, so they can call it.

- [ ] **Step 3: Add `scenarioTab` state to the component**

In the component state block (around line 120), after `const [useSharedBalance, setUseSharedBalance]`, add:

```typescript
  const [scenarioTab, setScenarioTab] = useState<ScenarioId>(
    (riskConfig?.scenario as ScenarioId | undefined) ?? 'default'
  )
```

- [ ] **Step 4: Update `sizingBySymbol` to use the active scenario tab**

Replace the current `sizingBySymbol` useMemo (lines 130–140):

```typescript
  const sizingBySymbol = useMemo<Record<string, { margin: number; lev: number }>>(() => {
    const out: Record<string, { margin: number; lev: number }> = {}
    for (const sym of loadedSymbols) {
      if (useSharedBalance && riskConfig) {
        switch (scenarioTab) {
          case 'allocation':
            out[sym] = computeSizingAllocation(sym, totalBalance, riskConfig, riskState)
            break
          case 'first_has_most':
            out[sym] = computeSizingFirstHasMost(sym, totalBalance, riskConfig, riskState)
            break
          default:
            out[sym] = computeSizingDefault(sym, totalBalance, riskConfig, riskState)
        }
      } else {
        out[sym] = { margin: positionSize, lev: leverage }
      }
    }
    return out
  }, [useSharedBalance, scenarioTab, riskConfig, riskState, totalBalance, positionSize, leverage, loadedSymbols])
```

- [ ] **Step 5: Add scenario tab row in JSX, after the existing tab row**

In the JSX, find the `{/* Shared balance toggle */}` section inside the controls row (around line 257). Add the scenario tab row **above** the shared balance toggle, but only when `useSharedBalance && riskConfig`:

```tsx
        {/* Scenario tabs — only visible in shared balance mode */}
        {useSharedBalance && riskConfig && (
          <div className="flex items-center gap-1">
            <span className="text-gray-600 text-[10px] mr-1">Scenario:</span>
            {([
              ['default',        'Default'],
              ['allocation',     'Allocation'],
              ['first_has_most', 'First Has Most'],
            ] as [ScenarioId, string][]).map(([id, label]) => (
              <button
                key={id}
                onClick={() => setScenarioTab(id)}
                className={`px-2 py-0.5 rounded text-[10px] font-semibold transition-colors ${
                  scenarioTab === id
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        )}
```

- [ ] **Step 6: Build check**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot/dashboard
npx tsc --noEmit 2>&1 | head -30
```

Expected: no new type errors.

- [ ] **Step 7: Commit**

```bash
git add dashboard/components/CrossSymbolComparison.tsx
git commit -m "feat: add scenario tabs to CrossSymbolComparison widget"
```

---

## Final: Run full test suite

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/ -v --ignore=tests/test_data_feed.py 2>&1 | tail -30
```

Expected: all tests pass.

One final code review using `superpowers:requesting-code-review` covering all tasks.
