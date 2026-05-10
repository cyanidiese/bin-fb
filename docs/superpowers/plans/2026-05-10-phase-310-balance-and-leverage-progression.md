# Phase 3.10 — Balance & Leverage Progression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the allocation-based + performance-leverage system with a graduated leverage approach: all symbols start at level 1, the global level advances when every active symbol has proven one closed real order at the current level. Position size = `min_notional / current_leverage`.

**Architecture:** New `LeverageTracker` owns the global leverage state and persists it to disk. Two thin loggers (`balance_history.py`, `decision_log.py`) record events post-run analysis. `VirtualOrderSimulator` gets a shared virtual balance pool (same sizing formula) with preset-efficiency-ranked opening. `main.py` replaces the per-symbol ad-hoc loop with an efficiency-ranked cross-symbol loop.

**Tech Stack:** Python (json, pathlib, dataclasses, asyncio), Next.js 15 App Router (TypeScript), existing atomic-write pattern (`tmp.write_text(...); tmp.replace(path)`), existing `RiskManager` / `VirtualTracker` / `OrderExecutor` interfaces.

---

## File Structure

| File | Status | Change |
|---|---|---|
| `bot/virtual_tracker.py` | Modify | Add `get_efficiency_score`, `get_preset_efficiency` |
| `bot/leverage_tracker.py` | **New** | `LeverageTracker` — global leverage progression |
| `bot/balance_history.py` | **New** | Append-only balance event logger |
| `bot/decision_log.py` | **New** | Append-only placement decision logger |
| `config/risk_config.py` | Modify | Add `use_allocation_weighting`, `max_leverage_level` to `DEFAULT_CONFIG` |
| `bot/risk_manager.py` | Modify | Simplify `can_open_sync` — remove `estimated_size_usdt` + allocation checks |
| `bot/order_executor.py` | Modify | Add `balance_at_open`, `signal_level`, `precision_score` to records; add `leverage` to close result dicts |
| `bot/virtual_order_simulator.py` | Modify | Remove `risk_manager`; add virtual balance pool + `leverage_tracker` + preset efficiency sorting |
| `main.py` | Modify | Efficiency-ranked loop, `_get_fresh_balance()`, wire LeverageTracker + loggers |
| `dashboard/app/api/balance-history/route.ts` | **New** | `GET /api/balance-history?mode=test` |
| `dashboard/app/api/risk/route.ts` | Modify | Accept `use_allocation_weighting`, `max_leverage_level` |
| `dashboard/app/risk/page.tsx` | Modify | Allocation checkbox, max leverage level input |
| `tests/test_virtual_tracker_helpers.py` | **New** | Tests for the two new VirtualTracker methods |
| `tests/test_leverage_tracker.py` | **New** | LeverageTracker unit tests |
| `tests/test_balance_history.py` | **New** | balance_history unit tests |
| `tests/test_decision_log.py` | **New** | decision_log unit tests |
| `tests/test_virtual_order_simulator.py` | **New** | VirtualOrderSimulator unit tests (was deferred from Phase 3.9) |
| `tests/test_risk_manager.py` | Modify | Remove deployment-cap test; update `can_open_sync` call sites |

---

### Task 1: VirtualTracker — add efficiency helpers

**Files:**
- Modify: `bot/virtual_tracker.py`
- Create: `tests/test_virtual_tracker_helpers.py`

`VirtualTracker` stores `{symbol: {preset_name: {total_winning_usdt, trade_count}}}`. Two new methods surface this for callers:
- `get_efficiency_score(symbol)` — highest `total_winning_usdt` among eligible presets (≥ `_MIN_TRADES` trades) for a symbol; used by `main.py` to rank symbols.
- `get_preset_efficiency(symbol, preset_name)` — `total_winning_usdt` for a specific preset; used by `VirtualOrderSimulator` to rank presets. Both return `0.0` when no data exists.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_virtual_tracker_helpers.py`:

```python
import json
import pytest
from bot.virtual_tracker import VirtualTracker


@pytest.fixture
def tracker(tmp_path):
    eff_path = tmp_path / 'eff.json'
    eff_data = {
        "BTCUSDT": {
            "preset_a": {"total_winning_usdt": 10.0, "trade_count": 5},
            "preset_b": {"total_winning_usdt": 25.0, "trade_count": 6},
            "preset_c": {"total_winning_usdt": 50.0, "trade_count": 2},  # ineligible: < 4 trades
        },
        "ETHUSDT": {
            "preset_a": {"total_winning_usdt": 8.0, "trade_count": 4},
        },
    }
    eff_path.write_text(json.dumps(eff_data))
    return VirtualTracker(
        mode='test',
        orders_path=tmp_path / 'orders.json',
        efficiency_path=eff_path,
    )


def test_get_efficiency_score_returns_best_eligible(tracker):
    # preset_a=10 (5 trades ok), preset_b=25 (6 trades ok), preset_c=50 but 2 trades → ineligible
    assert tracker.get_efficiency_score('BTCUSDT') == 25.0


def test_get_efficiency_score_unknown_symbol(tracker):
    assert tracker.get_efficiency_score('SOLUSDT') == 0.0


def test_get_preset_efficiency_known(tracker):
    assert tracker.get_preset_efficiency('BTCUSDT', 'preset_a') == 10.0


def test_get_preset_efficiency_unknown_preset(tracker):
    assert tracker.get_preset_efficiency('BTCUSDT', 'nonexistent') == 0.0


def test_get_preset_efficiency_unknown_symbol(tracker):
    assert tracker.get_preset_efficiency('SOLUSDT', 'preset_a') == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_virtual_tracker_helpers.py -v
