# Mean-Reversion Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a regime-switched mean-reversion overlay that fades *confirmed oscillating ranges*, routed through the existing signal→order→exit pipeline, gated per-symbol and OFF by default.

**Architecture:** Two pure functions in a new `bot/mean_reversion.py` (`detect_range`, `mr_signal`) do all MR logic. `RecommendationEngine.generate()` gains an optional `recent_klines` argument; when MR is enabled for the symbol and a range qualifies, it builds a `MEAN_REVERT_FADE` `Recommendation` and suppresses trend-continuation candidates (regime switch). Because both the live analyzer and the backtester call `engine.generate()`, one seam covers both — so the real backtester validates the exact live code path (Gate A).

**Tech Stack:** Python 3, dataclasses, pytest. No new dependencies.

## Global Constraints

- **OFF by default:** `enable_mean_reversion=False` globally; per-symbol opt-in only. No live-mode path is modified.
- **Validated defaults (do not change without re-running `/tmp/s60/mr_refine.py`):** `W=48`, `min_touches=2`, `touch_tol=0.12`, `band_min=0.02`, `band_max=0.16`, `decile=0.15`, `sl_buf=0.5`, TP=range mid, wick-rejection entry required.
- **Allow-list symbols (evidence: return/DD ≥ 1.0 and OOS-positive):** TIAUSDT, EIGENUSDT, INJUSDT, THETAUSDT, 1000PEPEUSDT, SOLUSDT. **DOGEUSDT excluded. MEMEUSDT virtual-only (probationary).**
- **Purity:** `detect_range` and `mr_signal` take only their arguments — no I/O, no global state, no `Trend` dependency.
- **Every `Recommendation` needs a `Point`** — use `trend.getCurrentPoint()` as the anchor for MR recs.
- **Metric honesty:** headline probe numbers are sum-of-per-trade-return-on-notional (~+0.2%/trade), NOT account return. The real USDT figure comes only from Gate A.
- Commit after every task. Run `python -m pytest` from repo root.

---

## File Structure

- **Create** `bot/mean_reversion.py` — `MRConfig`, `Range`, `MRSignal` dataclasses; `detect_range(klines, cfg)`; `mr_signal(klines, rng, cfg)`. One responsibility: MR geometry. Pure.
- **Create** `tests/test_mean_reversion.py` — unit tests for both functions.
- **Modify** `bot/recommendation.py` — add `MEAN_REVERT_FADE` enum member.
- **Modify** `config/settings.py` — declare + load MR settings fields.
- **Modify** `config/presets.py` — add `mr_fade` preset + `PresetOverrides` keys.
- **Modify** `bot/recommendation_engine.py` — `generate()`/`collect_all()` gain `recent_klines`; MR branch + regime-switch.
- **Modify** `bot/analyzer.py` — pass `self._klines` to `engine.generate()` (lines 112, 146).
- **Modify** `bot/backtester.py` — pass `analyzer.get_klines()` to `engine.generate()` (line 309); ensure MR geometry isn't mangled by TP/RR gates.
- **Modify** `main.py` — decision-log entry for MR signals (observability only).
- **Modify** `FEATURES.md` — document the overlay.

---

### Task 1: `bot/mean_reversion.py` — dataclasses + `detect_range()`

**Files:**
- Create: `bot/mean_reversion.py`
- Test: `tests/test_mean_reversion.py`

**Interfaces:**
- Produces: `MRConfig` (dataclass, validated defaults above), `Range(hi: float, lo: float, mid: float, width: float)`, `detect_range(klines: list, cfg: MRConfig) -> Optional[Range]`. `klines` are Binance rows: `[open_ms, open, high, low, close, ...]` (indices 1–4 are OHLC as float-able).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mean_reversion.py
from bot.mean_reversion import MRConfig, Range, detect_range

def _k(o, h, l, c, t=0):
    return [t, o, h, l, c, 0]

def _oscillating(n=48, lo=100.0, hi=110.0):
    # alternately tags the low and the high -> both boundaries tested
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(_k(105, hi, 104, 106, i))   # tags high
        else:
            out.append(_k(105, 106, lo, 104, i))   # tags low
    return out

def test_detect_range_qualifies_on_oscillating_window():
    cfg = MRConfig()
    rng = detect_range(_oscillating(), cfg)
    assert rng is not None
    assert abs(rng.hi - 110.0) < 1e-9 and abs(rng.lo - 100.0) < 1e-9
    assert abs(rng.mid - 105.0) < 1e-9

