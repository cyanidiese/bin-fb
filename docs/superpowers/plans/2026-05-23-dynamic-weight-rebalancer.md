# Dynamic Weight Rebalancer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Periodically recompute per-symbol allocation weights by combining a mini-backtest on recent klines with real closed P&L, soft-blending into existing weights so capital gradually flows toward better-performing symbols.

**Architecture:** A new `WeightRebalancer` class fires every N closed candles in a background thread, scores each active symbol (rank-normalized backtest profit % + real P&L), soft-blends toward new scores with a floor clamp, and persists updated `symbol_weights` to `risk_config.json`. The bot reads this file on every allocation call already, so no other plumbing changes are needed. Disabled by default via `weight_rebalancer.enabled = false`.

**Tech Stack:** Python 3.12, `threading.Thread` (daemon), `bot/backtester.py` (`Backtester` class), `config/risk_config.py` (`save_risk_config`), Next.js 15 App Router, TypeScript, Tailwind v4.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `bot/weight_rebalancer.py` | `WeightRebalancer` — scoring, blending, threading |
| Create | `tests/test_weight_rebalancer.py` | Unit tests for all rebalancer logic |
| Modify | `config/risk_config.py` | Add `weight_rebalancer` block to `DEFAULT_CONFIG` |
| Modify | `main.py` | Instantiate `WeightRebalancer`; call `on_candle_close` |
| Modify | `dashboard/lib/risk-types.ts` | Add `WeightRebalancerConfig`, `WeightRebalanceLogEntry` types |
| Modify | `dashboard/app/api/risk/route.ts` | Add `weight_rebalancer` to TS `DEFAULT_CONFIG` |
| Create | `dashboard/components/risk/WeightRebalancerSection.tsx` | UI section (toggle, sliders, status table) |
| Modify | `dashboard/app/risk/page.tsx` | Import and render `WeightRebalancerSection` |

---

## Task 1: Python config defaults

**Files:**
- Modify: `config/risk_config.py`

- [ ] **Step 1: Add `weight_rebalancer` to `DEFAULT_CONFIG`**

In `config/risk_config.py`, after the `"scenario"` key (line ~45), add:

```python
    "weight_rebalancer": {
        "enabled": False,
        "rebalance_candles": 96,
        "backtest_window_candles": 96,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.15,
        "weight_floor_ratio": 0.3,
    },
```

- [ ] **Step 2: Verify the new key appears in a freshly loaded config**

```bash
cd /path/to/project
python -c "
from config.risk_config import DEFAULT_CONFIG, load_risk_config
assert 'weight_rebalancer' in DEFAULT_CONFIG
cfg = load_risk_config()
assert 'weight_rebalancer' in cfg
assert cfg['weight_rebalancer']['enabled'] == False
assert cfg['weight_rebalancer']['rebalance_candles'] == 96
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add config/risk_config.py
git commit -m "feat: add weight_rebalancer defaults to risk_config"
```

---

## Task 2: `WeightRebalancer` — pure scoring helpers (TDD)

**Files:**
- Create: `bot/weight_rebalancer.py`
- Create: `tests/test_weight_rebalancer.py`

The pure helpers (`_rank_normalize`, `_filter_real_orders`, `_calc_scores`) have no I/O — test them directly without mocking anything.

- [ ] **Step 1: Write failing tests for `_rank_normalize`**

Create `tests/test_weight_rebalancer.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from bot.weight_rebalancer import WeightRebalancer


def _make_rebalancer(**cfg_overrides):
    """Build a WeightRebalancer with all deps mocked."""
    cfg = {
        "enabled": True,
        "rebalance_candles": 4,
        "backtest_window_candles": 4,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.2,
        "weight_floor_ratio": 0.3,
        **cfg_overrides,
    }
    sym_reg = MagicMock()
    risk_mgr = MagicMock()
    settings = MagicMock()
    return WeightRebalancer(
        symbol_registry=sym_reg,
        risk_manager=risk_mgr,
        settings=settings,
        get_klines_fn=lambda s: [],
        candle_duration_ms=900_000,
        mode="test",
        risk_config_path=Path("/tmp/rc.json"),
        data_dir=Path("/tmp/data"),
        cfg=cfg,
    )


class TestRankNormalize:
    def test_three_values_ranked(self):
        r = _make_rebalancer()
        values = {"A": 10.0, "B": 5.0, "C": 1.0}
        result = r._rank_normalize(values)
        assert result["A"] == pytest.approx(1.0)
        assert result["C"] == pytest.approx(0.0)
        assert result["B"] == pytest.approx(0.5)

    def test_all_equal_scores_midpoint(self):
        r = _make_rebalancer()
        values = {"A": 3.0, "B": 3.0, "C": 3.0}
        result = r._rank_normalize(values)
        # All ties → all get midpoint rank score
        for v in result.values():
            assert v == pytest.approx(0.5)

    def test_single_symbol_returns_one(self):
        r = _make_rebalancer()
        result = r._rank_normalize({"X": 7.5})
        assert result["X"] == pytest.approx(1.0)

    def test_empty_returns_empty(self):
        r = _make_rebalancer()
        assert r._rank_normalize({}) == {}
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
pytest tests/test_weight_rebalancer.py::TestRankNormalize -v
```