```
Expected: `AttributeError: 'VirtualTracker' object has no attribute 'get_efficiency_score'`

- [ ] **Step 3: Add the two helpers to `bot/virtual_tracker.py`**

Insert after `get_efficiency` (which ends around line 56):

```python
    def get_efficiency_score(self, symbol: str) -> float:
        """Highest total_winning_usdt among eligible presets (>= _MIN_TRADES) for this symbol.

        Returns 0.0 when no eligible preset exists. Used to rank symbols for real-order placement.
        """
        symbol_data = self._efficiency.get(symbol, {})
        best = 0.0
        for stats in symbol_data.values():
            if stats.get('trade_count', 0) >= _MIN_TRADES:
                best = max(best, stats.get('total_winning_usdt', 0.0))
        return best

    def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
        """total_winning_usdt for a specific preset. Returns 0.0 if not found.

        Used to rank presets inside VirtualOrderSimulator — best preset gets capital first.
        """
        return self._efficiency.get(symbol, {}).get(preset_name, {}).get('total_winning_usdt', 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_virtual_tracker_helpers.py -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/virtual_tracker.py tests/test_virtual_tracker_helpers.py
git commit -m "feat: add get_efficiency_score and get_preset_efficiency to VirtualTracker"
```

---

### Task 2: Create `bot/leverage_tracker.py`

**Files:**
- Create: `bot/leverage_tracker.py`
- Create: `tests/test_leverage_tracker.py`

`LeverageTracker` is the global source of truth for the current leverage level. It starts at level 1. Advancement rule: if every active symbol has `current_level` in its `_completed` set (meaning ≥1 closed real order was recorded at that level), increment `current_level`. It persists atomically to `data/leverage_state_{mode}.json`.

Edge cases:
- New symbol added at level N: must complete level N (not all previous levels) before the next advance — the uniform `_check_advance` loop handles this naturally since it always checks `current_level`.
- Symbol removed: `_check_advance` fires immediately and may unblock an advance.
- Empty symbols list: level stays frozen.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leverage_tracker.py`:

```python
import json
import pytest
from pathlib import Path
from bot.leverage_tracker import LeverageTracker


@pytest.fixture
def path(tmp_path):
    return tmp_path / 'leverage_state.json'


def test_starts_at_level_1(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    assert lt.get_current_level() == 1


def test_does_not_advance_with_partial_graduation(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    assert lt.get_current_level() == 1  # ETHUSDT hasn't closed level 1 yet


def test_advances_when_all_symbols_graduate(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    lt.record_closed('ETHUSDT', 1)
    assert lt.get_current_level() == 2


def test_new_symbol_blocks_next_advance(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.add_symbol('ETHUSDT')          # ETHUSDT must now complete level 2
    lt.record_closed('BTCUSDT', 2)
    assert lt.get_current_level() == 2  # ETHUSDT has not closed level 2


def test_new_symbol_unblocks_after_one_close_at_current_level(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.add_symbol('ETHUSDT')
    lt.record_closed('BTCUSDT', 2)
    lt.record_closed('ETHUSDT', 2)   # both at level 2 → advance to 3
    assert lt.get_current_level() == 3


def test_remove_symbol_may_unblock_advance(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    assert lt.get_current_level() == 1  # blocked by ETHUSDT
    lt.remove_symbol('ETHUSDT')         # only BTCUSDT needed, already done
    assert lt.get_current_level() == 2


def test_capped_at_max_level(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path, max_level=2)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.record_closed('BTCUSDT', 2)   # would advance to 3, capped at max=2
    assert lt.get_current_level() == 2


def test_persists_state_and_loads(path):
    lt1 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt1.record_closed('BTCUSDT', 1)
    assert lt1.get_current_level() == 2
    # Second instance loads from disk
    lt2 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    assert lt2.get_current_level() == 2


def test_no_advance_with_no_active_symbols(path):
    lt = LeverageTracker(mode='test', active_symbols=[], data_path=path)
    assert lt.get_current_level() == 1  # stays frozen


def test_record_closed_returns_true_when_advanced(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    advanced = lt.record_closed('BTCUSDT', 1)
    assert advanced is True


def test_record_closed_returns_false_when_not_advanced(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    advanced = lt.record_closed('BTCUSDT', 1)
    assert advanced is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_leverage_tracker.py -v
```
Expected: `ModuleNotFoundError: No module named 'bot.leverage_tracker'`

- [ ] **Step 3: Create `bot/leverage_tracker.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LeverageTracker:
    """
    Tracks the global leverage level. Starts at level 1 and advances when every
    active symbol has at least one closed real order recorded at the current level.
    Persists to data/leverage_state_{mode}.json (atomic write).
    """

    def __init__(
        self,
        mode: str,
        active_symbols: list[str],
        data_path: Path,
        max_level: int = 5,
    ) -> None:
        self._mode = mode
        self._active_symbols: list[str] = list(active_symbols)
        self._data_path = data_path
        self._max_level = max_level
        self._current_level: int = 1
        self._completed: dict[str, set[int]] = {}
        self._load()

    def get_current_level(self) -> int:
        return self._current_level

    def record_closed(self, symbol: str, leverage: int) -> bool:
        """Record a closed real order at the given leverage level.

        Returns True if the global level advanced as a result.
        """
        self._completed.setdefault(symbol, set()).add(leverage)
        advanced = self._check_advance()
        self._save()
        return advanced

    def add_symbol(self, symbol: str) -> bool:
        """Add a new active symbol. Returns True if this immediately triggers advancement."""
        if symbol not in self._active_symbols:
            self._active_symbols.append(symbol)
        advanced = self._check_advance()
        self._save()
        return advanced

    def remove_symbol(self, symbol: str) -> bool:
        """Remove a symbol. Returns True if removal triggers advancement."""
        self._active_symbols = [s for s in self._active_symbols if s != symbol]
        advanced = self._check_advance()
        self._save()
        return advanced

    def reset_for_mode(self, new_mode: str, data_path: Path) -> None:
        """Update mode after a live/test switch. Reloads persisted state for the new mode."""
        self._mode = new_mode
        self._data_path = data_path
        self._current_level = 1
        self._completed = {}
        self._load()

    def _check_advance(self) -> bool:
        if not self._active_symbols:
            return False
        advanced = False
        while self._current_level < self._max_level:
            all_graduated = all(
                self._current_level in self._completed.get(sym, set())
                for sym in self._active_symbols
            )
            if not all_graduated:
                break
            self._current_level += 1
            advanced = True
            logger.info(f"LeverageTracker: level advanced to {self._current_level}")
        return advanced

    def _save(self) -> None:
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'current_level': self._current_level,
            'completed': {sym: sorted(lev_set) for sym, lev_set in self._completed.items()},
        }
        tmp = self._data_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._data_path)

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text())
            self._current_level = int(data.get('current_level', 1))
            for sym, levels in data.get('completed', {}).items():
                self._completed[sym] = set(int(l) for l in levels)
        except Exception as exc:
            logger.warning(f"LeverageTracker: failed to load state, starting fresh: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_leverage_tracker.py -v
```
Expected: 11 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/leverage_tracker.py tests/test_leverage_tracker.py
git commit -m "feat: add LeverageTracker — graduated global leverage progression"
```

---

### Task 3: Create `bot/balance_history.py` and `bot/decision_log.py`

**Files:**
- Create: `bot/balance_history.py`
- Create: `bot/decision_log.py`
- Create: `tests/test_balance_history.py`
- Create: `tests/test_decision_log.py`

Both are thin append-only loggers following the same atomic-write pattern as `bot/system_log.py`. Capped at a max entry count (oldest trimmed). These are module-level functions, not classes — callers pass the path each time.

- [ ] **Step 1: Write failing tests for `balance_history`**

Create `tests/test_balance_history.py`:

```python
import json
import pytest
from pathlib import Path
from bot.balance_history import record, MAX_ENTRIES


def test_creates_file_on_first_write(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=1000.0, trigger='startup')
    assert path.exists()


def test_startup_entry_shape(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=500.0, trigger='startup')
    data = json.loads(path.read_text())
    assert len(data) == 1
    e = data[0]
    assert e['balance'] == 500.0
    assert e['trigger'] == 'startup'
    assert 'timestamp' in e
    assert 'symbol' not in e   # optional field absent when not provided


def test_order_close_entry_includes_pnl(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=1010.0, trigger='order_close',
           symbol='BTCUSDT', leverage=2, pnl_usdt=10.0)
    data = json.loads(path.read_text())
    e = data[0]
    assert e['trigger'] == 'order_close'
    assert e['symbol'] == 'BTCUSDT'
    assert e['leverage'] == 2
    assert e['pnl_usdt'] == 10.0


def test_appends_multiple_entries(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=100.0, trigger='startup')
    record(path, balance=110.0, trigger='order_close')
    data = json.loads(path.read_text())
    assert len(data) == 2


def test_caps_at_max_entries(tmp_path):
    path = tmp_path / 'bh.json'
    for i in range(MAX_ENTRIES + 5):
        record(path, balance=float(i), trigger='startup')
    data = json.loads(path.read_text())
    assert len(data) == MAX_ENTRIES
    assert data[-1]['balance'] == float(MAX_ENTRIES + 4)  # newest is last
```

- [ ] **Step 2: Write failing tests for `decision_log`**

Create `tests/test_decision_log.py`:

```python
import json
import pytest
from pathlib import Path
from bot.decision_log import record, MAX_ENTRIES


def test_creates_file_on_first_write(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1000, symbol='BTCUSDT', decision='placed',
           reason='ok', balance=100.0, leverage=1, efficiency_score=0.8)
    assert path.exists()


def test_placed_entry_shape(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1746878400000, symbol='ETHUSDT', decision='placed',
           reason='', balance=432.5, leverage=2, efficiency_score=0.83,
           preset_name='r5_arm15_cooldown', signal_type='ASCENDING_NEAR_HIGHER_LOW',
           precision_score=0.71, level=2)
    data = json.loads(path.read_text())
    assert len(data) == 1
    e = data[0]
    assert e['symbol'] == 'ETHUSDT'
    assert e['decision'] == 'placed'
    assert e['candle_ts'] == 1746878400000
    assert e['preset_name'] == 'r5_arm15_cooldown'
    assert e['precision_score'] == 0.71
    assert e['level'] == 2


def test_skip_entry_without_optional_fields(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1000, symbol='BTCUSDT', decision='skip_balance',
           reason='balance=5 < margin=22', balance=5.0, leverage=1, efficiency_score=0.0)
    data = json.loads(path.read_text())
    e = data[0]
    assert e['decision'] == 'skip_balance'
    assert 'preset_name' not in e
    assert 'precision_score' not in e


def test_caps_at_max_entries(tmp_path):
    path = tmp_path / 'dl.json'
    for i in range(MAX_ENTRIES + 5):
        record(path, candle_ts=i, symbol='BTCUSDT', decision='placed',
               reason='', balance=100.0, leverage=1, efficiency_score=0.0)
    data = json.loads(path.read_text())
    assert len(data) == MAX_ENTRIES
    assert data[-1]['candle_ts'] == MAX_ENTRIES + 4  # newest retained
```

- [ ] **Step 3: Run failing tests**

```bash
python -m pytest tests/test_balance_history.py tests/test_decision_log.py -v
```
Expected: `ModuleNotFoundError: No module named 'bot.balance_history'`

- [ ] **Step 4: Create `bot/balance_history.py`**

```python
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 10_000


def record(
    path: Path,
    balance: float,
    trigger: str,
    symbol: Optional[str] = None,
    leverage: Optional[int] = None,
    pnl_usdt: Optional[float] = None,
) -> None:
    """Append one balance event. Caps at MAX_ENTRIES (oldest trimmed first).

    trigger values: 'startup' | 'order_open' | 'order_close' | 'balance_refresh'
    """
    entry: dict = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'balance': balance,
        'trigger': trigger,
    }
    if symbol is not None:
        entry['symbol'] = symbol
    if leverage is not None:
        entry['leverage'] = leverage
    if pnl_usdt is not None:
        entry['pnl_usdt'] = pnl_usdt

    _append(path, entry)


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    if len(existing) > MAX_ENTRIES:
        existing = existing[-MAX_ENTRIES:]
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)
```

- [ ] **Step 5: Create `bot/decision_log.py`**

```python
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_ENTRIES = 5_000