def test_detect_range_rejects_one_sided_window():
    # only the high is ever tagged -> not a confirmed range
    cfg = MRConfig()
    kl = [_k(105, 110, 104, 106, i) for i in range(48)]
    assert detect_range(kl, cfg) is None

def test_detect_range_rejects_breakout_series():
    # steadily rising -> width blows out / boundaries not both re-tested
    cfg = MRConfig()
    kl = [_k(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(48)]
    assert detect_range(kl, cfg) is None

def test_detect_range_needs_full_window():
    cfg = MRConfig()
    assert detect_range(_oscillating(n=10), cfg) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mean_reversion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.mean_reversion'`

- [ ] **Step 3: Write minimal implementation**

```python
# bot/mean_reversion.py
"""Mean-reversion overlay geometry. PURE functions — no I/O, no global state,
no Trend dependency. Validated by /tmp/s60/mr_refine.py (session 61):
confirmed-range fade, mid config, 7/8 symbols OOS-positive."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MRConfig:
    window: int = 48
    min_touches: int = 2
    touch_tol: float = 0.12
    band_min: float = 0.02
    band_max: float = 0.16
    decile: float = 0.15
    sl_buf: float = 0.5


@dataclass(frozen=True)
class Range:
    hi: float
    lo: float
    mid: float
    width: float   # (hi - lo) / mid


def _ohlc(k):
    return float(k[1]), float(k[2]), float(k[3]), float(k[4])


def detect_range(klines: list, cfg: MRConfig) -> Optional[Range]:
    """Return a Range iff the trailing `window` candles form a CONFIRMED
    oscillating range: both boundaries tested >= min_touches, width within band."""
    if len(klines) < cfg.window:
        return None
    win = klines[-cfg.window:]
    highs = [float(k[2]) for k in win]
    lows = [float(k[3]) for k in win]
    hi, lo = max(highs), min(lows)
    rng = hi - lo
    if rng <= 0:
        return None
    mid = (hi + lo) / 2.0
    width = rng / mid
    if not (cfg.band_min <= width <= cfg.band_max):
        return None
    top_band = hi - cfg.touch_tol * rng
    bot_band = lo + cfg.touch_tol * rng
    top_touches = sum(1 for h in highs if h >= top_band)
    bot_touches = sum(1 for l in lows if l <= bot_band)
    if top_touches >= cfg.min_touches and bot_touches >= cfg.min_touches:
        return Range(hi=hi, lo=lo, mid=mid, width=width)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mean_reversion.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bot/mean_reversion.py tests/test_mean_reversion.py
git commit -m "feat(mr): detect_range — confirmed oscillating-range detector"
```

---

### Task 2: `mr_signal()` — fade the extreme

**Files:**
- Modify: `bot/mean_reversion.py`
- Test: `tests/test_mean_reversion.py`

**Interfaces:**
- Consumes: `Range`, `MRConfig` from Task 1.
- Produces: `MRSignal(side: str, entry: float, tp: float, sl: float)`, `mr_signal(klines: list, rng: Range, cfg: MRConfig) -> Optional[MRSignal]`. Decides on the **last** candle in `klines`. `side` is `'BUY'`/`'SELL'`. Requires a wick-rejection at the boundary (candle poked past it but closed back inside).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_mean_reversion.py
from bot.mean_reversion import MRSignal, mr_signal

def _rng():
    return Range(hi=110.0, lo=100.0, mid=105.0, width=10.0 / 105.0)

def test_mr_signal_sell_on_top_wick_rejection():
    cfg = MRConfig()
    # last candle: pokes above hi (110.2) but closes back inside near top (109.0)
    kl = _oscillating() + [_k(109, 110.2, 108.5, 109.0, 99)]
    sig = mr_signal(kl, _rng(), cfg)
    assert sig is not None and sig.side == 'SELL'
    assert abs(sig.tp - 105.0) < 1e-9            # TP = mid
    assert sig.sl > 110.0                         # SL beyond hi boundary
    assert abs(sig.entry - 109.0) < 1e-9          # entry = close

def test_mr_signal_buy_on_bottom_wick_rejection():
    cfg = MRConfig()
    kl = _oscillating() + [_k(101, 101.5, 99.8, 101.0, 99)]  # pokes below lo, closes inside
    sig = mr_signal(kl, _rng(), cfg)
    assert sig is not None and sig.side == 'BUY'
    assert abs(sig.tp - 105.0) < 1e-9
    assert sig.sl < 100.0

def test_mr_signal_none_when_mid_range():
    cfg = MRConfig()
    kl = _oscillating() + [_k(105, 105.5, 104.5, 105.0, 99)]  # close at mid, no fade
    assert mr_signal(kl, _rng(), cfg) is None

def test_mr_signal_none_without_wick_rejection():
    cfg = MRConfig()
    # closes ABOVE hi (breakout, no rejection back inside) -> no fade
    kl = _oscillating() + [_k(109, 111.0, 108.5, 110.8, 99)]
    assert mr_signal(kl, _rng(), cfg) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mean_reversion.py -k mr_signal -v`
Expected: FAIL — `ImportError: cannot import name 'MRSignal'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to bot/mean_reversion.py

@dataclass(frozen=True)
class MRSignal:
    side: str      # 'BUY' | 'SELL'
    entry: float
    tp: float
    sl: float


def mr_signal(klines: list, rng: Range, cfg: MRConfig) -> Optional[MRSignal]:
    """Fade the extreme of a confirmed range on the last candle.
    Requires a wick-rejection: the candle pokes past the boundary but closes
    back inside. TP = range mid, SL = boundary +/- sl_buf * range_width."""
    if not klines:
        return None
    o, h, l, c = _ohlc(klines[-1])
    span = rng.hi - rng.lo
    if span <= 0:
        return None
    pos = (c - rng.lo) / span
    body_tol = 0.15 * (h - l + 1e-12)
    # SELL: near top, poked above hi but closed back inside
    if pos >= 1 - cfg.decile:
        if h > rng.hi - 0.02 * span and c < h - body_tol and c <= rng.hi:
            return MRSignal(side='SELL', entry=c, tp=rng.mid, sl=rng.hi + cfg.sl_buf * span)
    # BUY: near bottom, poked below lo but closed back inside
    if pos <= cfg.decile:
        if l < rng.lo + 0.02 * span and c > l + body_tol and c >= rng.lo:
            return MRSignal(side='BUY', entry=c, tp=rng.mid, sl=rng.lo - cfg.sl_buf * span)
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mean_reversion.py -v`
Expected: PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add bot/mean_reversion.py tests/test_mean_reversion.py
git commit -m "feat(mr): mr_signal — fade confirmed-range extreme with wick rejection"
```

---

### Task 3: Config plumbing — rec type, settings fields, `mr_fade` preset

**Files:**
- Modify: `bot/recommendation.py:8-22` (enum)
- Modify: `config/settings.py` (declare ~line 134; load ~line 216)
- Modify: `config/presets.py` (`PresetOverrides` ~line 59; new preset in `PRESETS`)
- Test: `tests/test_mean_reversion.py`

**Interfaces:**
- Produces: `RecommendationTypes.MEAN_REVERT_FADE = 'mean_revert_fade'`; Settings fields `enable_mean_reversion: bool`, `mr_window: int`, `mr_min_touches: int`, `mr_touch_tol: float`, `mr_band_min: float`, `mr_band_max: float`, `mr_decile: float`, `mr_sl_buf: float`; preset key `'mr_fade'`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_mean_reversion.py
def test_config_wiring_exists():
    from bot.recommendation import RecommendationTypes
    from config.settings import load_settings
    from config.presets import ALL_PRESETS
    assert RecommendationTypes.MEAN_REVERT_FADE.value == 'mean_revert_fade'
    s = load_settings('TIAUSDT')
    assert s.enable_mean_reversion is False       # OFF by default
    assert s.mr_window == 48 and s.mr_sl_buf == 0.5
    assert 'mr_fade' in ALL_PRESETS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mean_reversion.py::test_config_wiring_exists -v`
Expected: FAIL — `AttributeError: MEAN_REVERT_FADE`

- [ ] **Step 3: Write minimal implementation**

In `bot/recommendation.py`, add to `RecommendationTypes` (after line 22):
```python
    # Mean-reversion overlay: fade the extreme of a confirmed oscillating range.
    MEAN_REVERT_FADE = 'mean_revert_fade'
```

In `config/settings.py`, add fields to the `Settings` dataclass (after `enforce_parent_alignment_hard`, keeping default-valued fields together):
```python
    enable_mean_reversion: bool = False
    mr_window: int = 48
    mr_min_touches: int = 2
    mr_touch_tol: float = 0.12
    mr_band_min: float = 0.02
    mr_band_max: float = 0.16
    mr_decile: float = 0.15
    mr_sl_buf: float = 0.5
```
And add load lines inside `load_settings(...)`'s `Settings(...)` constructor (near line 216):
```python
        enable_mean_reversion=os.getenv('ENABLE_MEAN_REVERSION', 'false').lower() in ('1', 'true', 'yes'),
        mr_window=int(os.getenv('MR_WINDOW', '48')),
        mr_min_touches=int(os.getenv('MR_MIN_TOUCHES', '2')),
        mr_touch_tol=float(os.getenv('MR_TOUCH_TOL', '0.12')),
        mr_band_min=float(os.getenv('MR_BAND_MIN', '0.02')),
        mr_band_max=float(os.getenv('MR_BAND_MAX', '0.16')),
        mr_decile=float(os.getenv('MR_DECILE', '0.15')),
        mr_sl_buf=float(os.getenv('MR_SL_BUF', '0.5')),
```
(Per-symbol overrides like `TIAUSDT_ENABLE_MEAN_REVERSION` are automatic via `_apply_symbol_overrides`.)

In `config/presets.py`, add keys to `PresetOverrides` (near line 59):
```python
    enable_mean_reversion: bool
    mr_window: int
    mr_min_touches: int
    mr_touch_tol: float
    mr_band_min: float
    mr_band_max: float
    mr_decile: float
    mr_sl_buf: float
```
And add the preset to `PRESETS`:
```python
    'mr_fade': {
        # Mean-reversion overlay preset (session 61). Fixed-geometry exits:
        # TP=range mid, SL=boundary +/- sl_buf*range (set by mr_signal, not by preset).
        # No partial/trail — MR banks the reversion outright.
        'enable_mean_reversion': True,
        'partial_take_pct': 0.0,
        'trailing_stop_pct': 0.0,
        'trail_activation_pct': 0.0,
        'min_profit_loss_ratio': 0.0,   # MR is high-WR/low-payoff; do not RR-gate it
        'tp_multiplier': 1.0,           # keep TP exactly at mid
        'max_losing_candles': 96,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mean_reversion.py::test_config_wiring_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bot/recommendation.py config/settings.py config/presets.py tests/test_mean_reversion.py
git commit -m "feat(mr): rec type, settings fields, and mr_fade preset (off by default)"
```

---

### Task 4: Engine integration — MR branch + regime switch

**Files:**
- Modify: `bot/recommendation_engine.py` (`generate` line 39, `collect_all` line 48, internal `_collect`)
- Test: `tests/test_mr_engine.py` (create)

**Interfaces:**
- Consumes: `detect_range`, `mr_signal`, `MRConfig` (Task 1–2); `RecommendationTypes.MEAN_REVERT_FADE`, Settings MR fields (Task 3); `Recommendation` (needs a `Point` — use `trend.getCurrentPoint()`).
- Produces: `generate(self, root_trend, entry_price, recent_klines=None) -> Optional[Recommendation]` — same for `collect_all`. When MR is enabled + a range qualifies + a fade fires: returns a `MEAN_REVERT_FADE` `Recommendation` and does NOT return any trend-continuation rec (regime switch). When MR enabled + range qualifies but no fade this candle: returns `None`. When no range: unchanged trend behavior.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mr_engine.py
from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.recommendation_engine import RecommendationEngine
from bot.recommendation import RecommendationTypes
import dataclasses


def _k(o, h, l, c, t):
    return [t, o, h, l, c, 0]

def _oscillating_klines(n=60, lo=100.0, hi=110.0):
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(_k(105, hi, 104, 106, i * 900_000))
        else:
            out.append(_k(105, 106, lo, 104, i * 900_000))
    # final candle: SELL fade (poke above hi, close back inside near top)
    out.append(_k(109, 110.2, 108.5, 109.0, n * 900_000))
    return out

def test_engine_emits_mr_fade_when_enabled_and_range_confirmed():
    s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
    engine = RecommendationEngine(s)
    analyzer = Analyzer(s.swing_neighbours, engine)
    kl = _oscillating_klines()
    analyzer.build_from_klines(kl)
    trend = analyzer.get_trend()
    rec = engine.generate(trend, 109.0, recent_klines=kl)
    assert rec is not None
    assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
    assert rec.getSide() == 'SELL'
    assert abs(rec.getTarget() - 105.0) < 1e-6      # TP=mid
    assert rec.getStop() > 110.0                      # SL beyond boundary

def test_engine_ignores_mr_when_disabled():
    s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=False)
    engine = RecommendationEngine(s)
    analyzer = Analyzer(s.swing_neighbours, engine)
    kl = _oscillating_klines()
    analyzer.build_from_klines(kl)
    trend = analyzer.get_trend()
    rec = engine.generate(trend, 109.0, recent_klines=kl)
    # MR off -> no MR rec (trend engine may or may not fire, but never MR type)
    assert rec is None or rec.getType() != RecommendationTypes.MEAN_REVERT_FADE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mr_engine.py -v`
Expected: FAIL — `generate()` got an unexpected keyword argument `recent_klines`

- [ ] **Step 3: Write minimal implementation**

At the top of `bot/recommendation_engine.py`, add imports:
```python
from bot.mean_reversion import MRConfig, detect_range, mr_signal
from bot.recommendation import Recommendation, RecommendationTypes
```
Add a helper method on `RecommendationEngine`:
```python
    def _mr_config(self) -> MRConfig:
        s = self._s
        return MRConfig(window=s.mr_window, min_touches=s.mr_min_touches,
                        touch_tol=s.mr_touch_tol, band_min=s.mr_band_min,
                        band_max=s.mr_band_max, decile=s.mr_decile, sl_buf=s.mr_sl_buf)

    def _mr_recommendation(self, root_trend, entry_price, recent_klines):
        """Return (active, rec): active=True means a confirmed range is in force
        (regime switch — suppress trend). rec may be None if no fade this candle."""
        if not self._s.enable_mean_reversion or not recent_klines:
            return False, None
        cfg = self._mr_config()
        rng = detect_range(recent_klines, cfg)
        if rng is None:
            return False, None
        sig = mr_signal(recent_klines, rng, cfg)
        if sig is None:
            return True, None
        point = root_trend.getCurrentPoint()
        if point is None:
            return True, None
        rec = Recommendation(point, sig.tp, sig.sl, sig.side,
                             RecommendationTypes.MEAN_REVERT_FADE)
        rec.setLevel(1).setEntryPrice(sig.entry).setPrecision(1.0)
        rr = abs(sig.tp - sig.entry) / abs(sig.sl - sig.entry) if sig.sl != sig.entry else 0.0
        rec.setRR(rr)
        return True, rec
```
Then at the START of `generate()` (before `_collect`):
```python
        mr_active, mr_rec = self._mr_recommendation(root_trend, entry_price, recent_klines)
        if mr_active:
            return mr_rec        # regime switch: MR owns this symbol; suppress trend
```
Update the signatures: `def generate(self, root_trend, entry_price, recent_klines=None)` and `def collect_all(self, root_trend, entry_price, recent_klines=None)`. In `collect_all`, mirror the branch: if `mr_active`, `return [mr_rec] if mr_rec else []`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mr_engine.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: PASS (existing tests unaffected — `recent_klines` defaults to `None`, so all current callers get identical behavior).

- [ ] **Step 6: Commit**

```bash
git add bot/recommendation_engine.py tests/test_mr_engine.py
git commit -m "feat(mr): engine regime-switch — emit MEAN_REVERT_FADE, suppress trend in confirmed ranges"
```

---

### Task 5: Thread klines through analyzer + backtester

**Files:**
- Modify: `bot/analyzer.py:112` and `bot/analyzer.py:146`
- Modify: `bot/backtester.py:309`
- Test: `tests/test_mr_engine.py` (add end-to-end analyzer test)

**Interfaces:**
- Consumes: `generate(..., recent_klines=...)` from Task 4; `Analyzer.get_klines()` (exists, line 167) and `self._klines` (line 16).
- Produces: live path (`analyzer.add_candle` / `get_best_recommendation`) and backtest path both pass the kline buffer to `generate`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_mr_engine.py
def test_analyzer_add_candle_routes_mr():
    import dataclasses
    from config.settings import load_settings
    from bot.analyzer import Analyzer
    from bot.recommendation_engine import RecommendationEngine
    from bot.recommendation import RecommendationTypes
    s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
    engine = RecommendationEngine(s)
    analyzer = Analyzer(s.swing_neighbours, engine)
    kl = _oscillating_klines()
    analyzer.build_from_klines(kl[:-1])
    analyzer.update_price(109.0)
    analyzer.add_candle(kl[-1])                 # feeds final fade candle
    rec = analyzer.get_best_recommendation()
    assert rec is not None
    assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mr_engine.py::test_analyzer_add_candle_routes_mr -v`
Expected: FAIL — MR not routed (klines not passed), `rec` is `None` or wrong type.

- [ ] **Step 3: Write minimal implementation**

In `bot/analyzer.py` line 112, change:
```python
        self._best_recommendation = self._engine.generate(self._trend, self._current_price)
```
to:
```python
        self._best_recommendation = self._engine.generate(
            self._trend, self._current_price, recent_klines=self._klines)
```
In `bot/analyzer.py` line 146, change:
```python
        return RecommendationEngine(s).generate(self._trend, self._current_price)
```
to:
```python
        return RecommendationEngine(s).generate(
            self._trend, self._current_price, recent_klines=self._klines)
```
In `bot/backtester.py` line 309, change:
```python
                    rec = engine.generate(trend, entry_price)
```
to:
```python
                    rec = engine.generate(trend, entry_price, recent_klines=analyzer.get_klines())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mr_engine.py -v && python -m pytest -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add bot/analyzer.py bot/backtester.py tests/test_mr_engine.py
git commit -m "feat(mr): route kline window to engine.generate in live + backtest paths"
```

---

### Task 6: Gate A — real-backtester validation of the MR edge

**Files:**
- Create: `scripts/validate_mr_backtest.py` (repo-tracked validation harness)
- Modify: `bot/backtester.py` — verify MR geometry survives the TP/SL/RR gate block (lines 335–404); if a gate mangles MR, guard it with `if rec.getType() != RecommendationTypes.MEAN_REVERT_FADE`.

**Interfaces:**
- Consumes: `Backtester.run(klines, {'mr_fade': {...}})`; klines at `/tmp/s60/fullklines/<SYM>.json`.
- Produces: a printed per-symbol table (n, WR, net%) and a hard assertion that the allow-list is net-positive on a majority.

- [ ] **Step 1: Write the validation harness**

```python
# scripts/validate_mr_backtest.py
"""Gate A: run the REAL backtester with the mr_fade preset on the allow-list
symbols and confirm the toy-sim edge (session 61) survives real sizing/fees/gates."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import load_settings
from config.presets import ALL_PRESETS
from bot.backtester import Backtester

ALLOW = ['TIAUSDT', 'EIGENUSDT', 'INJUSDT', 'THETAUSDT', '1000PEPEUSDT', 'SOLUSDT']
KDIR = Path('/tmp/s60/fullklines')

def main():
    preset = dict(ALL_PRESETS['mr_fade'])
    pos = 0
    print(f"{'sym':13} {'n':>4} {'net%':>8}")
    for s in ALLOW:
        f = KDIR / f'{s}.json'
        if not f.exists():
            print(f'{s:13} no klines'); continue
        kl = json.load(open(f))
        bt = Backtester(load_settings(s), initial_balance=1000.0)
        r = bt.run(kl, {'mr_fade': preset})['mr_fade']
        net = r.total_profit_pct()
        pos += 1 if net > 0 else 0
        print(f'{s:13} {len(r.trades):>4} {net:>8.2f}')
    print(f"\nnet-positive on {pos}/{len(ALLOW)} allow-list symbols")
    assert pos >= 4, "Gate A FAILED: MR edge did not survive the real backtester"
    print("Gate A PASSED")

if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run it — observe whether MR fires at all**

Run: `python scripts/validate_mr_backtest.py`
Expected (first run may reveal): if `n` is ~0 for every symbol, the backtester's TP/SL/RR gate block (lines 335–404) is rejecting MR recs. Inspect those lines for gates that reject `tp==mid`/geometry (e.g. `min_profit_pct`, SL-floor, RR floor, `tp_multiplier`).

- [ ] **Step 3: Guard MR from geometry-mangling gates (only if Step 2 shows suppression)**

In `bot/backtester.py`, wrap the TP/SL adjustment + RR-rejection gates so MR bypasses the ones that assume swing geometry. Example pattern around the gate block:
```python
                    is_mr = rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
                    # ... existing tp_multiplier / SL-floor / RR gates ...
                    if not is_mr:
                        # existing min_rr / min_profit_pct / tp_multiplier logic unchanged
                        ...
```
(Import `RecommendationTypes` in `backtester.py` if not already.) Keep the `max_sl_pct` guard active for MR (safety). Re-run Step 2 until MR fires.

- [ ] **Step 4: Verify the edge survives**

Run: `python scripts/validate_mr_backtest.py`
Expected: `net-positive on >=4/6 allow-list symbols`, `Gate A PASSED`. Record the printed per-symbol net% in the session notes.

- [ ] **Step 5: Decision checkpoint (STOP — human review)**

If Gate A does NOT pass (edge does not survive real sizing/fees/cooldowns): STOP. The premise failed the real engine — do not enable MR anywhere. Report numbers, and treat the overlay as a rejected experiment (update spec §8 accordingly). Do NOT proceed to Task 7 without a passing Gate A.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate_mr_backtest.py bot/backtester.py
git commit -m "feat(mr): Gate A backtester validation harness + MR gate guards"
```

---

### Task 7: Observability + docs (only after Gate A passes)

**Files:**
- Modify: `main.py` — decision-log entry for MR signals (near the `dl_record` call sites, ~line 481+)
- Modify: `FEATURES.md`
- Test: manual log inspection

**Interfaces:**
- Consumes: live MR recs flowing through `_try_place_order` (already routed — no order-path change).
- Produces: a `dl_record` entry tagging MR placements/skips so the overlay is observable in the decision log.

- [ ] **Step 1: Add decision-log visibility**

In `main.py`, where the placed/skip decisions are recorded, include the signal type so MR trades are filterable. At the existing `dl_record(decision='placed', ...)` call (~line 818), confirm `signal_type=best.getType().value` is already passed (it is via `best.getType()`); if MR needs a distinct marker, add `is_mean_reversion=(best.getType() == RecommendationTypes.MEAN_REVERT_FADE)` to the record kwargs. Import `RecommendationTypes` in `main.py` if absent.

- [ ] **Step 2: Run the existing suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 3: Update FEATURES.md**

Add an entry: what the MR overlay does, files (`bot/mean_reversion.py`, engine wiring, `mr_fade` preset), config knobs (`enable_mean_reversion` + `mr_*` + per-symbol env), allow-list, default OFF, and the four validation gates.

- [ ] **Step 4: Commit**

```bash
git add main.py FEATURES.md
git commit -m "feat(mr): decision-log visibility + FEATURES docs for MR overlay"
```

---

## Post-plan: remaining gates (NOT code — operational)

- **Gate B (risk-throttled):** already exercised inside Gate A (the real backtester applies cooldowns/one-position). Confirm the passing symbols stay net-positive with cooldowns on. If cooldowns erase the edge, treat as fail.
- **Gate C (unit tests):** Tasks 1–5 cover `detect_range`/`mr_signal`/engine/routing including the breakout-de-qualifies test (Task 1 `test_detect_range_rejects_breakout_series`).
- **Gate D (testnet):** enable `enable_mean_reversion` + per-symbol allow-list on **testnet only** via `risk_config.json`/env; observe for a set window; compare live MR fills to Gate A expectation before any scale-up. This is a `bfb-config` + `bfb-deploy` operation, user-gated — not part of this plan.

---

## Self-review notes

- **Spec coverage:** §3.1 → Tasks 1–2; §3.2 regime-switch → Task 4; §3.3 invalidation → emergent, covered by Task 1 breakout-reject test; §3.4 preset/routing → Tasks 3, 5; §4 config/gating → Task 3 (+ per-symbol env free); §5 Gates A–D → Task 6 + post-plan; §6 rejected alts → encoded as fixed defaults (not knobs to re-explore). All covered.
- **Type consistency:** `MRConfig`/`Range`/`MRSignal` field names identical across Tasks 1–4; `generate(..., recent_klines=None)` signature identical in Tasks 4–5; `MEAN_REVERT_FADE` value string identical in Tasks 3–4, 7.
- **Key risk surfaced in-plan:** Task 6 Step 2–3 handles the real integration unknown (backtester TP/RR gates possibly rejecting MR geometry) explicitly rather than assuming it works.