Expected: `ImportError` or `ModuleNotFoundError` (file doesn't exist yet).

- [ ] **Step 3: Create `bot/weight_rebalancer.py` with `_rank_normalize`**

```python
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.risk_config import load_risk_config, save_risk_config
from config.settings import Settings

logger = logging.getLogger(__name__)

_LOG_MAX = 50


class WeightRebalancer:
    def __init__(
        self,
        symbol_registry,
        risk_manager,
        settings: Settings,
        get_klines_fn: Callable[[str], list],
        candle_duration_ms: int,
        mode: str,
        risk_config_path: Path,
        data_dir: Path,
        cfg: dict,
    ) -> None:
        self._registry = symbol_registry
        self._risk = risk_manager
        self._settings = settings
        self._get_klines = get_klines_fn
        self._candle_ms = candle_duration_ms
        self._mode = mode
        self._config_path = risk_config_path
        self._data_dir = data_dir
        self._cfg = cfg
        self._counter = 0
        self._running = threading.Event()  # set while a rebalance is in progress
        self.enabled: bool = bool(cfg.get("enabled", False))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def on_candle_close(self, candle_ts: int) -> None:
        if not self.enabled:
            return
        self._counter += 1
        n = int(self._cfg.get("rebalance_candles", 96))
        if self._counter % n != 0:
            return
        if self._running.is_set():
            logger.warning("WeightRebalancer: previous rebalance still running — skipping")
            return
        self._running.set()
        t = threading.Thread(target=self._run, args=(candle_ts,), daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    # Pure helpers (no I/O, easily unit-tested)                           #
    # ------------------------------------------------------------------ #

    def _rank_normalize(self, values: dict[str, float]) -> dict[str, float]:
        """Map values to [0, 1] by rank. Best → 1.0, worst → 0.0. Ties share midpoint rank."""
        if not values:
            return {}
        n = len(values)
        if n == 1:
            return {k: 1.0 for k in values}
        sorted_keys = sorted(values, key=lambda k: values[k], reverse=True)
        # Group tied values
        result: dict[str, float] = {}
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[sorted_keys[j]] == values[sorted_keys[j + 1]]:
                j += 1
            # Ranks i..j are tied — assign midpoint normalized rank
            mid_rank_score = 1.0 - (i + j) / 2.0 / (n - 1)
            for k in sorted_keys[i:j + 1]:
                result[k] = mid_rank_score
            i = j + 1
        return result

    def _filter_real_orders(
        self, symbol: str, window_start_ms: int
    ) -> list[dict]:
        """Load real_orders file for symbol; return only orders closed within the window."""
        path = self._data_dir / f"real_orders_{symbol}_{self._mode}.json"
        if not path.exists():
            return []
        try:
            records: list[dict] = json.loads(path.read_text())
        except Exception:
            return []
        result = []
        for r in records:
            ct = r.get("close_time")
            if not ct:
                continue
            try:
                close_ms = int(datetime.fromisoformat(ct).timestamp() * 1000)
            except Exception:
                continue
            if close_ms >= window_start_ms:
                result.append(r)
        return result

    def _calc_scores(
        self,
        backtest_pcts: dict[str, float],
        real_pnls: dict[str, float],
        alpha: float,
    ) -> dict[str, float]:
        """Combine rank-normalized backtest and real-P&L signals into a single score."""
        bt_norm = self._rank_normalize(backtest_pcts)
        pnl_norm = self._rank_normalize(real_pnls)
        symbols = set(backtest_pcts) | set(real_pnls)
        return {
            s: alpha * pnl_norm.get(s, 0.5) + (1.0 - alpha) * bt_norm.get(s, 0.5)
            for s in symbols
        }

    def _blend_weights(
        self,
        current_weights: dict[str, float],
        scores: dict[str, float],
        blend_rate: float,
        floor_ratio: float,
    ) -> dict[str, float]:
        """Soft-blend current weights toward scores, clamp floor, renormalize."""
        symbols = list(scores)
        n = len(symbols)
        if n == 0:
            return current_weights
        floor = floor_ratio / n
        new_w: dict[str, float] = {}
        for s in symbols:
            old = current_weights.get(s, 1.0 / n)
            new_w[s] = (1.0 - blend_rate) * old + blend_rate * scores[s]
            new_w[s] = max(floor, new_w[s])
        total = sum(new_w.values())
        return {s: w / total for s, w in new_w.items()}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_weight_rebalancer.py::TestRankNormalize -v
```

Expected: 4 passed.

- [ ] **Step 5: Write failing tests for `_filter_real_orders` and `_calc_scores`**

Append to `tests/test_weight_rebalancer.py`:

```python
import json, tempfile, os
from datetime import datetime, timezone, timedelta


class TestFilterRealOrders:
    def _write_orders(self, tmp_dir: Path, symbol: str, orders: list) -> None:
        path = tmp_dir / f"real_orders_{symbol}_test.json"
        path.write_text(json.dumps(orders))

    def test_returns_orders_in_window(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        recent_ct = datetime.fromtimestamp((now_ms - 1000) / 1000, tz=timezone.utc).isoformat()
        old_ct = datetime.fromtimestamp((now_ms - 99999999) / 1000, tz=timezone.utc).isoformat()
        self._write_orders(tmp_path, "BTCUSDT", [
            {"close_time": recent_ct, "pnl_usdt": 5.0},
            {"close_time": old_ct, "pnl_usdt": -2.0},
        ])
        result = r._filter_real_orders("BTCUSDT", window_start_ms=now_ms - 10000)
        assert len(result) == 1
        assert result[0]["pnl_usdt"] == 5.0

    def test_missing_file_returns_empty(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        result = r._filter_real_orders("XYZUSDT", window_start_ms=0)
        assert result == []

    def test_all_old_orders_excluded(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        old_ct = "2020-01-01T00:00:00+00:00"
        self._write_orders(tmp_path, "ETHUSDT", [
            {"close_time": old_ct, "pnl_usdt": 10.0},
        ])
        result = r._filter_real_orders("ETHUSDT", window_start_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        assert result == []


class TestCalcScores:
    def test_equal_signals_equal_scores(self):
        r = _make_rebalancer()
        bt = {"A": 2.0, "B": 2.0}
        pnl = {"A": 0.0, "B": 0.0}
        scores = r._calc_scores(bt, pnl, alpha=0.5)
        assert scores["A"] == pytest.approx(scores["B"])

    def test_better_backtest_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 5.0, "B": 1.0}
        pnl = {"A": 0.0, "B": 0.0}  # tie on real P&L
        scores = r._calc_scores(bt, pnl, alpha=0.0)  # backtest only
        assert scores["A"] > scores["B"]

    def test_better_pnl_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 0.0, "B": 0.0}  # tie on backtest
        pnl = {"A": 10.0, "B": -3.0}
        scores = r._calc_scores(bt, pnl, alpha=1.0)  # real P&L only
        assert scores["A"] > scores["B"]
```

- [ ] **Step 6: Run to confirm fail**

```bash
pytest tests/test_weight_rebalancer.py::TestFilterRealOrders tests/test_weight_rebalancer.py::TestCalcScores -v
```

Expected: FAIL (methods not yet implemented — they're there, so expect PASS after the class is written — if they fail, debug).

- [ ] **Step 7: Run full test class after confirming all pass**

```bash
pytest tests/test_weight_rebalancer.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add bot/weight_rebalancer.py tests/test_weight_rebalancer.py
git commit -m "feat: add WeightRebalancer with rank-normalize and scoring helpers"
```

---

## Task 3: `WeightRebalancer` — blend/save logic (TDD)

**Files:**
- Modify: `bot/weight_rebalancer.py`
- Modify: `tests/test_weight_rebalancer.py`

- [ ] **Step 1: Write failing tests for `_blend_weights`**

Append to `tests/test_weight_rebalancer.py`:

```python
class TestBlendWeights:
    def test_blend_moves_toward_score(self):
        r = _make_rebalancer()
        current = {"A": 0.5, "B": 0.5}
        scores = {"A": 1.0, "B": 0.0}
        result = r._blend_weights(current, scores, blend_rate=0.2, floor_ratio=0.0)
        # A gains, B loses
        assert result["A"] > 0.5
        assert result["B"] < 0.5

    def test_floor_prevents_zero_weight(self):
        r = _make_rebalancer()
        current = {"A": 0.9, "B": 0.1}
        scores = {"A": 1.0, "B": 0.0}
        # floor_ratio=0.3 → floor = 0.3/2 = 0.15
        result = r._blend_weights(current, scores, blend_rate=0.5, floor_ratio=0.3)
        assert result["B"] >= pytest.approx(0.15 / (result["A"] + 0.15), rel=0.01)

    def test_weights_sum_to_one(self):
        r = _make_rebalancer()
        current = {"A": 0.4, "B": 0.3, "C": 0.3}
        scores = {"A": 0.9, "B": 0.05, "C": 0.05}
        result = r._blend_weights(current, scores, blend_rate=0.15, floor_ratio=0.3)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_empty_scores_returns_current(self):
        r = _make_rebalancer()
        current = {"A": 0.6, "B": 0.4}
        result = r._blend_weights(current, {}, blend_rate=0.15, floor_ratio=0.3)
        assert result == current
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_weight_rebalancer.py::TestBlendWeights -v
```

Expected: FAIL (method exists but floor logic may be off — verify).

- [ ] **Step 3: Fix `_blend_weights` if any tests failed**

The floor clamp after renormalization needs a second pass. Replace the existing `_blend_weights` in `bot/weight_rebalancer.py` with this corrected version that applies the floor in normalized space:

```python
def _blend_weights(
    self,
    current_weights: dict[str, float],
    scores: dict[str, float],
    blend_rate: float,
    floor_ratio: float,
) -> dict[str, float]:
    symbols = list(scores)
    n = len(symbols)
    if n == 0:
        return current_weights
    floor = floor_ratio / n
    # Blend
    new_w: dict[str, float] = {}
    for s in symbols:
        old = current_weights.get(s, 1.0 / n)
        new_w[s] = (1.0 - blend_rate) * old + blend_rate * scores[s]
    # Renormalize once
    total = sum(new_w.values()) or 1.0
    new_w = {s: w / total for s, w in new_w.items()}
    # Clamp floor (in normalized space)
    clamped = {s: max(floor, w) for s, w in new_w.items()}
    # Renormalize again after clamping
    total2 = sum(clamped.values()) or 1.0
    return {s: w / total2 for s, w in clamped.items()}
```

- [ ] **Step 4: Run tests to confirm all pass**

```bash
pytest tests/test_weight_rebalancer.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/weight_rebalancer.py tests/test_weight_rebalancer.py
git commit -m "feat: add blend_weights with floor clamp and renormalization"
```

---

## Task 4: `WeightRebalancer` — `_run()` and `on_candle_close` (TDD)

**Files:**
- Modify: `bot/weight_rebalancer.py`
- Modify: `tests/test_weight_rebalancer.py`

- [ ] **Step 1: Write failing tests for triggering logic**

Append to `tests/test_weight_rebalancer.py`:

```python
class TestTrigger:
    def test_fires_after_n_candles(self):
        r = _make_rebalancer(rebalance_candles=3)
        r.enabled = True
        r._run = MagicMock()
        for i in range(2):
            r.on_candle_close(1000 + i * 900_000)
        r._run.assert_not_called()
        r.on_candle_close(1000 + 2 * 900_000)
        r._run.assert_called_once()

    def test_skips_when_already_running(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = True
        r._run = MagicMock()
        r._running.set()  # simulate in-progress
        r.on_candle_close(1000)
        r._run.assert_not_called()

    def test_no_op_when_disabled(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = False
        r._run = MagicMock()
        r.on_candle_close(1000)
        r._run.assert_not_called()
```

- [ ] **Step 2: Run to confirm fail**

```bash
pytest tests/test_weight_rebalancer.py::TestTrigger -v
```

Expected: FAIL — `on_candle_close` spawns a thread so `_run` won't be called synchronously. We need to patch `threading.Thread` for the test.

- [ ] **Step 3: Patch thread in tests and re-run**

Replace the `TestTrigger` class with a version that patches threading:

```python
class TestTrigger:
    def test_fires_after_n_candles(self):
        r = _make_rebalancer(rebalance_candles=3)
        r.enabled = True
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            for i in range(2):
                r.on_candle_close(1000 + i * 900_000)
            MockThread.assert_not_called()
            r.on_candle_close(1000 + 2 * 900_000)
            MockThread.assert_called_once()

    def test_skips_when_already_running(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = True
        r._running.set()
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()

    def test_no_op_when_disabled(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = False
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()
```

- [ ] **Step 4: Run to confirm pass**

```bash
pytest tests/test_weight_rebalancer.py::TestTrigger -v
```

Expected: 3 passed.

- [ ] **Step 5: Implement `_run()` and `_score_symbol()` in `bot/weight_rebalancer.py`**

Add these methods to the `WeightRebalancer` class:

```python
    # ------------------------------------------------------------------ #
    # Background execution                                                 #
    # ------------------------------------------------------------------ #

    def _run(self, trigger_ts: int) -> None:
        try:
            self._do_rebalance(trigger_ts)
        except Exception:
            logger.exception("WeightRebalancer: unhandled error during rebalance")
        finally:
            self._running.clear()

    def _do_rebalance(self, trigger_ts: int) -> None:
        cfg = self._cfg
        window_candles = int(cfg.get("backtest_window_candles", 96))
        alpha = float(cfg.get("real_pnl_alpha", 0.5))
        blend_rate = float(cfg.get("blend_rate", 0.15))
        floor_ratio = float(cfg.get("weight_floor_ratio", 0.3))
        window_start_ms = trigger_ts - window_candles * self._candle_ms

        symbols = [
            s for s in self._registry.get_symbols()
            if not self._registry.is_disabled(s)
            and self._registry.get_weight(s) > 0
        ]
        if len(symbols) < 2:
            logger.info("WeightRebalancer: fewer than 2 active symbols — skipping")
            return

        from config.presets import ALL_PRESETS
        from bot.backtester import Backtester

        # Silence backtester INFO spam during mini-backtest
        bt_logger = logging.getLogger("bot.backtester")
        orig_level = bt_logger.level
        bt_logger.setLevel(logging.WARNING)

        backtest_pcts: dict[str, float] = {}
        real_pnls: dict[str, float] = {}

        try:
            for sym in symbols:
                bt_pct, pnl = self._score_symbol(
                    sym, window_candles, window_start_ms, ALL_PRESETS
                )
                backtest_pcts[sym] = bt_pct
                real_pnls[sym] = pnl
        finally:
            bt_logger.setLevel(orig_level)

        scores = self._calc_scores(backtest_pcts, real_pnls, alpha)

        rc = load_risk_config(self._config_path)
        current_weights: dict[str, float] = rc.get("symbol_weights", {})
        new_weights = self._blend_weights(current_weights, scores, blend_rate, floor_ratio)

        for sym in symbols:
            old = round(current_weights.get(sym, 1.0 / len(symbols)), 4)
            new = round(new_weights[sym], 4)
            logger.info(
                f"WeightRebalancer [{sym}]: bt={backtest_pcts[sym]:.2f}% "
                f"pnl={real_pnls[sym]:.2f} score={scores[sym]:.3f} "
                f"weight {old:.4f} → {new:.4f}"
            )

        rc["symbol_weights"] = new_weights
        save_risk_config(rc, self._config_path)
        self._append_log(trigger_ts, symbols, backtest_pcts, real_pnls, scores, current_weights, new_weights)

    def _score_symbol(
        self,
        symbol: str,
        window_candles: int,
        window_start_ms: int,
        presets: dict,
    ) -> tuple[float, float]:
        from bot.backtester import Backtester

        klines = self._get_klines(symbol)
        klines_slice = klines[-window_candles:] if len(klines) >= window_candles else klines
        best_pct = 0.0
        if klines_slice:
            try:
                backtester = Backtester(base_settings=self._settings)
                results = backtester.run(klines_slice, presets)
                if results:
                    best_pct = max(r.total_profit_pct() for r in results.values())
            except Exception:
                logger.warning(f"WeightRebalancer: backtester failed for {symbol}", exc_info=True)

        orders = self._filter_real_orders(symbol, window_start_ms)
        pnl = sum(float(o.get("pnl_usdt", 0.0)) for o in orders)
        return best_pct, pnl

    def _append_log(
        self,
        trigger_ts: int,
        symbols: list[str],
        backtest_pcts: dict[str, float],
        real_pnls: dict[str, float],
        scores: dict[str, float],
        old_weights: dict[str, float],
        new_weights: dict[str, float],
    ) -> None:
        path = self._data_dir / f"weight_rebalance_log_{self._mode}.json"
        try:
            entries: list = json.loads(path.read_text()) if path.exists() else []
        except Exception:
            entries = []
        n = len(symbols)
        entry = {
            "ts": trigger_ts,
            "symbols": {
                s: {
                    "backtest_pct": round(backtest_pcts.get(s, 0.0), 4),
                    "real_pnl_usdt": round(real_pnls.get(s, 0.0), 4),
                    "score": round(scores.get(s, 0.0), 4),
                    "old_weight": round(old_weights.get(s, 1.0 / n), 4),
                    "new_weight": round(new_weights.get(s, 1.0 / n), 4),
                }
                for s in symbols
            },
        }
        entries.append(entry)
        if len(entries) > _LOG_MAX:
            entries = entries[-_LOG_MAX:]
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, indent=2))
        tmp.replace(path)
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/test_weight_rebalancer.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add bot/weight_rebalancer.py tests/test_weight_rebalancer.py
git commit -m "feat: implement WeightRebalancer._run and _score_symbol"
```

---

## Task 5: Wire into `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Import and instantiate `WeightRebalancer`**

Near the top of `main.py` imports (with other `bot/` imports):

```python
from bot.weight_rebalancer import WeightRebalancer
```

In the bot setup section (after `risk_manager`, `symbol_registry`, and `analyzers` are created — search for where `virtual_order_simulator` is created as a reference point), add:

```python
_wr_cfg = risk_cfg.get("weight_rebalancer", {})
weight_rebalancer = WeightRebalancer(
    symbol_registry=symbol_registry,
    risk_manager=risk_manager,
    settings=settings,
    get_klines_fn=lambda sym: analyzers[sym].get_klines() if sym in analyzers else [],
    candle_duration_ms=_tf_to_ms(timeframe),
    mode=current_mode,
    risk_config_path=Path("risk_config.json"),
    data_dir=Path("data"),
    cfg=_wr_cfg,
)
```

- [ ] **Step 2: Call `on_candle_close` in the candle loop**

Find the section in `main.py` where per-symbol `on_candle_close` processing completes (near the bottom of the candle handling loop, after the BestGetsFirst block and virtual simulator calls). Add:

```python
        weight_rebalancer.on_candle_close(candle_ts)
```

- [ ] **Step 3: Smoke test — start bot, confirm no crash**

```bash
python main.py
```

Watch for:
- No `ImportError` or `AttributeError` on startup
- Bot connects to WebSocket normally
- No errors in the first few candle closes

Ctrl+C after 30 seconds.

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire WeightRebalancer into main.py candle loop"
```

---

## Task 6: Dashboard types and API

**Files:**
- Modify: `dashboard/lib/risk-types.ts`
- Modify: `dashboard/app/api/risk/route.ts`

- [ ] **Step 1: Add TypeScript types**

In `dashboard/lib/risk-types.ts`, append after the `RiskState` interface:

```typescript
export interface WeightRebalancerConfig {
  enabled: boolean
  rebalance_candles: number
  backtest_window_candles: number
  real_pnl_alpha: number
  blend_rate: number
  weight_floor_ratio: number
}

export interface WeightRebalanceSymbolEntry {
  backtest_pct: number
  real_pnl_usdt: number
  score: number
  old_weight: number
  new_weight: number
}

export interface WeightRebalanceLogEntry {
  ts: number
  symbols: Record<string, WeightRebalanceSymbolEntry>
}
```

Also extend `RiskConfig` with the optional field:

```typescript
// In the RiskConfig interface, add:
  weight_rebalancer?: WeightRebalancerConfig
```

- [ ] **Step 2: Add `weight_rebalancer` to the TS `DEFAULT_CONFIG` in `route.ts`**

In `dashboard/app/api/risk/route.ts`, in the `DEFAULT_CONFIG` object (after `scenario`), add:

```typescript
  weight_rebalancer: {
    enabled: false,
    rebalance_candles: 96,
    backtest_window_candles: 96,
    real_pnl_alpha: 0.5,
    blend_rate: 0.15,
    weight_floor_ratio: 0.3,
  },
```

The existing `GET` handler merges `DEFAULT_CONFIG` with the file, so `weight_rebalancer` will now appear automatically in the API response. The existing `POST` handler already saves the full body to disk, so no further changes are needed.

- [ ] **Step 3: Type-check**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 4: Verify GET response includes `weight_rebalancer`**

```bash
curl -s http://localhost:3000/api/risk | python3 -m json.tool | grep -A 8 weight_rebalancer
```

Expected: the `weight_rebalancer` object appears with default values.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/risk-types.ts dashboard/app/api/risk/route.ts
git commit -m "feat: add weight_rebalancer types and API defaults"
```

---

## Task 7: Dashboard UI component

**Files:**
- Create: `dashboard/components/risk/WeightRebalancerSection.tsx`

- [ ] **Step 1: Create the component**

```tsx
'use client'

import { WeightRebalancerConfig, WeightRebalanceLogEntry } from '@/lib/risk-types'
import { useEffect, useState } from 'react'

interface Props {
  config: WeightRebalancerConfig
  mode: string
  patchConfig: (patch: Record<string, unknown>) => void
}

export default function WeightRebalancerSection({ config, mode, patchConfig }: Props) {
  const [log, setLog] = useState<WeightRebalanceLogEntry[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    fetch(`/api/public-file?f=weight_rebalance_log_${mode}.json`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setLog(Array.isArray(data) ? data.slice(-1) : []))
      .catch(() => setLog([]))
  }, [open, mode])

  const patch = (key: keyof WeightRebalancerConfig, value: unknown) =>
    patchConfig({ weight_rebalancer: { ...config, [key]: value } })

  const lastEntry = log[0]
  const lastTs = lastEntry
    ? `${Math.round((Date.now() - lastEntry.ts) / 60000)} min ago`
    : 'Never'

  return (
    <section className="border border-neutral-700 rounded p-4 mt-6">
      <button
        className="w-full flex justify-between items-center text-left font-semibold text-sm"
        onClick={() => setOpen(o => !o)}
      >
        <span>Weight Rebalancer</span>
        <span>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-4 space-y-4">
          {/* Enable toggle */}
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              checked={config.enabled}
              onChange={e => patch('enabled', e.target.checked)}
              className="w-4 h-4"
            />
            Enabled
          </label>

          {/* Numeric controls */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <label className="flex flex-col gap-1">
              Rebalance every N candles
              <input
                type="number" min={10} max={1000} step={1}
                value={config.rebalance_candles}
                onChange={e => patch('rebalance_candles', Number(e.target.value))}
                className="bg-neutral-800 border border-neutral-600 rounded px-2 py-1 w-28"
              />
            </label>
            <label className="flex flex-col gap-1">
              Backtest window (candles)
              <input
                type="number" min={10} max={1000} step={1}
                value={config.backtest_window_candles}
                onChange={e => patch('backtest_window_candles', Number(e.target.value))}
                className="bg-neutral-800 border border-neutral-600 rounded px-2 py-1 w-28"
              />
            </label>
          </div>

          {/* Sliders */}
          {([
            ['real_pnl_alpha', 'Real P&L weight (vs backtest)', 0, 1, 0.05],
            ['blend_rate', 'Blend rate per cycle', 0.05, 0.5, 0.05],
            ['weight_floor_ratio', 'Floor ratio (× equal share)', 0.1, 0.9, 0.05],
          ] as [keyof WeightRebalancerConfig, string, number, number, number][]).map(
            ([key, label, min, max, step]) => (
              <label key={key} className="flex flex-col gap-1 text-sm">
                {label}: <span className="font-mono">{(config[key] as number).toFixed(2)}</span>
                <input
                  type="range" min={min} max={max} step={step}
                  value={config[key] as number}
                  onChange={e => patch(key, Number(e.target.value))}
                  className="w-full"
                />
              </label>
            )
          )}

          {/* Status */}
          <div className="text-sm text-neutral-400">Last rebalance: {lastTs}</div>

          {/* Per-symbol table */}
          {lastEntry && (
            <table className="w-full text-xs border-collapse">
              <thead>
                <tr className="text-neutral-400 border-b border-neutral-700">
                  <th className="text-left py-1 pr-3">Symbol</th>
                  <th className="text-right py-1 pr-3">BT %</th>
                  <th className="text-right py-1 pr-3">Real P&L</th>
                  <th className="text-right py-1 pr-3">Score</th>
                  <th className="text-right py-1">Weight</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(lastEntry.symbols).map(([sym, d]) => (
                  <tr key={sym} className="border-b border-neutral-800">
                    <td className="py-1 pr-3 font-mono">{sym}</td>
                    <td className="text-right py-1 pr-3">{d.backtest_pct.toFixed(2)}%</td>
                    <td className="text-right py-1 pr-3">{d.real_pnl_usdt >= 0 ? '+' : ''}{d.real_pnl_usdt.toFixed(2)}</td>
                    <td className="text-right py-1 pr-3">{d.score.toFixed(3)}</td>
                    <td className="text-right py-1">
                      <span className="text-neutral-500">{d.old_weight.toFixed(3)}</span>
                      {' → '}
                      <span className={d.new_weight > d.old_weight ? 'text-green-400' : d.new_weight < d.old_weight ? 'text-red-400' : ''}>
                        {d.new_weight.toFixed(3)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add dashboard/components/risk/WeightRebalancerSection.tsx
git commit -m "feat: add WeightRebalancerSection dashboard component"
```

---

## Task 8: Integrate component into Risk page

**Files:**
- Modify: `dashboard/app/risk/page.tsx`

- [ ] **Step 1: Import and render the section**

At the top of `dashboard/app/risk/page.tsx`, add the import alongside other section imports:

```typescript
import WeightRebalancerSection from '@/components/risk/WeightRebalancerSection'
```

Find where `config` and `state` are read (around `const config = ...`) and check how `mode` is available. The `state` object has `state?.mode`. Add the component at the bottom of the rendered JSX, after `<ScenarioSection>`:

```tsx
{config.weight_rebalancer && (
  <WeightRebalancerSection
    config={config.weight_rebalancer}
    mode={state?.mode ?? 'test'}
    patchConfig={(patch) => {
      const merged = { ...config, ...patch }
      setConfig(merged as typeof config)
    }}
  />
)}
```

Note: `patchConfig` here must also trigger a save to the API. Verify how the existing `ScenarioSection` calls save; replicate the same pattern. If the page has a `handleSave` function triggered by a top-level Save button, the local `setConfig` call is sufficient — the save button will pick up the merged config.

- [ ] **Step 2: Check the page renders without errors**

```bash
cd dashboard && npm run dev
```

Open `http://localhost:3000/risk`. Scroll to the bottom. Confirm the "Weight Rebalancer" collapsible section appears. Click it to expand — confirm controls render without console errors.

- [ ] **Step 3: Toggle enabled, click Save, confirm `risk_config.json` updated**

Enable the toggle, click the page's Save button, then:

```bash
python3 -c "
import json
cfg = json.load(open('risk_config.json'))
print(cfg.get('weight_rebalancer'))
"
```

Expected: `{'enabled': True, 'rebalance_candles': 96, ...}`

- [ ] **Step 4: Type-check**

```bash
cd dashboard && npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app/risk/page.tsx
git commit -m "feat: integrate WeightRebalancerSection into Risk page"
```

---

## Task 9: End-to-end smoke test

- [ ] **Step 1: Enable rebalancer with a short cycle for testing**

```bash
python3 -c "
import json
from pathlib import Path
cfg = json.loads(Path('risk_config.json').read_text())
cfg['weight_rebalancer']['enabled'] = True
cfg['weight_rebalancer']['rebalance_candles'] = 2
Path('risk_config.json').write_text(json.dumps(cfg, indent=2))
print('done')
"
```

- [ ] **Step 2: Run bot, wait for 2 candle closes**

```bash
python main.py
```

Watch logs for lines like:

```
WeightRebalancer [BTCUSDT]: bt=1.42% pnl=3.21 score=0.870 weight 0.0667 → 0.0712
```

Confirm `data/weight_rebalance_log_test.json` is created and contains one entry.

- [ ] **Step 3: Restore default cycle length**

```bash
python3 -c "
import json
from pathlib import Path
cfg = json.loads(Path('risk_config.json').read_text())
cfg['weight_rebalancer']['rebalance_candles'] = 96
cfg['weight_rebalancer']['enabled'] = False
Path('risk_config.json').write_text(json.dumps(cfg, indent=2))
print('done')
"
```

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all previously passing tests still pass; new weight rebalancer tests pass.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: dynamic weight rebalancer — complete implementation"
```