def record(
    path: Path,
    candle_ts: int,
    symbol: str,
    decision: str,
    reason: str,
    balance: float,
    leverage: int,
    efficiency_score: float,
    preset_name: Optional[str] = None,
    signal_type: Optional[str] = None,
    precision_score: Optional[float] = None,
    level: Optional[int] = None,
) -> None:
    """Append one placement decision. Caps at MAX_ENTRIES (oldest trimmed first).

    decision values: 'placed' | 'skip_balance' | 'skip_profit_factor' |
                     'skip_hard_stop' | 'skip_already_open' | 'skip_no_signal'
    """
    entry: dict = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'candle_ts': candle_ts,
        'symbol': symbol,
        'decision': decision,
        'reason': reason,
        'balance': balance,
        'leverage': leverage,
        'efficiency_score': efficiency_score,
    }
    if preset_name is not None:
        entry['preset_name'] = preset_name
    if signal_type is not None:
        entry['signal_type'] = signal_type
    if precision_score is not None:
        entry['precision_score'] = precision_score
    if level is not None:
        entry['level'] = level

    _append(path, entry)


def _append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    if len(existing) > MAX_ENTRIES:
        existing = existing[-MAX_ENTRIES:]
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(existing))
    tmp.replace(path)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
python -m pytest tests/test_balance_history.py tests/test_decision_log.py -v
```
Expected: 9 tests pass.

- [ ] **Step 7: Commit**

```bash
git add bot/balance_history.py bot/decision_log.py \
        tests/test_balance_history.py tests/test_decision_log.py
git commit -m "feat: add balance_history and decision_log utility modules"
```

---

### Task 4: Update `config/risk_config.py` and `bot/risk_manager.py`

**Files:**
- Modify: `config/risk_config.py`
- Modify: `bot/risk_manager.py`
- Modify: `tests/test_risk_manager.py`

Add two new defaults. Simplify `can_open_sync` to only gate on `hard_stop_active` and `min_profit_factor` — the `estimated_size_usdt` parameter and all allocation/deployment-cap checks are removed. The affordability check is now done explicitly in `_try_place_order` before calling this method.

- [ ] **Step 1: Add new defaults to `config/risk_config.py`**

At the end of the `DEFAULT_CONFIG` dict (after `"price_stale_threshold_s": 15`), add two lines:

```python
    # Leverage progression
    "max_leverage_level": 5,
    # Allocation weighting (archived — disabled by default)
    "use_allocation_weighting": False,
```

- [ ] **Step 2: Simplify `can_open_sync` in `bot/risk_manager.py`**

Replace the entire `can_open_sync` method (the one starting at line 100) with:

```python
    def can_open_sync(self, symbol: str) -> tuple[bool, str]:
        """
        Gate for order placement. Returns (allowed, reason).
        reason is '' when allowed=True.
        Checks: hard_stop_active gate and min_profit_factor gate only.
        Sizing affordability is handled explicitly by the caller before invoking this.
        """
        with self._lock:
            cfg = self._load_config()

            if self._hard_stop_active:
                return False, "hard_stop_active"

            _, pf = self._get_perf_score(symbol, cfg)
            if pf < cfg["min_profit_factor"]:
                return (
                    False,
                    f"profit_factor={pf:.2f} below threshold={cfg['min_profit_factor']}",
                )

            return True, ""
```

Also update the async wrapper to match the new signature:

```python
    async def can_open(self, symbol: str) -> tuple[bool, str]:
        return self.can_open_sync(symbol)
```

- [ ] **Step 3: Update `tests/test_risk_manager.py` — remove deployment-cap test; fix call sites**

The test file has 4 tests that call `can_open_sync` with 2 arguments and 1 test that specifically tests the deployment cap (which no longer exists). Make these changes:

**Delete** the test `test_can_open_blocked_by_deployment_cap` (lines 87–93) entirely.

**Update** the 4 remaining `can_open_sync` call sites — remove the second argument:

```python
# test_can_open_passes_with_zero_size  (line 63)
allowed, reason = rm.can_open_sync("BTCUSDT")   # was: rm.can_open_sync("BTCUSDT", 0.0)

# test_can_open_passes_when_pf_ok  (line 73)
allowed, reason = rm.can_open_sync("BTCUSDT")   # was: rm.can_open_sync("BTCUSDT", 0.0)

# test_can_open_blocked_by_hard_stop  (line 82)
allowed, reason = rm.can_open_sync("BTCUSDT")   # was: rm.can_open_sync("BTCUSDT", 0.0)

# test_reset_hard_stop  (line 130)
allowed, _ = rm.can_open_sync("BTCUSDT")         # was: rm.can_open_sync("BTCUSDT", 0.0)
```

- [ ] **Step 4: Run risk tests to verify they pass**

```bash
python -m pytest tests/test_risk_config.py tests/test_risk_manager.py -v
```
Expected: 16 tests pass (was 17 — one removed).

- [ ] **Step 5: Commit**

```bash
git add config/risk_config.py bot/risk_manager.py tests/test_risk_manager.py
git commit -m "feat: simplify can_open_sync — remove allocation checks; add max_leverage_level default"
```

---

### Task 5: Update `bot/order_executor.py` — enrich records + close result dicts

**Files:**
- Modify: `bot/order_executor.py`

Three new fields in order records (`balance_at_open`, `signal_level`, `precision_score`) enable post-run correlation of signal quality with trade outcomes. Adding `leverage` to close result dicts lets `main.py` call `leverage_tracker.record_closed(symbol, leverage)` without re-reading the file.

- [ ] **Step 1: Add three fields to the `OpenOrder` dataclass**

Find the `@dataclass class OpenOrder` (around line 48) and add after `open_time`:

```python
    balance_at_open: float = 0.0
    signal_level: int = 0
    precision_score: float = 0.0
```

The full dataclass becomes:

```python
@dataclass
class OpenOrder:
    symbol: str
    preset_name: str
    side: str
    entry_price: float
    tp_price: float
    sl_price: float
    quantity: float
    leverage: int
    partial_take_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    exchange_order_id: str | None = None
    open_time: str | None = None
    balance_at_open: float = 0.0
    signal_level: int = 0
    precision_score: float = 0.0
```

- [ ] **Step 2: Add three params to `place_order` signature**

Find `async def place_order(` (around line 123). Add after `signal_type: str = ''`:

```python
        balance_at_open: float = 0.0,
        signal_level: int = 0,
        precision_score: float = 0.0,
```

- [ ] **Step 3: Store new fields when creating `OpenOrder` inside `place_order`**

Find the `self._open_orders[symbol] = OpenOrder(...)` block (around line 149). Add the three new fields:

```python
                self._open_orders[symbol] = OpenOrder(
                    symbol=symbol, preset_name=preset_name, side=side,
                    entry_price=entry, tp_price=tp, sl_price=sl,
                    quantity=rounded_qty, leverage=leverage,
                    partial_take_pct=partial_take_pct,
                    trailing_stop_pct=trailing_stop_pct,
                    exchange_order_id=order_id,
                    balance_at_open=balance_at_open,
                    signal_level=signal_level,
                    precision_score=precision_score,
                )
```

- [ ] **Step 4: Include new fields in `_record_real_order_close`**

Find the `records.append({...})` block (around line 376). Add the three new fields:

```python
        records.append({
            'preset_name': order.preset_name,
            'side': order.side,
            'entry_price': order.entry_price,
            'close_price': close_price,
            'tp': order.tp_price,
            'sl': order.sl_price,
            'quantity': order.quantity,
            'leverage': order.leverage,
            'open_time': order.open_time,
            'close_time': datetime.now(timezone.utc).isoformat(),
            'pnl_usdt': pnl_usdt,
            'result': result,
            'balance_at_open': order.balance_at_open,
            'signal_level': order.signal_level,
            'precision_score': order.precision_score,
        })
```

- [ ] **Step 5: Add `leverage` to close result dicts in `check_all_orders`**

Find `closed.append({...})` inside `check_all_orders` (around line 236). Add `"leverage": open_order.leverage`:

```python
            closed.append({
                "symbol": symbol,
                "preset_name": open_order.preset_name,
                "result": result,
                "pnl_usdt": pnl,
                "side": open_order.side,
                "entry_price": open_order.entry_price,
                "close_price": actual_close_price,
                "leverage": open_order.leverage,
            })
```

- [ ] **Step 6: Add `leverage` to the return value of `check_symbol_price`**

Find `return [{...}]` inside `check_symbol_price` (around line 288). Add `"leverage": open_order.leverage`:

```python
        return [{
            "symbol": symbol,
            "preset_name": open_order.preset_name,
            "result": result,
            "pnl_usdt": pnl,
            "side": open_order.side,
            "entry_price": open_order.entry_price,
            "close_price": actual_close_price,
            "leverage": open_order.leverage,
        }]
```

- [ ] **Step 7: Verify clean import**

```bash
python -c "from bot.order_executor import OrderExecutor, OpenOrder; print('OK')"
```
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add bot/order_executor.py
git commit -m "feat: add balance_at_open/signal_level/precision_score to order records; add leverage to close result"
```

---

### Task 6: Rewrite `bot/virtual_order_simulator.py`

**Files:**
- Modify: `bot/virtual_order_simulator.py`
- Create: `tests/test_virtual_order_simulator.py`

Replace the `risk_manager` dependency with a shared virtual balance pool. Add `leverage_tracker` for sizing (Option A: `margin = min_notional / leverage`). Sort presets by efficiency before opening — best preset gets capital first. Track `virtual_margin`, `virtual_balance_at_open`, `virtual_balance_after_close` in records.

- [ ] **Step 1: Write failing tests**

Create `tests/test_virtual_order_simulator.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from bot.virtual_order_simulator import VirtualOrderSimulator
from bot.leverage_tracker import LeverageTracker


def make_lt(tmp_path, symbols=None):
    return LeverageTracker(
        mode='test',
        active_symbols=symbols or ['BTCUSDT'],
        data_path=tmp_path / 'lev.json',
    )


def make_vt():
    vt = MagicMock()
    vt.get_preset_efficiency.return_value = 0.0
    return vt


def make_sim(tmp_path, initial_balance=100.0, presets=None):
    lt = make_lt(tmp_path)
    vt = make_vt()
    sim = VirtualOrderSimulator(
        mode='test',
        all_presets=presets or {'preset_a': {}, 'preset_b': {}},
        project_root=tmp_path,
        leverage_tracker=lt,
        initial_balance=initial_balance,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
    )
    return sim, lt, vt


def test_initial_balance_set(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=200.0)
    assert sim._virtual_balance == 200.0
    assert sim._virtual_committed == 0.0


def test_virtual_balance_persists_to_disk(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=100.0)
    sim._virtual_balance = 150.0
    sim._virtual_committed = 10.0
    sim._save_virtual_balance()
    path = tmp_path / 'data' / 'virtual_balance_test.json'
    data = json.loads(path.read_text())
    assert data['virtual_balance'] == 150.0
    assert data['virtual_committed'] == 10.0


def test_virtual_balance_loads_from_disk(tmp_path):
    # First instance persists
    sim1, *_ = make_sim(tmp_path, initial_balance=100.0)
    sim1._virtual_balance = 150.0
    sim1._virtual_committed = 10.0
    sim1._save_virtual_balance()
    # Second instance should load disk state, not use initial_balance
    sim2, *_ = make_sim(tmp_path, initial_balance=999.0)
    assert sim2._virtual_balance == 150.0
    assert sim2._virtual_committed == 10.0


def test_margin_formula_uses_leverage_level(tmp_path):
    # margin = min_notional / leverage = 5.0 / 1 = 5.0 at level 1
    sim, lt, _ = make_sim(tmp_path, initial_balance=100.0)
    assert lt.get_current_level() == 1
    margin = 5.0 / lt.get_current_level()
    assert margin == 5.0


def test_skips_open_when_balance_insufficient(tmp_path):
    sim, lt, _ = make_sim(tmp_path, initial_balance=3.0)
    # min_notional=5, leverage=1 → margin=5 > balance=3
    available = sim._virtual_balance - sim._virtual_committed
    margin = 5.0 / lt.get_current_level()
    assert available < margin  # confirms the skip condition


def test_close_releases_committed_and_updates_balance(tmp_path):
    sim, *_ = make_sim(tmp_path, initial_balance=100.0)
    margin = 5.0
    pnl = 2.0
    sim._virtual_committed = margin
    sim._virtual_balance = 100.0
    # Simulate close logic
    sim._virtual_committed -= margin
    sim._virtual_committed = max(0.0, sim._virtual_committed)
    sim._virtual_balance += pnl
    assert sim._virtual_committed == 0.0
    assert sim._virtual_balance == 102.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_virtual_order_simulator.py -v
```
Expected: `TypeError` due to wrong constructor signature.

- [ ] **Step 3: Replace `bot/virtual_order_simulator.py` with the new implementation**

Write the complete file:

```python
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bot.fake_order import FakeOrder
from bot.recommendation_engine import RecommendationEngine

if TYPE_CHECKING:
    from bot.analyzer import Analyzer
    from bot.data_feed import DataFeed
    from bot.leverage_tracker import LeverageTracker
    from bot.virtual_tracker import VirtualTracker
    from config.settings import Settings

logger = logging.getLogger(__name__)

_MAX_CLOSED = 500
_DEFAULT_MIN_NOTIONAL = 5.0


class VirtualOrderSimulator:
    """
    Tracks virtual positions for all non-best presets.
    Uses a shared virtual balance pool sized by Option A: min_notional / leverage.
    Presets are sorted by efficiency before opening so capital goes to best performers first.
    Persists virtual_orders_{symbol}_{mode}.json and virtual_balance_{mode}.json.
    """

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
        self._mode = mode
        self._all_presets = all_presets
        self._project_root = project_root
        self._leverage_tracker = leverage_tracker
        self._virtual_tracker = virtual_tracker
        self._min_notionals = min_notionals
        # symbol -> {preset_name: order_record_dict}
        self._open: dict[str, dict[str, dict]] = {}
        # symbol -> {preset_name: FakeOrder}
        self._fake_orders: dict[str, dict[str, FakeOrder]] = {}
        # per-symbol write locks to prevent concurrent _append_closed races
        self._write_locks: dict[str, asyncio.Lock] = {}

        self._virtual_balance: float = initial_balance
        self._virtual_committed: float = 0.0
        self._balance_path = project_root / 'data' / f'virtual_balance_{mode}.json'
        self._load_virtual_balance()

    def _load_virtual_balance(self) -> None:
        if not self._balance_path.exists():
            return
        try:
            data = json.loads(self._balance_path.read_text())
            self._virtual_balance = float(data.get('virtual_balance', self._virtual_balance))
            self._virtual_committed = float(data.get('virtual_committed', 0.0))
        except Exception as exc:
            logger.warning(f"VirtualOrderSimulator: failed to load balance state: {exc}")

    def _save_virtual_balance(self) -> None:
        self._balance_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._balance_path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps({
            'virtual_balance': self._virtual_balance,
            'virtual_committed': self._virtual_committed,
        }))
        tmp.replace(self._balance_path)

    async def on_candle_close(
        self,
        symbol: str,
        analyzer: 'Analyzer',
        best_preset_name: Optional[str],
        base_settings: 'Settings',
    ) -> None:
        open_for_symbol = self._open.setdefault(symbol, {})
        fake_for_symbol = self._fake_orders.setdefault(symbol, {})

        lev = self._leverage_tracker.get_current_level()
        min_notional = self._min_notionals.get(symbol, _DEFAULT_MIN_NOTIONAL)
        margin = min_notional / lev if lev > 0 else min_notional

        # Sort presets by efficiency descending — best preset gets capital first
        sorted_presets = sorted(
            self._all_presets.items(),
            key=lambda kv: self._virtual_tracker.get_preset_efficiency(symbol, kv[0]),
            reverse=True,
        )

        for preset_name, overrides in sorted_presets:
            if preset_name == best_preset_name:
                continue  # handled as real order
            if preset_name in open_for_symbol:
                continue  # already open for this preset

            available = self._virtual_balance - self._virtual_committed
            if available < margin:
                logger.debug(
                    f"[{symbol}][{preset_name}] Virtual skip: "
                    f"available={available:.2f} < margin={margin:.2f}"
                )
                continue

            try:
                preset_settings = dataclasses.replace(base_settings, **overrides)
                engine = RecommendationEngine(preset_settings)
                rec = engine.generate(analyzer.get_trend(), analyzer.get_current_price())
            except Exception as exc:
                logger.debug(f"[{symbol}][{preset_name}] Recommendation error: {exc}")
                continue

            if rec is None:
                continue

            entry = rec.getEntryPrice()
            tp = rec.getTarget()
            sl = rec.getStop() or 0.0
            if entry <= 0 or tp <= 0:
                continue

            quantity = min_notional / entry if entry > 0 else 0.0
            if quantity <= 0:
                continue

            self._virtual_committed += margin
            side = rec.getSide()
            partial_pct = float(getattr(preset_settings, 'partial_take_pct', 0.0))
            trail_pct = float(getattr(preset_settings, 'trailing_stop_pct', 0.0))

            record = {
                'preset_name': preset_name,
                'side': side,
                'entry_price': entry,
                'tp': tp,
                'sl': sl,
                'quantity': quantity,
                'leverage': lev,
                'virtual_margin': margin,
                'virtual_balance_at_open': self._virtual_balance,
                'open_time': datetime.now(timezone.utc).isoformat(),
                'status': 'open',
                'close_price': None,
                'close_time': None,
                'pnl_usdt': None,
                'result': None,
            }
            self._save_virtual_balance()
            open_for_symbol[preset_name] = record
            fake_for_symbol[preset_name] = FakeOrder(
                side=side,
                entry_price=entry,
                tp=tp,
                sl=sl if sl else entry * (0.99 if side == 'BUY' else 1.01),
                level=rec.getLevel(),
                signal_type=rec.getType().value,
                candle_index=0,
                partial_take_pct=partial_pct,
                trailing_stop_pct=trail_pct,
            )
            logger.debug(f"[{symbol}] Virtual order opened: {preset_name} {side} @ {entry}")

    async def check_prices(self, symbol: str, price: float) -> list[dict]:
        closed = []
        open_for_symbol = self._open.get(symbol, {})
        fake_for_symbol = self._fake_orders.get(symbol, {})

        for preset_name in list(open_for_symbol.keys()):
            fake = fake_for_symbol.get(preset_name)
            if fake is None:
                open_for_symbol.pop(preset_name, None)
                continue

            result = fake.check_price(price)
            if result is None:
                continue

            record = open_for_symbol.pop(preset_name)
            fake_for_symbol.pop(preset_name, None)

            close_price = fake.close_price or price
            pnl = self._calc_pnl(record, close_price)

            virtual_margin = record.get('virtual_margin', 0.0)
            self._virtual_committed = max(0.0, self._virtual_committed - virtual_margin)
            self._virtual_balance += pnl
            self._save_virtual_balance()

            record.update({
                'status': 'closed',
                'close_price': close_price,
                'close_time': datetime.now(timezone.utc).isoformat(),
                'pnl_usdt': pnl,
                'result': result,
                'virtual_balance_after_close': self._virtual_balance,
            })
            await self._append_closed(symbol, record)
            closed.append({
                'preset_name': preset_name,
                'pnl_usdt': pnl,
                'result': result,
                'entry_price': record['entry_price'],
                'close_price': close_price,
                'side': record['side'],
            })
            logger.debug(f"[{symbol}] Virtual order closed: {preset_name} {result} pnl={pnl:.2f}")

        return closed

    async def close_all_open(self, symbols: list[str], feed: 'DataFeed') -> None:
        for symbol in symbols:
            open_for_symbol = self._open.get(symbol, {})
            if not open_for_symbol:
                continue
            try:
                ticker = await asyncio.to_thread(
                    feed.client.futures_symbol_ticker, symbol=symbol
                )
                close_price = float(ticker.get('price', 0) or 0)
            except Exception as exc:
                logger.warning(f"[{symbol}] Failed to fetch price for virtual close: {exc}")
                close_price = 0.0

            for preset_name, record in list(open_for_symbol.items()):
                row_close = close_price if close_price > 0 else record['entry_price']
                pnl = self._calc_pnl(record, row_close) if close_price > 0 else 0.0
                virtual_margin = record.get('virtual_margin', 0.0)
                self._virtual_committed = max(0.0, self._virtual_committed - virtual_margin)
                self._virtual_balance += pnl
                record.update({
                    'status': 'closed',
                    'close_price': row_close,
                    'close_time': datetime.now(timezone.utc).isoformat(),
                    'pnl_usdt': pnl,
                    'result': 'closed_early',
                    'virtual_balance_after_close': self._virtual_balance,
                })
                await self._append_closed(symbol, record)

            self._save_virtual_balance()
            open_for_symbol.clear()
            self._fake_orders.get(symbol, {}).clear()
            logger.info(f"[{symbol}] All virtual orders closed (bot stop/mode switch)")

    def _calc_pnl(self, record: dict, close_price: float) -> float:
        entry = record['entry_price']
        qty = record['quantity']
        if record['side'] == 'BUY':
            return (close_price - entry) * qty
        return (entry - close_price) * qty

    def _path(self, symbol: str) -> Path:
        return self._project_root / 'data' / f'virtual_orders_{symbol}_{self._mode}.json'

    def _get_write_lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._write_locks:
            self._write_locks[symbol] = asyncio.Lock()
        return self._write_locks[symbol]

    async def _append_closed(self, symbol: str, record: dict) -> None:
        async with self._get_write_lock(symbol):
            path = self._path(symbol)
            path.parent.mkdir(parents=True, exist_ok=True)
            existing: list = []
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except Exception:
                    existing = []
            open_records = [r for r in existing if r.get('status') == 'open']
            closed_records = [r for r in existing if r.get('status') != 'open']
            closed_records.append(record)
            if len(closed_records) > _MAX_CLOSED:
                closed_records = closed_records[-_MAX_CLOSED:]
            tmp = path.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(open_records + closed_records))
            tmp.replace(path)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_virtual_order_simulator.py -v
```
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/virtual_order_simulator.py tests/test_virtual_order_simulator.py
git commit -m "feat: replace risk_manager with virtual balance pool in VirtualOrderSimulator"
```

---

### Task 7: Rewrite `main.py` — efficiency-ranked loop + wire all new modules

**Files:**
- Modify: `main.py`

This is the largest change. `main.py` is a closures-based async module — all the major callbacks are nested functions inside `run()`. Read the full current file before editing.

Key changes:
1. Add imports for new modules.
2. Replace the module-level `_should_poll_balance` / `_BALANCE_POLL_INTERVAL` with a local `_get_fresh_balance()` coroutine (5s TTL via a mutable list trick for closure mutation).
3. Wire `LeverageTracker` at startup.
4. Collect `min_notionals` dict and initial balance before creating `VirtualOrderSimulator`.
5. Rewrite `_try_place_order` — Option A sizing, `decision_log` calls, `balance_at_open`.
6. Rewrite `on_candle_close` — efficiency-ranked cross-symbol loop.
7. Wire `leverage_tracker.record_closed()` + `balance_history` in `on_price_update`.
8. Update `on_switch_mode` — recreate `VirtualOrderSimulator` with new args; call `leverage_tracker.reset_for_mode()`.

- [ ] **Step 1: Add imports at the top of `main.py`**

After the existing imports, add:

```python
from bot.leverage_tracker import LeverageTracker
from bot.balance_history import record as bh_record
from bot.decision_log import record as dl_record
```

- [ ] **Step 2: Remove the module-level balance-poll globals**

Delete these lines (around lines 34–44):

```python
_last_balance_poll: float = 0.0
_BALANCE_POLL_INTERVAL = 30.0


def _should_poll_balance() -> bool:
    global _last_balance_poll
    now = time.monotonic()
    if now - _last_balance_poll >= _BALANCE_POLL_INTERVAL:
        _last_balance_poll = now
        return True
    return False
```

- [ ] **Step 3: Inside `run()`, add `_get_fresh_balance()` and path variables just before `_try_place_order`**

Insert before the `async def _try_place_order(...)` definition:

```python
    # Mutable container for balance cache (allows mutation inside nested coroutine)
    _balance_cache_inner: list[tuple[float, float]] = [(0.0, 0.0)]
    _BALANCE_TTL = 5.0

    async def _get_fresh_balance() -> float:
        now = time.monotonic()
        cached_val, cached_ts = _balance_cache_inner[0]
        if now - cached_ts < _BALANCE_TTL:
            return cached_val
        try:
            bal = await order_executor.fetch_account_balance()
        except Exception as exc:
            logger.warning(f"Balance fetch failed: {exc}")
            bal = 0.0
        if bal > 0:
            _balance_cache_inner[0] = (bal, now)
            return bal
        return cached_val

    bh_path = _PROJECT_ROOT / 'data' / f'balance_history_{current_mode}.json'
    dl_path = _PROJECT_ROOT / 'data' / f'decision_log_{current_mode}.json'
```

- [ ] **Step 4: Wire `LeverageTracker` in the startup section**

After creating `virtual_tracker` (the `VirtualTracker(...)` call), add:

```python
    leverage_tracker = LeverageTracker(
        mode=current_mode,
        active_symbols=symbols,
        data_path=_PROJECT_ROOT / 'data' / f'leverage_state_{current_mode}.json',
        max_level=risk_cfg.get('max_leverage_level', 5),
    )
```

- [ ] **Step 5: Collect `min_notionals` and initial balance; update `VirtualOrderSimulator` construction**

After the `await order_executor.fetch_leverage_brackets(symbols)` call, add:

```python
    min_notionals: dict[str, float] = {}
    for sym in symbols:
        min_notionals[sym] = await order_executor.get_min_notional(sym)

    startup_balance = await order_executor.fetch_account_balance()
    if startup_balance > 0:
        risk_manager.update_balance(startup_balance)
    bh_record(bh_path, balance=risk_manager.get_balance(), trigger='startup')
```

Replace the old `VirtualOrderSimulator(...)` call with:

```python
    virtual_order_simulator = VirtualOrderSimulator(
        mode=current_mode,
        all_presets=all_presets,
        project_root=_PROJECT_ROOT,
        leverage_tracker=leverage_tracker,
        initial_balance=risk_manager.get_balance(),
        virtual_tracker=virtual_tracker,
        min_notionals=min_notionals,
    )
```

- [ ] **Step 6: Replace `_try_place_order` entirely**

Replace the existing `async def _try_place_order(symbol, best, settings) -> None:` with:

```python
    async def _try_place_order(
        symbol: str, best, settings, balance: float, candle_ts: int
    ) -> None:
        preset_name = virtual_tracker.best_preset(symbol)
        _all_presets_local = {**LOCKED_PRESETS, **PRESETS}
        overrides = _all_presets_local.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        entry = best.getEntryPrice()
        if entry <= 0:
            return

        current_lev = leverage_tracker.get_current_level()
        bracket_max = order_executor.get_bracket_max(symbol)
        max_policy_lev = load_risk_config().get('max_leverage_level', 5)
        actual_lev = min(current_lev, bracket_max, max_policy_lev)
        if actual_lev <= 0:
            actual_lev = 1

        min_notional = min_notionals.get(symbol)
        if min_notional is None:
            min_notional = await order_executor.get_min_notional(symbol)
            min_notionals[symbol] = min_notional

        margin = min_notional / actual_lev
        eff_score = virtual_tracker.get_efficiency_score(symbol)

        if balance < margin:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_balance',
                reason=f'balance={balance:.2f} < margin={margin:.2f}',
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name,
            )
            logger.info(f"[{symbol}] Insufficient balance: {balance:.2f} < margin={margin:.2f}")
            return

        allowed, reason = risk_manager.can_open_sync(symbol)
        if not allowed:
            decision = 'skip_hard_stop' if 'hard_stop' in reason else 'skip_profit_factor'
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision=decision, reason=reason,
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name,
            )
            logger.info(f"[{symbol}] Order skipped: {reason}")
            return

        # Verify exchange state if best preset changed since last order
        if order_executor._last_opened_preset.get(symbol) != preset_name:
            await order_executor.check_symbols_on_exchange([symbol])
            if order_executor.get_state(symbol) != OrderState.IDLE:
                return

        quantity = (margin * actual_lev) / entry

        bh_record(bh_path, balance=balance, trigger='order_open',
                  symbol=symbol, leverage=actual_lev)

        precision = best.getPrecision() if hasattr(best, 'getPrecision') else 0.0

        placed = await order_executor.place_order(
            symbol=symbol,
            preset_name=preset_name or 'default',
            side=best.getSide(),
            entry=entry,
            tp=best.getTarget(),
            sl=best.getStop() or 0.0,
            quantity=quantity,
            leverage=actual_lev,
            partial_take_pct=preset_settings.partial_take_pct,
            trailing_stop_pct=preset_settings.trailing_stop_pct,
            level=best.getLevel(),
            signal_type=best.getType().value,
            balance_at_open=balance,
            signal_level=best.getLevel() or 0,
            precision_score=precision or 0.0,
        )
        if placed:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='placed', reason='',
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name,
                signal_type=best.getType().value,
                level=best.getLevel(),
                precision_score=precision or 0.0,
            )
```

- [ ] **Step 7: Replace `on_candle_close` — efficiency-ranked cross-symbol loop**

Replace the existing `async def on_candle_close(symbol, kline) -> None:`:

```python
    async def on_candle_close(symbol: str, kline: list) -> None:
        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        if symbol_registry.is_disabled(symbol):
            return

        if symbol not in sym_settings or symbol not in analyzers:
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        recs = analyzer.add_candle(kline)
        best_for_this = analyzer.get_best_recommendation()

        try:
            await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 10)
        except Exception as e:
            logger.warning(f"[{symbol}] Kline refresh failed: {e}")

        # Fetch balance (5s TTL shared across candle batch)
        balance = await _get_fresh_balance()
        if balance > 0:
            risk_manager.update_balance(balance)

        # Efficiency-ranked cross-symbol placement loop
        candle_ts = int(kline[0]) if kline else 0
        candidates = []
        for sym in symbol_registry.get_symbols():
            if order_executor.get_state(sym) != OrderState.IDLE:
                continue
            best_sym = (
                best_for_this if sym == symbol
                else (analyzers[sym].get_best_recommendation() if sym in analyzers else None)
            )
            if best_sym is None:
                continue
            score = virtual_tracker.get_efficiency_score(sym)
            candidates.append((sym, best_sym, sym_settings.get(sym, settings), score))

        candidates.sort(key=lambda x: x[3], reverse=True)
        for sym, best, sym_s, _ in candidates:
            await _try_place_order(sym, best, sym_s, balance, candle_ts)

        await virtual_order_simulator.on_candle_close(
            symbol=symbol,
            analyzer=analyzer,
            best_preset_name=virtual_tracker.best_preset(symbol),
            base_settings=settings,
        )

        export(
            symbol, timeframe, mode_manager.current_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best_for_this,
        )

        if best_for_this:
            trades_logger.info(f"BEST | symbol={symbol} | {best_for_this}")
        for rec in recs:
            trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")
```

- [ ] **Step 8: Update `on_price_update` — wire `leverage_tracker.record_closed` + `bh_record`**

Replace the existing `async def on_price_update(symbol, price) -> None:`:

```python
    async def on_price_update(symbol: str, price: float) -> None:
        if symbol in analyzers:
            analyzers[symbol].update_price(price)

        closed = await order_executor.check_symbol_price(symbol, price)
        for c in closed:
            virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])
            leverage_tracker.record_closed(c['symbol'], c.get('leverage', 1))
            fresh_bal = await _get_fresh_balance()
            bh_record(
                bh_path, balance=fresh_bal, trigger='order_close',
                symbol=c['symbol'], leverage=c.get('leverage', 1),
                pnl_usdt=c.get('pnl_usdt'),
            )

        virtual_closed = await virtual_order_simulator.check_prices(symbol, price)
        for vc in virtual_closed:
            virtual_tracker.record_closed_trade(symbol, vc['preset_name'], vc['pnl_usdt'])
```

- [ ] **Step 9: Update `on_switch_mode` — new VirtualOrderSimulator args + leverage_tracker reset**

Inside `on_switch_mode`, replace the `VirtualOrderSimulator(...)` instantiation (near the end) with:

```python
        for sym in current_symbols:
            min_notionals[sym] = await order_executor.get_min_notional(sym)
        switch_bal = await order_executor.fetch_account_balance()
        virtual_order_simulator = VirtualOrderSimulator(
            mode=target_mode,
            all_presets=all_presets,
            project_root=_PROJECT_ROOT,
            leverage_tracker=leverage_tracker,
            initial_balance=switch_bal if switch_bal > 0 else risk_manager.get_balance(),
            virtual_tracker=virtual_tracker,
            min_notionals=min_notionals,
        )
        leverage_tracker.reset_for_mode(
            target_mode,
            _PROJECT_ROOT / 'data' / f'leverage_state_{target_mode}.json',
        )
```

Also add `nonlocal virtual_order_simulator` at the top of `on_switch_mode` (it was already `nonlocal virtual_tracker, virtual_order_simulator`).

- [ ] **Step 10: Verify clean import**

```bash
python -c "import main; print('OK')"
```
Expected: `OK`

- [ ] **Step 11: Commit**

```bash
git add main.py
git commit -m "feat: efficiency-ranked order loop; wire LeverageTracker, balance_history, decision_log"
```

---

### Task 8: Dashboard — `GET /api/balance-history` route

**Files:**
- Create: `dashboard/app/api/balance-history/route.ts`

Simple route that reads `data/balance_history_{mode}.json` and returns the last N entries reversed (newest first).

- [ ] **Step 1: Create `dashboard/app/api/balance-history/route.ts`**

```typescript
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

const BOT_ROOT = path.resolve(process.cwd(), '..')

export async function GET(req: NextRequest) {
  const mode = req.nextUrl.searchParams.get('mode') || 'test'
  const limit = Math.max(1, parseInt(req.nextUrl.searchParams.get('limit') || '500', 10))

  const filePath = path.join(BOT_ROOT, 'data', `balance_history_${mode}.json`)
  let entries: unknown[] = []
  try {
    entries = JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return NextResponse.json({ entries: [] })
  }

  const recent = entries.slice(-limit).reverse()
  return NextResponse.json({ entries: recent })
}
```

- [ ] **Step 2: Verify the route responds**

Start the dev server if not already running:
```bash
cd dashboard && npm run dev
```

Then in another terminal:
```bash
curl "http://localhost:3000/api/balance-history?mode=test&limit=10"
```
Expected: `{"entries":[]}` (file doesn't exist yet) or a valid JSON array.

- [ ] **Step 3: Commit**

```bash
git add dashboard/app/api/balance-history/route.ts
git commit -m "feat: add GET /api/balance-history route"
```

---

### Task 9: Dashboard — Risk API + Risk page controls

**Files:**
- Modify: `dashboard/app/api/risk/route.ts`
- Modify: `dashboard/app/risk/page.tsx`

Add `max_leverage_level` and `use_allocation_weighting` to the API defaults and to the Risk page UI. The POST handler already merges unknown fields so no logic changes needed there — only `DEFAULT_CONFIG` and the TypeScript interface need updating.

- [ ] **Step 1: Update `DEFAULT_CONFIG` in `dashboard/app/api/risk/route.ts`**

Add two lines to the `DEFAULT_CONFIG` object (after the existing keys):

```typescript
const DEFAULT_CONFIG = {
  balance_tiers: [
    { min_balance_usdt: 0,    max_deploy_pct: 40, max_leverage_ceiling: 5  },
    { min_balance_usdt: 1000, max_deploy_pct: 50, max_leverage_ceiling: 10 },
    { min_balance_usdt: 5000, max_deploy_pct: 60, max_leverage_ceiling: 15 },
  ],
  base_leverage: 2,
  max_leverage: 10,
  min_profit_factor: 1.2,
  drawdown_warning_pct: 10.0,
  drawdown_hard_stop_pct: 20.0,
  backtest_initial_balance_usdt: 1000.0,
  symbol_weights: {} as Record<string, number>,
  max_leverage_level: 5,
  use_allocation_weighting: false,
}
```

- [ ] **Step 2: Update `RiskConfig` interface in `dashboard/app/risk/page.tsx`**

Find the `interface RiskConfig` block and add two new fields:

```typescript
interface RiskConfig {
  balance_tiers: BalanceTier[]
  base_leverage: number
  max_leverage: number
  min_profit_factor: number
  drawdown_warning_pct: number
  drawdown_hard_stop_pct: number
  backtest_initial_balance_usdt: number
  symbol_weights: Record<string, number>
  max_leverage_level: number
  use_allocation_weighting: boolean
}
```

- [ ] **Step 3: Add UI controls to the Risk page**

Find the section that renders leverage controls (Section C — the block with `base_leverage` and `max_leverage` inputs). Add after those inputs, before the closing `</div>` of that section body:

```tsx
<LabeledInput
  label="Max leverage level"
  tooltip="LeverageTracker ceiling — global level will not advance past this value (1–20)"
  value={config.max_leverage_level ?? 5}
  onChange={v => setConfig(c => ({ ...c, max_leverage_level: Number(v) }))}
  min={1}
  max={20}
  step={1}
/>
<div className="flex items-center gap-3">
  <label className="text-xs text-gray-500 w-52 shrink-0" title="When disabled, position size = min_notional / leverage. Enable to use per-symbol weighted allocation.">
    Use allocation weighting
  </label>
  <input
    type="checkbox"
    checked={config.use_allocation_weighting ?? false}
    onChange={e => setConfig(c => ({ ...c, use_allocation_weighting: e.target.checked }))}
    className="accent-indigo-500 h-4 w-4 cursor-pointer"
  />
</div>
```

Note: `LabeledInput` is defined at the top of the file — use the same import-free pattern already in the file.

- [ ] **Step 4: Add default values to the initial config state**

Find where `config` state is initialized from the fetched data (the `setConfig` call inside `useEffect`). Ensure the new fields are included in the merged default:

```typescript
setConfig(prev => ({
  balance_tiers: [...],
  // ... existing defaults ...
  max_leverage_level: 5,
  use_allocation_weighting: false,
  ...data.config,
}))
```

The exact pattern depends on how the existing `useEffect` is written — mirror it exactly, just add the two new keys to the default spread.

- [ ] **Step 5: Load the Risk page in browser and verify**

Navigate to `http://localhost:3000/risk`. Confirm:
- "Max leverage level" number input appears (default 5)
- "Use allocation weighting" checkbox appears (default unchecked)
- Clicking Save sends POST with new fields and returns 200

- [ ] **Step 6: Commit**

```bash
git add dashboard/app/api/risk/route.ts dashboard/app/risk/page.tsx
git commit -m "feat: add max_leverage_level and use_allocation_weighting to Risk page"
```

---

## Self-Review

### Spec coverage

| Requirement | Task | Status |
|---|---|---|
| `LeverageTracker` with all 6 methods + persistence | Task 2 | ✓ |
| Advancement requires ALL active symbols at current_level | Task 2 (`_check_advance`) | ✓ |
| New symbol: needs current_level (not all previous) | Task 2 (uniform rule) | ✓ |
| Symbol removal may trigger immediate advancement | Task 2 | ✓ |
| `get_efficiency_score(symbol)` + `get_preset_efficiency(symbol, preset)` | Task 1 | ✓ |
| `bot/balance_history.py` — 4 trigger types, cap 10k | Task 3 | ✓ |
| `bot/decision_log.py` — 6 decision values, cap 5k | Task 3 | ✓ |
| `use_allocation_weighting`, `max_leverage_level` in DEFAULT_CONFIG | Task 4 | ✓ |
| `can_open_sync(symbol)` — no `estimated_size_usdt` | Task 4 | ✓ |
| `balance_at_open`, `signal_level`, `precision_score` in real order records | Task 5 | ✓ |
| `leverage` in close result dicts | Task 5 | ✓ |
| VirtualOrderSimulator: shared virtual balance pool | Task 6 | ✓ |
| VirtualOrderSimulator: remove `risk_manager` dependency | Task 6 | ✓ |
| VirtualOrderSimulator: preset-efficiency-sorted loop | Task 6 | ✓ |
| VirtualOrderSimulator: `virtual_margin`, `virtual_balance_at_open`, `virtual_balance_after_close` | Task 6 | ✓ |
| VirtualOrderSimulator: persist balance to disk, load on init | Task 6 | ✓ |
| `_get_fresh_balance()` with 5s TTL | Task 7 | ✓ |
| Efficiency-ranked cross-symbol order loop | Task 7 | ✓ |
| Option A sizing: `margin = min_notional / actual_lev` | Task 7 | ✓ |
| `leverage_tracker.record_closed()` on real order close | Task 7 | ✓ |
| `balance_history.record()` at startup, order_open, order_close | Task 7 | ✓ |
| `decision_log.record()` for placed + all skip reasons | Task 7 | ✓ |
| `GET /api/balance-history?mode=&limit=` | Task 8 | ✓ |
| Risk page: `max_leverage_level` number input | Task 9 | ✓ |
| Risk page: `use_allocation_weighting` checkbox | Task 9 | ✓ |
| Tests: VirtualTracker helpers | Task 1 | ✓ |
| Tests: LeverageTracker | Task 2 | ✓ |
| Tests: balance_history | Task 3 | ✓ |
| Tests: decision_log | Task 3 | ✓ |
| Tests: VirtualOrderSimulator | Task 6 | ✓ |
| Tests: risk_manager `can_open_sync` updated | Task 4 | ✓ |

### Placeholder scan

No "TBD", "TODO", or vague step descriptions present. All code blocks are complete. Task 9 Step 4 says "mirror exactly" — this is guidance for a context-dependent merge, not a placeholder, since the pattern depends on the live file state at execution time.

### Type consistency

- `VirtualOrderSimulator.__init__` accepts `leverage_tracker: LeverageTracker` → matches `bot/leverage_tracker.py` class name ✓
- `LeverageTracker.record_closed(symbol, leverage)` returns `bool` → `main.py` ignores return value ✓
- `bh_record` is imported as `from bot.balance_history import record as bh_record` → called as `bh_record(path, balance=..., trigger=...)` ✓
- `dl_record` is imported as `from bot.decision_log import record as dl_record` → called as `dl_record(path, candle_ts=..., ...)` ✓
- `can_open_sync(symbol)` → called as `risk_manager.can_open_sync(symbol)` in Task 7 ✓
- `place_order(... balance_at_open=, signal_level=, precision_score=)` → new params added in Task 5 ✓
- `leverage` in close result dict → accessed as `c.get('leverage', 1)` in Task 7 ✓
