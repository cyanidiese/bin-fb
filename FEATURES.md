# FEATURES.md — Binance Futures Trading Bot

Comprehensive reference of all implemented features. Each section lists what the feature does, which files implement it, and key config/behaviour details.

---

## Data Feed & Kline Management

### Live Price & Kline Streams
Fetches real-time kline data via WebSocket (per-symbol) and REST fallback. Detects candle closes precisely using `k.x == true` flag. Supports both testnet and live Binance Futures.

**Files**: `bot/data_feed.py`
**Key details**:
- REST base URLs: `https://testnet.binancefuture.com` (test) vs `https://fapi.binance.com` (live)
- WebSocket: `wss://stream.binancefuture.com` (test) vs `wss://fstream.binance.com` (live)
- Combined stream for all active symbols to reduce connection overhead
- Auto-reconnect with exponential backoff
- Price staleness detection: alerts if no candle received for `price_stale_threshold_s` (default 15s)

### Kline Cache with Gap Detection
Loads up to 1000 recent klines on startup, stores locally, and merges new candles. Detects time gaps and re-fetches if needed. Separate cache per symbol/timeframe/mode.

**Files**: `bot/data_feed.py` (refresh_klines, _merge)
**Key details**:
- Cache file: `data/{SYMBOL}_{TIMEFRAME}_{MODE}.json`
- Max 1000 candles kept (gitignored)
- Gap detection: if time jump > 1 candle duration, discard stale cache and re-fetch
- Gracefully handles network errors and cache corruption

### Startup Kline Fallback (Rate-Limit Resilience)
When update fetch fails after startup (e.g., Binance IP ban during initial sync), bot proceeds with cached klines if available instead of crashing. Only raises if no cache exists.

**Files**: `bot/data_feed.py` (load_klines)
**Key details**:
- Update fetch failure with valid cache: log warning and continue with `fresh = []`
- No valid cache at all: raise exception (startup abort, data required)
- Enables bot to survive temporary IP bans at startup and recover once rate limit lifts

### Multi-Symbol Support
Bot processes multiple symbols concurrently via asyncio. Symbol list stored in `symbol_registry.json`. Can add/remove symbols at runtime via dashboard Settings page.

**Files**: `bot/symbol_registry.py`, `main.py` (on_candle_close per symbol)
**Key details**:
- Registry persists to `symbol_registry.json` (seed from `SYMBOLS` env var on first startup)
- Per-symbol status tracking: backtest state, active/disabled
- Subscriber callback system for registry changes
- Per-rank symbol disable: can disable rank 2–6 positions per symbol without affecting rank 1 (real orders)
- **Hot-reload**: `reload_from_disk()` method allows live re-reading symbol_registry.json without restarting bot (session 42 improvement)

---

## Strategy & Analysis

### Swing Point Detection (Price Action)
Detects swing highs/lows using the `SWING_NEIGHBOURS` rule: a point is a swing if it's an extreme among its N neighboring candles (default N=3, env: `SWING_NEIGHBOURS`).

**Files**: `bot/kline_processor.py`, `bot/point.py`
**Key details**:
- Swing type: high (peak) or low (trough)
- Stores: open_time, high, low, close, volume
- Exportable metadata: level assignment, active flag (whether currently in live trend or historically wiped)

### Multi-Level Trend Tracking
Hierarchical trend structure: L1 (finest detail, 1-3 swings) → L2 (coarser, impulse moves) → L3 (very coarse, multi-day structure). Each level independently tracks ascending/descending direction, last swing type, and break-of-structure (BoS) events.

**Files**: `bot/trend.py`
**Key details**:
- L1 trends built from raw klines
- L2/L3 built from L1 swings (one L2 low = multiple L1 swings)
- BoS: trend flips when price closes through the previous swing's extremum
- `removePointsUpTo()` wipes points on BoS (only live trend; permanent history preserved by Analyzer)
- `getRecommendation()` generates trading signals from trend state

### Permanent Swing Point History
`Analyzer._all_points` accumulates every detected swing point with level assignment, capturing BEFORE any BoS wipeout. Dashboard uses this for complete historical context; live trend shows only active points.

**Files**: `bot/analyzer.py`
**Key details**:
- Captured at detection time and after each level promotion (L1→L2→L3)
- Never cleared on BoS
- Exporter uses history; fallback to live-trend traversal if not provided
- Enables post-run analysis: "Which points triggered the trend flip?"

### Break-of-Structure (BoS) Entry Signals
Post-BoS L2 entry signals using minimum swing point requirements. When BoS occurs, new trend begins with fewer initial swings. Presets allow entry immediately after BoS with relaxed `min_swing_points` settings (default 2 for BoS presets vs 3 global).

**Files**: `bot/trend.py`, `config/settings.py`, `config/presets.py`, `bot/recommendation_engine.py`, `backtest_api.py`
**Key settings**:
- `min_swing_points_projection: int` (default 1) — minimum swing points in next BoS structure before entry allowed (usually 1, meaning enter on first swing of next level)
- Settings propagated through `trend.py::getSupposedNextPoints(min_pts)` to feed recommendation_engine with low thresholds

**New presets**:
- `l2_bos_entry` — min_swing_points=2, min_swing_points_projection=1, ignore_parent_alignment=True. Post-BoS L2 entry with minimal data.
- `l2_bos_trend` — same settings plus trend-aware filtering (ignore_parent_alignment=False).

### Recommendation Generation & Scoring
Analyzes trend state at candle open and generates a single best trading signal (BUY or SELL) with entry, TP, SL, and a 0.0–1.0 precision score.

**Files**: `bot/recommendation_engine.py`, `bot/recommendation.py`, `bot/trend.py`, `bot/analyzer.py` (session 42 updates)
**Key details**:

**Signal types**:
- `RISING_ABOVE_LAST_LOW` / `LOWERING_BELOW_LAST_HIGH` (primary entries after swing confirmation)
- `ASCENDING_NEAR_HIGHER_LOW` / `DESCENDING_NEAR_LOWER_HIGH` (pre-confirmation entries, if enabled)
- BoS-triggered entries (RISING_ABOVE_LAST_LOW / LOWERING_BELOW_LAST_HIGH on new trend post-structure break)

**Filtering**:
- Min swing points per level: `min_swing_points` (default 3, env: `MIN_SWING_POINTS`)
- Min profit target: `min_profit_pct` (default 0.5, env: `MIN_PROFIT_PCT`)
- Min profit/loss ratio: `min_profit_loss_ratio` (default 1.5, env: `MIN_PROFIT_LOSS_RATIO`)

**Precision scoring (0.0–1.0)**:
- Projection reliability (0.40 weight): variance of recent swing magnitudes
- Parent trend alignment (0.35 weight): does L2+ trend agree with signal?
- Entry quality (0.25 weight): proximity to optimal entry zone

**Advanced settings**:
- `sl_adjust_to_rr`: tighten SL to meet R:R target if structure SL is too loose
- `sl_filter_min/max_pct`: skip if SL is too tight/loose relative to entry
- `min_sl_atr_mult`: skip if structure SL < ATR × multiplier (volatility-aware)
- `max_profit_pct`: skip if TP target > % threshold
- `correction_weight`: bonus precision if signal follows a well-formed pullback/correction (0.0 = disabled)
- `higher_low_buy` / `lower_high_sell`: enable pre-confirmation entry signals
- `trailing_stop_pct`, `partial_take_pct`: order exit mechanics (see Order Execution)

**Session 42 enhancement — preset-specific signal generation fallback**:
- Base `RecommendationEngine` runs with base settings. Symbols whose best preset requires feature overrides (e.g., `higher_low_buy=True`) that differ from base may not generate signals from base engine.
- New fallback logic: if base engine returns None and best_preset differs from base, call `get_recommendation_for_preset(preset_overrides)` to try the signal with full preset settings.
- Only runs at candidates-gate time if base fails. `_try_place_order` re-runs the full engine with preset overrides anyway, so no double-counting.
- Fixes: `hl_buy_trail15` and other hl_buy/lh_sell-dependent presets now generate signals live instead of being silently skipped at gate.

### Early Loss Exit (Session 29)
Close trade early when adverse move becomes too large, reducing loss severity. Three independent thresholds. Implemented in backtester and live/virtual bot.

**Files**: `config/settings.py`, `config/presets.py`, `bot/backtester.py`, `bot/fake_order.py`, `main.py`, `bot/virtual_order_simulator.py`
**Key settings** (all default 0 = disabled):
- `max_losing_pct: float` — exit when adverse move reaches X% of full SL distance (e.g., 0.50 = halfway to SL)
- `max_losing_amount_usdt: float` — exit when unrealized loss exceeds X USDT (live/virtual only; backtest uses entry margin)
- `max_losing_candles: int` — exit after N consecutive candles with close on wrong side of entry

**Implementation**:
- **Backtester** (`bot/fake_order.py`): on each candle, compute unrealized PnL and candle direction bias; check all three thresholds; exit at candle close if triggered
- **Live bot** (`main.py` in `check_symbol_price()`): on each price tick, compute current position PnL; trigger market close if threshold hit
- **Virtual simulator** (`bot/virtual_order_simulator.py`): same tick-level checks as live; close position if threshold exceeded
- **Presets**: 29 presets updated with safe-floor settings (e.g., 0.50 pct + 5 candles); 3 standout presets tuned with max-profit settings (pct=0.70, candles=5)
- **Session 57 refinement (2026-07-13)**: Added `max_losing_candles: 96` (24 hours at 15m candles) to 6 non-locked l2_* presets (`l2_bos_entry`, `l2_bos_trend`, `l2_trend_sell`, `l2_trend_buy`, `l2_regime_aware`, `l2_regime_aware_strict`) to prevent stuck positions. Value calibrated from real l2_* trade history: winning trades close within 16.3h max (avg 5.2h), losing trades within 3.4h max (avg 0.7h). 24h cap stops only genuinely stuck positions, never cuts legitimate in-progress trades. LOCKED_PRESETS (code-protected) intentionally left untouched.
- **Dashboard**: PresetSettingsPanel shows "Early exit" section with all 3 settings; Create page filtering supports mlp/mla/mlc abbreviations

### Cooldown Mechanisms
Prevents repeated entries after losses and reduces overtrading in choppy markets. Implemented in both backtester and live bot.

**Files**: `bot/recommendation_engine.py`, `config/settings.py`, `main.py` (live implementation), `bot/backtester.py` (backtest implementation)
**Key details**:
- **Loss streak cooldown**: After `loss_streak_max` consecutive losses on one side (BUY or SELL), block that side for `loss_streak_cooldown_candles` candles. Per-direction isolation — BUY losses don't affect SELL and vice versa.
- **Loss counting** (session 36): Now includes trail and partial exits with negative PnL. Previously only SL-hit losses (`result == 'loss'`) incremented streak; trail/partial exits with `pnl_usdt < 0` fell into else branch and RESET the streak. Now: `is_loss = c.get('result') == 'loss' or (c.get('result') in ('trail', 'partial') and c.get('pnl_usdt', 0.0) < 0)`. Prevents rapid re-entry after losing trail exits.
- **Global pause**: If both BUY and SELL lose within `global_pause_trigger_candles` of each other, pause ALL entries for `global_pause_candles` candles.
- **Live bot implementation** (session 28): Added state dicts `_loss_streak`, `_streak_blocked`, `_global_pause_until`, `_last_loss_ts`. New helper `_update_loss_streak()` called from both candle-close and price-update loops. Gate check in `_try_place_order()` skips signals during active cooldowns.
- **Zone SL cooldown** (session 37): After `zone_sl_max` consecutive SL hits at the same price level (within `duplicate_skip_pct%`), block that side for `zone_sl_cooldown_candles` candles. Prevents re-entry on stale support/resistance zones. State tracked in `_zone_sl_count`, `_zone_sl_level`, `_zone_sl_block` dicts. Implemented in both main.py and backtester for consistency.

### Hard Parent-Trend Alignment Gate (Session 37)
Filters continuation-type signals that oppose the parent trend direction. Prevents entry into signals that contradict coarser trend structure.

**Files**: `bot/recommendation_engine.py`, `config/settings.py`
**Key details**:
- **Continuation types** (exempt from counter-trend entry): RISING_BELOW_LAST_HIGH, LOWERING_ABOVE_LAST_LOW
- **Reversal types** (allowed counter-trend): RISING_ABOVE_SUPPOSED_HIGH, DESCENDING_NEAR_LOWER_HIGH, ASCENDING_NEAR_HIGHER_LOW
- **Logic**: New helper `_parent_is_opposing()` checks if L2+ trend explicitly opposes signal direction (BUY in descending trend = skip). Only applies to continuation types.
- **Root cause fixed** (session 37): May 23-25 analysis showed every BUY trade lost because continuation signals fired while L2 trend was descending. Now blocked at filter stage.

### Minimum Precision Floor (Session 37)
Filters low-confidence signals based on precision score. Tunable per preset for fine-grained control.

**Files**: `bot/recommendation_engine.py`, `config/settings.py`, `config/presets.py`
**Key details**:
- **Setting**: `min_precision_score: float` (default 0.0 = disabled)
- **Implementation**: In `_score_and_filter()`, after computing precision (0.0–1.0), skip candidates with `score < min_precision_score`
- **Use case**: Raise threshold to 0.3–0.4 in choppy markets to filter noisy signals; lower in trending markets
- **Dashboard**: PresetSettingsPanel shows as "Entry filter" control, per-preset override

---

## Backtesting System

### Preset Definition & Execution
100+ presets (parameter combinations) defined in `config/presets.py` (centralized). Each preset is a dict of Settings field overrides. Backtester replays klines and simulates fake orders to compute win rate, profit %, max drawdown, etc.

**Files**: `config/presets.py` (PRESETS, LOCKED_PRESETS, ALL_PRESETS), `backtest.py`, `bot/backtester.py`
**Key details**:
- Presets centralized in `config/presets.py` (session 22) — PRESETS dict (user-modifiable), LOCKED_PRESETS dict (code-level, only unlocked by source edit)
- Locked presets: `trail_15_from_30_full`, `trail_15_from_30_cooldown`, `sl_adjust_rr_tp95`, `trail_20_from_30_cooldown`
- User can lock/unlock additional presets via dashboard; lock status persists in JSON
- `backtest.py` and `discover.py` import from `config/presets.py`
- Backtest output: `backtest_results.json` (live feed) and `backtest_{timestamp}.json` (archive)
- Klines limit control: `--klines-count N` CLI flag for faster reruns on cached data

### Range Position Max Tuning (Session 38)
Each of the 78 presets has been tuned with an optimal `range_position_max` value (0.1–1.0) based on systematic sweep analysis across all 15 symbols.

**Files**: `config/presets.py` — each preset has `range_position_max` field
**Key details**:
- **Sweep methodology**: 78 presets × 15 symbols × 6 values [1.0, 0.8, 0.65, 0.5, 0.3, 0.1] tested. Average profit per symbol calculated for each combination.
- **Assignment rationale**: Values chosen based on data, not heuristic. 4 groups flip from negative to positive; 27 in 0.10 group confirmed to show monotonic improvement across all sweep values (genuine quality gain, not trade suppression).
- **Grand total improvement**: +1,670.47% aggregate profit across all 78×15 combinations.
- **Verification step**: Deep analysis confirmed the 0.10 group improvement is real (trade quality) not artifact (zero-trade suppression bias). All 31 presets show consistent improvement across all 6 values and 15 symbols.

### Fake Order Engine
Simulates order entry/exit without exchange API. Entry at next-candle open. Exit on TP hit, SL hit, or trailing stop trigger. Computes PnL, win/loss/partial result.

**Files**: `bot/fake_order.py`
**Key details**:
- **Trailing stop (standard case)**: when profit % reaches `partial_take_pct`, arm at `_partial_price`, then exit on next candle if price retraces by `trailing_stop_pct`
- **Trail-only presets** (session 58 fix): Presets with `trailing_stop_pct > 0` but `partial_take_pct == 0` cannot arm via `_partial_price`. New logic: if `trail_activation_pct > 0`, arm at `entry * (1 ± trail_activation_pct/100)` for each direction (BUY: 1 - pct%, SELL: 1 + pct%). Presets without `trail_activation_pct` remain documented dead (no arming).
- **Trail min-arming for mixed presets** (session 59 fix, commit c77dc4a): For presets with both `partial_take_pct > 0` AND `trailing_stop_pct > 0` AND `trail_activation_pct > 0`, arm threshold is now `min(partial_price, activation_price)` for BUY / `max(...)` for SELL. This prevents waiting for unreachable partial-take TP fractions when activation threshold is hit first. Evidence: EIGENUSDT/INJUSDT positions peaked +4.1-4.3% favorable (above 2% activation) but needed +5.1%/+8.9% to reach old partial arm point — now arms earlier and captures favorable moves.
- Conservative multi-hit logic: if same candle hits both TP and SL, SL assumed first (loss)
- Stores: entry price, TP, SL, entry type, result, profit_pct, symbol, timeframe
- Serializable to JSON for backtest result export
- **Applies consistently**: Live orders (OrderExecutor), virtual simulation (VirtualOrderSimulator), and backtests (Backtester) all use FakeOrder for exit logic

### Backtester State Machine
Iterates klines one by one (no lookahead), generates signals, opens/closes fake orders, tracks compound balance per preset.

**Files**: `bot/backtester.py`
**Key details**:
- One order at a time per preset (no stacking)
- Balance updated per preset: `balance_end = balance_start + sum(trade PnL)`
- Hard-stop gate: if drawdown (starting balance − current) > threshold, no new entries for that preset
- Counts: total trades, wins, losses, win rate, avg R:R, max consecutive losses

### Backtest Results Export
Results written to both live dashboard feed and timestamped archive. Includes per-preset summary and per-trade details.

**Files**: `backtest.py` (calls export), `bot/exporter.py` (export_backtest)
**Key details**:
- Dashboard feed: `dashboard/public/backtest_results.json`
- Preset summary: profit%, win rate, max drawdown, trade count, avg R:R
- Trade list: entry/exit prices, result, PnL, entry type, signal metadata
- Locked presets array persisted in JSON (preserved across backtest reruns)

---

## Order Execution (Real & Virtual)

### Real Order Execution
Places actual orders on Binance Futures (testnet or live based on mode). Wired to full exchange API: order placement, cancellation, state reconciliation, PnL tracking.

**Files**: `bot/order_executor.py`
**Key details**:

**State machine**:
- `IDLE` → `PLACING` → `OPEN` → `CLOSED` (or `PARTIAL_EXIT` → `CLOSED`)

**Exchange integration**:
- `POST /fapi/v1/order` for market entry
- `POST /fapi/v1/order?type=TAKE_PROFIT_MARKET` for TP
- `POST /fapi/v1/order?type=STOP_MARKET` for SL (placed immediately after entry)
- SL order auto-cancelled before software-triggered closes (avoid double-exit)
- Supports partial take: re-enter at TP level before SL is hit (architecture ready, requires exchange-side trailing order on TP level)

**Position sizing**:
- Quantity = `margin / current_price` where `margin = min_notional / current_leverage`
- `min_notional`: symbol-specific minimum notional from exchange (lot size cache fetched at startup)
- **Max notional cap** (session 45):
  - New config field: `max_order_notional_usdt` (default 500.0, upgraded to 5000.0 in session 45)
  - Prevents single orders from exceeding specified notional value (e.g., $5k cap prevents $15k bets on high-leverage symbols)
  - Caps order notional BEFORE placement (quantity adjusted downward if needed)
  - Example: $5k cap at 4x leverage = $1,250 margin, at 0.5% SL = $25 max loss per trade
- **Min notional safeguards** (session 22):
  - When balance < min_notional / leverage: auto-bump leverage up to needed level (capped at bracket_max)
  - If even bracket_max insufficient: skip order with `skip_min_notional` decision log entry
  - 2% quantity buffer applied after margin calculation to prevent rounding below Binance floor
- Leverage gated by RiskManager; per-symbol leverage determined by LeverageScenario

**Entry fill reconciliation** (session 62):
- After placement, `_reconcile_entry_fill()` reads the entry leg's real average fill
  price back from `futures_account_trades(symbol, orderId)` and stores it on
  `OpenOrder.fill_entry_price`. The MARKET response carries `avgPrice="0"` until the
  matching engine settles, so trade records are the reliable source — same approach
  `_market_close()` already used for the exit leg.
- `_effective_entry(order)` returns `fill_entry_price` when set, else `entry_price`
  (the intended/signal price). `_calc_pnl()` and `_order_fee()` both use it, so PnL,
  fee, the Telegram message, `real_orders_*.json`, balance history and the preset
  efficiency ranking are all computed off the price actually paid.
- **Runs last in `place_order()`, after the exchange SL is placed** — it polls for up
  to ~0.5s and must never delay crash protection.
- **Deliberately does NOT feed the FakeOrder or the SL/TP geometry.** Trigger levels
  stay on the signalled entry, so this fix changes reported money only, never when a
  trail arms or a stop fires. (Aligning trigger geometry to the fill is a separate,
  behaviour-changing decision — not done.)
- Logs `Entry slippage: signalled X → filled Y (Z%)` at WARNING when slippage ≥0.05%,
  INFO otherwise; `0.0` fill (lookup failed) falls back to the signalled price with an
  INFO line, preserving pre-session-62 behaviour.
- **Why**: on 2026-08-18 INJUSDT was signalled at 4.052 and filled at 4.0670. PnL
  computed off the signalled price reported +11.81 USDT against Binance's true
  +6.24 — 5.57 USDT of phantom profit from 1.5 cents of slippage on 372.1 units.
  Across the four Aug-18/19 real trades the bot reported +115.71 vs a true +109.26
  (+5.9%); with reconciliation the same four report +109.40 (+0.12%, the residual
  being funding fees, which PnL still does not model).

**Real order persistence**:
- Stored in `data/real_orders_{SYMBOL}_{MODE}.json` (one file per symbol per mode)
- Includes: entry price, **actual fill entry price**, TP, SL, quantity, filled PnL,
  fee, result, signal metadata, balance at open, **wallet at open**
- Old sessions archived to `real_orders_{SYMBOL}_{MODE}_archive_{YYYYMMDDTHHMMSSZ}.json` on bot restart

### Unknown-symbol leverage containment (session 63)
A symbol with no readable `backtest_results_{symbol}.json` scores **0.0** in
`_get_cross_symbol_score`, so it sizes at `base_leverage`.

**Files**: `bot/risk_manager.py`; tests in `tests/test_risk_manager_unknown_symbol.py`

**Key details**:
- Previously returned `0.5`, which on a 2/10 config gave an entirely unknown symbol
  `2 + floor(0.5 * 8)` = **6x leverage** — mid-range sizing for the one case with no
  evidence it can carry leverage at all.
- Symbols *with* data are unaffected: they still normalise across `symbol_weights`
  and scale from base up to the tier ceiling.
- Complements the `can_open_sync` policy, which deliberately allows unknown symbols
  to trade (`pf=0.0` is "no data", not "a loser" — otherwise a new symbol could never
  accumulate data). The risk is contained on the leverage side instead: unknown
  symbols may trade, but only at base leverage.

### Zero-score signal discards are logged (session 63)
When TATS drops a candidate for `score <= 0` — almost always
`risk_config.symbol_weights[sym] == 0` zeroing it — the reason is now written to both
the bot log and the decision log (`decision='skip_zero_score'`).

**Files**: `main.py` (TATS candidate filter)

**Why**: on 2026-08-29 the bot produced 28 valid signals and placed zero orders, and
**nothing in the log explained why** — every signal was JUPUSDT at weight 0, dropped
by a filter with no logging. Diagnosing an idle day required correlating `trades.log`
against `risk_config` by hand.

### Weight=0 Symbol Trading Gate
Symbols with allocation weight set to 0 are excluded from both real order placement and virtual order simulation. Enforced at two points: candidate filtering before real orders, and guard before virtual simulator processing.

**Files**: `main.py`, `symbol_registry.py`
**Key details**:
- Real order loop: `if symbol_registry.get_weight(sym) == 0.0: continue` (line ~676)
- Virtual simulator call: `if symbol_registry.get_weight(symbol) > 0.0:` guard before `on_candle_close()`
- Zero-cap symbols also blocked from BestGetsFirst loop: `if sym_cap <= 0: continue`
- Prevents accidental trading of disabled symbols when weight is set to 0 without calling `disable()`

### Virtual Order Simulation (Rank-Based Pools)
Tracks N independent virtual positions for the top non-best presets (ranks 2–6). Each rank has a shared balance pool across all symbols. When a preset's rank changes (efficiency rankings shift), the old position is evicted at current price and the new rank-N preset opens fresh.

**Files**: `bot/virtual_order_simulator.py`, `bot/virtual_tracker.py`
**Key details**:

**Architecture**:
- Rank 1 = best preset → real order (not tracked here)
- Ranks 2..6 = virtual positions, one independent pool per rank
- At any time, each symbol contributes ≤1 open position per rank
- When preset efficiency rankings change, rank-N position switches to new preset
- Position switchover recorded as `rank_change` result (evict at current price)

**Preset efficiency scoring (session 32 refinement — two-tier ranking)**:
- Two-tier tuple system: `(tier: int, value: float)` where tier 1 (live-proven) always beats tier 0 (seed-only)
- Tier 1: preset has ≥ N real+virtual trades (default N=3, per-symbol overrides in risk_config.json)
- Tier 0: preset has < N trades (backtest-only seed, ranked by seeded_winning_usdt)
- Configurable threshold: `get_min_trades_for_ranking(cfg, symbol)` reads global default then per-symbol override
- No blend formula (eliminates abrupt inversions where large seed beat better live record)
- Fixes TIAUSDT case: `trail_15_from_15` (4 real, -$14, 25% win) no longer blocks `pre_confirm_prox15_trail15` (5 virtual, +$66, 60% win)
- `best_preset(symbol)` returns highest-tier, highest-score preset; tracks rank history in `_last_best` dict
- Logs preset changes with trade counts and tier/score values for analysis
- `get_efficiency_score(symbol)` used to rank symbols for real-order loop
- `get_preset_efficiency(symbol, preset)` used to rank presets within symbol for virtual allocation

**Virtual balance**:
- Shared pool per rank, initialized from real balance at mode start
- Updated on virtual order close (PnL applied)
- Separate from real balance (never reads from exchange)
- Persists to `data/virtual_orders_rank{N}_{SYMBOL}_{MODE}.json`
- Max 500 closed positions kept per rank+symbol file

**Per-rank symbol disable**:
- Can disable rank 2–6 positions for a symbol via dashboard toggle
- Real order (rank 1) unaffected

**Trade order sorting (session 26)**:
- Real and virtual orders intermixed and sorted chronologically by `open_time` descending
- Unified timeline view across all ranks and trade types

### Last-N Preset Ranking with Virtual-Only Floor Gate (Session 35)
Sliding-window ranking system that elevates recent performance and prevents negative-efficiency presets from executing real orders. Once a preset accumulates N live trades, scoring switches from all-time cumulative to sum of last N trades.

**Files**: `bot/virtual_tracker.py`, `config/risk_config.py`, `main.py`, `dashboard/app/api/trades/route.ts`, `tests/test_virtual_tracker.py`
**Key details**:
- **Recent trades window**: VirtualTracker stores `recent_trades: float[]` per preset (capped to `ranking_window_size`, default 10)
- **Window-based scoring**: Once preset has ≥ `min_trades_for_ranking` live trades (default 3), ranking uses sum of last N trades instead of all-time cumulative. Fallback to cumulative during warm-up phase.
- **Virtual-only floor gate**: New method `is_virtual_only(symbol)` checks if best-ranked preset score < `virtual_only_floor` (default -20.0 USDT) AND trade_count ≥ min_trades_for_ranking. If true, skip real order; log warning. Locked presets bypass gate entirely.
- **Config keys** (new in session 35):
  - `ranking_window_size: int` (default 10) — number of recent trades to include in window
  - `virtual_only_floor: float` (default -20.0) — minimum score threshold for real order eligibility
- **Backward compatibility**: preset_efficiency JSON files gain `recent_trades: []` field (missing field treated as empty list)
- **Dashboard**: effectiveScore() helper now mirrors Python window logic; MIN_TRADES hard-code removed (reads from risk_config default)
- **Tests**: 17 passing (test_virtual_tracker.py includes window logic + floor gate tests)
- **Impact**: Eliminates negative-efficiency order execution (session 35 analysis found 12/54 orders from negative-efficiency presets, -$25 loss). Promotes recent winners faster than all-time averaging allows.

---

## Risk Management

### Preset Blocklist (Session 43)
Prevents specific presets from being used for real order placement. Blocklisted presets are excluded from the scoring and selection process entirely. Useful for disabling presets that were historically successful but have degraded in recent conditions.

**Files**: `config/risk_config.py`, `bot/virtual_tracker.py`, `main.py`
**Key details**:
- **Config field**: `preset_blocklist: list[str]` in risk_config.json (contains preset names to block)
- **Bot behavior**: In `VirtualTracker.best_preset()` (session 45), blocklisted presets are filtered out BEFORE the `max()` scoring selection. Also resets `_last_best` sentinel when previous best preset is now blocklisted, preventing hysteresis from locking into an ineligible preset.
- **Virtual tracking**: Blocklisted presets can still accumulate virtual trades (rank 2–6) for historical tracking, but do NOT participate in real order selection
- **Difference from weight=0**: Weight=0 excludes entire symbol; blocklist excludes specific preset while other presets for that symbol remain available
- **Session 45 discovery**: Blocklist had been missing from risk_config.json during June 6–11, explaining why blocklisted presets were trading. Adding it + fixing the filter logic resolved 1000PEPEUSDT deadlock (db_clone_cooldown was blocklisted but kept winning, blocking all other presets)
- **Current blocklist** (session 43–45): db_clone_cooldown, pre_confirm_prox15_trail15, pre_confirm_trail15, trail_15_from_15_d1, sl_adjust_rr_tp95, r6_arm15_rr4, correction_w20_trail15_30, trail_15_from_15

### Lock Preset Per Symbol (Session 33, fixed Session 50)
Allows manual override of automatic preset selection for a specific symbol. When locked, bot uses the designated preset for that symbol instead of calling `best_preset()` for efficiency ranking. **Critical design**: Locked presets bypass BOTH the virtual_tracker selection AND the global preset_blocklist.

**Files**: `dashboard/app/api/risk/lock-preset/route.ts` (NEW), `config/risk_config.py`, `dashboard/app/api/risk/route.ts`, `dashboard/lib/risk-types.ts`, `dashboard/app/trades/page.tsx`, `main.py` (~line 425 in `_try_place_order`)
**Key details**:
- **API endpoint** (`/api/risk/lock-preset`): POST `{symbol, preset}` to lock, POST `{symbol: "BTCUSDT", preset: null}` to unlock
- **Config field**: `locked_presets: Record<string, string>` in risk_config.json (maps symbol → preset name)
- **Bot behavior**: In `_try_place_order`, before calling `best_preset()`, checks `if symbol in locked_presets: use locked_presets[symbol]` directly; logs info-level message
- **Blocklist bypass** (Session 50 fix, commit 4276319): Locked presets skip the blocklist check entirely. Main.py line 441 now: `if not is_locked and preset_name in _blocklist:` — blocklist only applies to non-locked presets. This allows locking a blocklisted preset to force its use despite poor global performance.
- **Dashboard UI** (Trades page): 🔒/🔓 button appears per preset row; locked row highlighted in amber; button fetches locked preset state on symbol change
- **Use case**: Manually pin a specific preset to a symbol when you want to override the auto-ranking AND global blocklist (e.g., lock r6_arm15_rr4 to DOGEUSDT when it's best for DOGE but blocklisted globally because it hurts ETHFIUSDT; lock a conservative preset during high drawdown)
- **Current state** (session 51): DOGEUSDT locked to r6_arm15_rr4, MEMEUSDT locked to sl_adjust_rr_tp95 (both presets are globally blocklisted but best for their respective symbols). Locks are now working correctly after session 50 blocklist bypass fix and session 51 Docker rebuild.

### Virtual Orders for Disabled Symbols (Session 50, deployed Session 51)
Disabled symbols continue accumulating virtual order performance data (ranks 2–6) even though they are blocked from real order placement. Allows tracking preset efficiency on disabled symbols without placing real orders.

**Files**: `main.py` (on_candle_close), `bot/virtual_order_simulator.py`
**Key details**:
- **Disabled symbol behavior**: `symbol_registry.is_disabled(symbol)` blocks real order placement, but virtual simulator still runs
- **Virtual-only mode**: `on_candle_close(symbol, virtual_only=True)` called for disabled symbols; opens/closes virtual orders at ranks 2–6
- **Efficiency accumulation**: Virtual order results recorded to `data/virtual_orders_rank{N}_{SYMBOL}_{MODE}.json`
- **Use case**: Keep performance data current for symbols temporarily disabled (e.g., due to precision errors or market regime change), allowing quick re-enablement with fresh efficiency baseline
- **Implementation** (commit f0323a1): Added check in main.py: `if symbol_registry.is_disabled(sym): virtual_order_simulator.on_candle_close(sym, virtual_only=True)`

### Position Persistence on Restart (Session 50, deployed Session 51)
Open positions are automatically restored on bot restart instead of being force-closed. Allows uninterrupted trading across bot restarts and deployments.

**Files**: `main.py` (startup + shutdown), `bot/order_executor.py`, `bot/virtual_order_simulator.py`, `config/risk_config.py`
**Key details**:
- **Config**: `close_positions_on_stop: bool` in risk_config.json (default: false = persist; true = close on stop)
- **On shutdown** (`on_stop_bot`): If `close_positions_on_stop=false`, save open position state to `data/restart_positions_{mode}.json` before closing (lists symbol, side, entry, qty, preset for each open order)
- **On startup**: Call `restore_open_positions()` before `reconcile_with_exchange()` to reload saved positions into memory
- **Exchange reconciliation**: After restoring, `reconcile_with_exchange()` verifies positions still exist on exchange; closes any that were liquidated/manually closed during downtime
- **Default behavior** (false): Positions resume transparently; old trades continue with original entry/SL/TP
- **Conservative mode** (true): Force-closes all positions at market price on stop (original behavior)
- **Persistence format**: JSON file with per-symbol order state: `{ "symbol": "BTCUSDT", "side": "BUY", "entry_price": 0.12345, ... }`
- **Not yet tested in production** (session 50/51): No position was open during first proper deploy (17:38 Jun 14); needs verification on next restart with open position

### Drag-and-Drop Symbol Weights (Session 33)
Interactive reordering of symbol allocation weights via drag-and-drop in the Risk dashboard. Dragging a row reassigns weights 1..N based on drop position, then updates risk_config.json atomically.

**Files**: `dashboard/components/risk/PerSymbolAllocation.tsx`, `dashboard/package.json` (added `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`)
**Key details**:
- **Libraries**: `@dnd-kit/core` + `@dnd-kit/sortable` + `@dnd-kit/utilities` (lightweight, no heavy UI library)
- **Component**: Wraps table tbody with `DndContext` + `SortableContext`. Each row is a `SortableRow` with `useSortable()` hook.
- **Drag handle**: `GripIcon` SVG (hamburger icon) appears in first column, allows grab-and-drag
- **Drop logic**: `onDragEnd` computes new weight order (1..N), updates state, calls `patchConfig` to persist
- **Both modes**: Works in both BGF (Best-Gets-First) and non-BGF (allocation weighting) modes
- **Visual feedback**: Row opacity changes during drag; standard pointer cursor on handle

### WeightRebalancer Restyle (Session 33)
Unified visual styling for WeightRebalancerSection to match all other risk management widgets. Removed collapsible state and standardized layout.

**Files**: `dashboard/components/risk/WeightRebalancerSection.tsx`
**Key details**:
- **Layout primitives**: Uses `SECTION_CLS`, `SECTION_HEADER_CLS`, `SECTION_BODY_CLS` (standard throughout Risk page)
- **Form inputs**: All numeric fields wrapped in `LabeledInput` components (consistent with other sections)
- **Colors**: Text changed to `text-xs` and `gray-*` palette to match peer sections
- **Visibility**: Body always visible (removed `open` collapsible state)
- **No behavioral change**: Feature logic unchanged, only visual unification

### Per-Symbol Settings Overrides (Session 37)
Override any preset setting for a specific symbol, allowing capital-unlock and fine-tuning without creating new presets. Applied at order placement time after preset selection.

**Files**: `main.py` (_try_place_order), `config/risk_config.py`, server `risk_config.json`
**Key details**:
- **Config structure**: `risk_config.json["per_symbol_settings"]: { "INJUSDT": { "max_profit_pct": 5.0 }, ... }`
- **Application**: In `_try_place_order()`, after constructing `preset_settings`, reads overrides and applies via `setattr()` on the Settings object
- **Use case**: INJUSDT had 75 signals blocked at 4.3–5.0% projected profit by global 3.0% cap. Override to 5.0% unlocks those signals.
- **Any setting supported**: Any Settings field can be overridden (e.g., tp_multiplier, max_sl_pct, min_precision_score, etc.)
- **Priority**: Per-symbol override > preset setting > default setting

### Per-Symbol SL Caps (Session 59)
Data-backed stop-loss width limits per symbol, protecting against artifact SL geometry from cross-level signal sourcing. Applied via per_symbol_settings `max_sl_pct` override.

**Files**: `main.py` (_try_place_order), `config/risk_config.py`, server `risk_config.json`
**Key details**:
- **Rationale**: Analysis of 260 all-time trades revealed SL-width distribution: 8%+ SL bucket is artifact zone (-$205 net, n=2 outliers including one -$206.12 catastrophic loss), while 4-8% SL bucket is healthy (+$22 net, 45% WR, multiple profitable trades). Root cause: cross-level stop sourcing (session 59 defect #2) produces degenerate geometry with stop distances reaching parent trend extremes unbounded.
- **Current setting (session 59, 2026-07-16)**: `per_symbol_settings.{TIAUSDT, EIGENUSDT, INJUSDT, MEMEUSDT, DOGEUSDT}.max_sl_pct = 8.0`
- **Behavior**: Blocks any signal where computed SL distance exceeds 8% of entry. Interim guard until Fix A (same-level stop sourcing, not yet implemented) resolves root cause.
- **Expected impact**: Eliminates artifact signals with wide SLs, preserves healthy 4-8% range signals.

### Two-Tier Preset Ranking (Session 32)
Replaces hard-coded `_MIN_TRADES = 8` threshold with configurable tuple-based ranking system. Live-proven presets (≥N real+virtual trades) are always ranked above seed-only presets (backtest-only), regardless of seed magnitude. Solves the TIAUSDT problem where a less-profitable preset with a large backtest seed outranked a better-performing preset with fewer live trades.

**Files**: `config/risk_config.py`, `bot/virtual_tracker.py`, `tests/test_virtual_tracker.py`, `main.py`, `dashboard/lib/risk-types.ts`, `dashboard/app/api/risk/route.ts`, `dashboard/components/risk/PresetRankingSection.tsx`, `dashboard/app/risk/page.tsx`
**Key details**:
- **Scoring**: Each preset gets a tuple `(tier, value)` where:
  - Tier 1: preset has ≥ N real+virtual trades (tier value = total_winning_usdt, live track record)
  - Tier 0: preset has < N trades (tier value = seeded_winning_usdt, backtest seed)
  - Python tuple comparison ensures tier 1 always beats tier 0 regardless of magnitude
- **Configurable threshold N**: Default 3, stored in `risk_config.json["min_trades_for_ranking"]`, per-symbol overrides in `risk_config.json["min_trades_for_ranking_per_symbol"]`
- **Implementation**: Module-level `_score(stats, min_trades) -> tuple[int, float]` in `bot/virtual_tracker.py` computes tier and value. VirtualTracker constructor accepts `get_min_trades: Callable[[str], int]` callable.
- **Lambda closure**: In `main.py`, `_get_min_trades` lambda correctly captures hot-reloaded `risk_cfg` because Python closures capture cell references, not values.
- **Dashboard UI** (PresetRankingSection): Global threshold input (slider 1–20), per-symbol override table with add/remove controls
- **Motivation**: TIAUSDT had `trail_15_from_15` (4 real trades, -$14, 25% win rate, $694 backtest seed) blocking `pre_confirm_prox15_trail15` (5 virtual trades, +$66, 60% win rate) purely due to seed magnitude. Two-tier ranking fixes this by elevating the better-performing 5-trade preset despite its smaller seed.

### Dynamic Weight Rebalancer (Session 31)
Gradually rebalances `symbol_weights` in risk_config.json based on real-time symbol performance. Every N closed candles, scores each symbol on two metrics (mini-backtest recent klines + real closed P&L), rank-normalizes both, and soft-blends current weights toward new scores. Better-performing symbols accumulate more allocation; a floor prevents any symbol from dropping below minimum share.

**Files**: `bot/weight_rebalancer.py`, `tests/test_weight_rebalancer.py`, `dashboard/components/risk/WeightRebalancerSection.tsx`
**Key details**:
- Triggered: every `rebalance_candles` closed candles (default 96, ~1 day at 15m)
- Scoring: `backtest_window_candles` lookback window (default 96)
  - `backtest_score`: mini-backtest profit % on recent klines using best preset for symbol
  - `real_pnl_score`: actual closed P&L $ from real orders in same window
  - `real_pnl_alpha`: weight blending between the two (default 0.5 = equal weight)
- Rank-normalization: both scores normalized to [0, 1] range per symbol
- Soft-blend: current weights move `blend_rate` toward new scores (default 0.15 = 15% per rebalance)
- Floor protection: no symbol can drop below `weight_floor_ratio / n_active` of equal share (default 0.3 = 30% of equal share)
- State: `_running` dedup prevents concurrent rebalances; log entries appended to state JSON for UI display
- Enabled: **false by default**. When enabled, requires monitoring to ensure blend rate and window match strategy rhythm.

**Config schema** (risk_config.json):
```json
{
  "enabled": false,
  "rebalance_candles": 96,
  "backtest_window_candles": 96,
  "real_pnl_alpha": 0.5,
  "blend_rate": 0.15,
  "weight_floor_ratio": 0.3
}
```

**Known issues** (documented during code review, not blocking for disabled state):
1. Live config changes via dashboard do NOT take effect until bot restart (WeightRebalancer holds stale dict reference)
2. `close_time` ISO string parsing uses `datetime.fromisoformat()` which uses local system timezone (safe on UTC VPS but fragile if timezone changes)

**Dashboard UI** (WeightRebalancerSection):
- Enable/disable toggle
- Config controls: all 6 settings with sliders/inputs
- Last rebalance status: timestamp, number of symbols, current blend rate applied
- Per-symbol table: symbol name, previous weight, new weight, backtest score, real PnL, delta %

### Risk Configuration & State
Centralized risk controls with persistent config and runtime state. Loaded from `risk_config.json` (created on first run with sensible defaults). State persists to `risk_state.json` for dashboard polling.

**Files**: `config/risk_config.py`, `bot/risk_manager.py`
**Key config fields**:
- `balance_tiers`: capital deployment % and leverage ceiling per balance tier
- `base_leverage`: starting leverage for all symbols (default 2)
- `max_leverage`: global ceiling on any symbol's leverage (default 20)
- `min_profit_factor`: presets with PF < this are blocked from opening (default 1.2)
- `drawdown_warning_pct`: alert when drawdown > this % (default 10%)
- `drawdown_hard_stop_pct`: block all orders when drawdown > this % (default 20%)
- `test_starting_balance_usdt`: virtual balance at test mode startup (default 10,000)
- `backtest_initial_balance_usdt`: balance used for backtest presets (default 1,000)
- `use_allocation_weighting`: enables symbol-weight-based capital distribution (archived, default OFF)
- `scenario`: leverage progression scenario name (default "default")
- Telegram config: `token`, `chat_id`, `emergency_repeat_interval_s`, `warning_repeat_interval_s`

### Capital Gates (RiskManager)
Sync method `can_open_sync(symbol, preset_name)` checks:
- Hard stop active? → no
- Preset profit factor ≥ min threshold? → yes

Async method `can_open()` is a wrapper for async contexts.

**Files**: `bot/risk_manager.py`
**Key details**:
- Threading.RLock ensures thread-safe operations in both sync and async contexts
- Caches preset performance scores (60s TTL) to avoid repeated disk reads
- Minimum 4 trade threshold for preset eligibility

### Drawdown Guard
Tracks peak balance and current balance. Issues warning at `drawdown_warning_pct`. At `drawdown_hard_stop_pct`, halts all new orders. Manual reset via dashboard Risk page button.

**Files**: `bot/risk_manager.py`
**Key details**:
- Peak is set to current balance on first mode start (prevents phantom drawdown)
- Updated on every real trade close
- Hard-stop flag latched (requires manual reset, not auto-reset on balance recovery)
- Warning auto-resets when drawdown improves below threshold

### Leverage Progression (LeverageScenario)
Global leverage level starts at 1. Advances to next level only when ALL active symbols have ≥1 closed real order at current level. New symbols only need level 1 before blocking next advance.

**Files**: `bot/leverage_scenario.py`
**Key details**:
- Scenario protocol: pluggable strategies (currently only DefaultScenario implemented)
- Per-symbol level tracking: `symbol_level_{symbol}_{mode}.json`
- Global level file: `leverage_state_{mode}.json`
- Advances logged to system log
- Ceiling: `max_leverage_level` config (default 5)

### Per-Symbol Leverage Overrides (Session 26)
Allows manual override of leverage per symbol in Risk dashboard, independent of global progression. Useful for fine-tuning capital deployment by cross-symbol profit score and risk tolerance.

**Files**: `dashboard/components/risk/PerSymbolAllocation.tsx`, `dashboard/lib/risk-types.ts`, `bot/risk_manager.py`
**Key details**:
- **Dashboard UI**: Section B (Per-Symbol Allocation) shows editable leverage input per symbol in BGF mode
- **Auto-computation**: Default value computed from cross-symbol profit score (mirrors `_calc_leverage` in risk_manager.py)
- **Reactivity**: Values update when base_leverage, max_leverage, or tier leverage_ceiling changes
- **Override persistence**: Manual overrides saved to `config.symbol_leverage` dict in risk_config.json
- **UI feedback**: Shows "auto" label when using computed value; amber "⟳" reset button when manually overridden
- **Runtime application**: `risk_manager.py::_calc_leverage` checks `cfg["symbol_leverage"][symbol]` override before auto-computation
- **TypeScript schema**: `risk-types.ts` extended with `symbol_leverage?: Record<string, number>` in `RiskConfig`

### Balance History
Append-only log of balance snapshots (cap 10k entries). Records at: startup, order open (before), order close (after), or >0.5% change.

**Files**: `bot/balance_history.py`
**Key details**:
- File: `data/balance_history_{MODE}.json`
- Each entry: timestamp, balance, trigger (startup / order_open / order_close / change_threshold)
- Correlates with decision log for post-run analysis

### Allocation Weighting
Optional feature for distributing capital proportionally by symbol weight. Can be toggled via Settings checkbox or risk_config.json.

**Files**: `config/risk_config.py`, `config/settings.py`, `main.py`
**Key details**:
- `symbol_weights` dict in risk config (weight per symbol, default 0-20 range)
- `use_allocation_weighting` flag controls behavior (default false)
- When enabled (session 36): deployable capital distributed as `symbol_allocation = total_deployable × symbol_weight / sum(weights)`. Capital flows proportionally to higher-weighted symbols.
- When disabled: all symbols compete equally for available capital (ranking determines priority)
- **Current weights (session 57, 2026-07-13)**:
  - Active (real orders): SOLUSDT:20, TIAUSDT:15, EIGENUSDT:8, INJUSDT:7, MEMEUSDT:2, DOGEUSDT:1
  - Virtual-only: 1000SHIBUSDT:0, APTUSDT:0, AVAXUSDT:0, ETHFIUSDT:0, JUPUSDT:0, REZUSDT:0, THETAUSDT:0, WLDUSDT:0
- **Previous weights (session 46)**: SOLUSDT:20, TIAUSDT:15, 1000PEPEUSDT:10, MEMEUSDT:8, INJUSDT:3, DOGEUSDT:1
- **Rationale for session 57 rebalancing**: MEMEUSDT reduced (structurally blocked by RR filter due to low volatility 0.53% < 0.7% floor), EIGENUSDT increased to 8 (strong recent performance +$249.50 in 28 trades), INJUSDT increased to 7 (positive contribution +$9.49)

---

## Dashboard (Next.js 15)

### Architecture
Standalone Next.js 15 app under `dashboard/`. Bot and dashboard communicate via JSON files in `dashboard/public/`. No direct API coupling.

**Files**: `dashboard/` (all Next.js 15 app with App Router, TypeScript, Tailwind v4)
**Key details**:
- Live feed: `bot_state.json` (polling 10s), `risk_state.json` (5s), `results_{SYMBOL}.json` (fetch on view)
- Backtest results: `backtest_results.json` (updated on each backtest run)
- Alert feed: `alert_state.json` (undismissed warnings/emergencies)
- System log: `data/system_log.json` (100 rolling entries)

### Strategy Page
Main page (`/`). Shows swing points, trend levels, trading signals, kline chart with price lines and swing markers per level.

**Files**: `dashboard/app/page.tsx`, `dashboard/components/SwingPointsChart.tsx`, `dashboard/components/TrendLevelsTable.tsx`, `dashboard/components/AllPointsTable.tsx`, `dashboard/components/SignalsPanel.tsx`
**Key features**:
- Symbol switcher (top nav): selected symbol pinned left, others scroll horizontally
- Level filter: L1/L2/L3 ceiling button (L2 = show L1+L2, etc.)
- Date range picker: From/To datetime-local, resets level filter to max if out of range
- Chart: 4 price lines (close, open, high, low) + trend line + swing points colored by level
- Swing points: full color for active, gray for inactive (historical context)
- Trend table: one row per level showing direction, last swing type, next projected extremum
- Signals table: active recommendations only with full signal metadata

### Backtest Page
Results viewer (`/backtest`). Sortable preset summary table with drill-down to per-preset trade list.

**Files**: `dashboard/app/backtest/page.tsx`, `dashboard/components/BacktestSummaryTable.tsx`, `dashboard/components/BacktestTradeList.tsx`
**Key features**:
- Summary table: profit%, win rate, max DD, trade count, avg R:R per preset
- Sortable by any column (default: profit% desc)
- Click row to expand trade list for that preset
- Trade list: color-coded result (green=win, amber=partial/trail, red=loss)
- Trade drill-down: entry price, TP, SL, filled price, PnL, signal type, level
- Run Backtest button: kline count input (default cached) + spawn subprocess + show progress spinner
- Locked presets: lock/unlock toggles + delete buttons + visual indicator

### Trades Page
Virtual order tracking (`/trades`). Real and virtual preset efficiency, performance chart with trade markers, recent order table, live open positions.

**Files**: `dashboard/app/trades/page.tsx`, `dashboard/components/PresetEfficiencyTable.tsx`, `dashboard/app/api/trades/route.ts`, `dashboard/app/api/trades/symbols/route.ts`
**Key features**:
- Live open positions: real orders shown with green LIVE badge, virtual orders with blue LIVE badge, listed before closed orders
- Position count in section header: "Open Positions (N)"
- Preset efficiency table: preset name, rank badge (★ Real / #2–#6 / —), trade count, total PnL%, balance
- Hide virtual-only checkbox: filters out presets with 0 real+virtual trades (seeded from backtest, not yet executed)
- Candlestick chart with trade entry/exit markers (▲ BUY, ▼ SELL)
- Recent real orders table (most recent first): symbol, preset, side, entry/exit price, PnL, status, qty
- Qty column: smart decimal formatting (2 decimals for normal ranges, scientific notation for micro-qty)

### Risk Page
Risk management controls (`/risk`). Config editor, real-time state polling, drawdown guard, leverage info.

**Files**: `dashboard/app/risk/page.tsx`, `dashboard/app/api/risk/route.ts`
**Key sections**:
- **A – Global Capital Rules**: balance tiers (min balance, max deploy %, lever ceiling)
- **B – Per-Symbol Allocation**: symbol weights (if weighting enabled)
- **C – Leverage Controls**: base leverage, max global leverage, max progression level, allocation weighting toggle
- **D – Drawdown Guard**: warning %, hard-stop %, current drawdown, peak balance, reset button
- **E – Live State**: current balance, leverage in use, capital deployed, drawdown status, polling updates every 5s

### Settings Page
Configuration and administration (`/settings`). Add/remove symbols, Telegram alerts, start/stop bot, mode switch, discovery.

**Files**: `dashboard/app/settings/page.tsx`, `dashboard/components/SymbolDiscovery.tsx`
**Key features**:
- **Symbol registry**: add symbol input, remove buttons, toggle disable per symbol
- **Telegram alerts**: token/chat_id inputs, test button, sample messages
- **Bot control**: Start/Stop Bot buttons, trading mode selector (test / live)
- **Symbol discovery**: async subprocess to find high-profit symbols; progress bar, sortable candidates table
- **UI Preview**: theme switcher, example component showcase

### Alerts & Logging
Real-time alerts and system log viewer.

**Files**: `dashboard/app/log/page.tsx`, `dashboard/components/AlertBanner.tsx`
**Key features**:
- Alert banner above nav: shows undismissed warnings/emergencies, dismissible
- Log page: system log with level filter (all/info/warning/error), reversed chronological order
- Entries include: timestamp, level, title, body, source module
- Auto-dismiss read alerts when page viewed

### Navigation & State
Top nav bar with symbol switcher, mode badge, alert count, links to Strategy / Backtest / Trades / Risk / Settings / Log.

**Files**: `dashboard/components/NavBar.tsx`, `dashboard/components/ModeBadge.tsx`, `dashboard/components/SymbolSwitcher.tsx`

---

## Learning Mode (dashboard)
Step through historical candles one at a time, accept/reject the bot's signal, place
manual paper orders, and attach free-text notes. On "Stop & Save" the session is
downloaded as JSON for Claude to analyse into `docs/learning/hypotheses.md`.

**Files**: `dashboard/lib/useLearningSession.ts`, `dashboard/lib/learningTypes.ts`,
`dashboard/components/LearningStartModal.tsx`,
`dashboard/components/LearningRecommendationPanel.tsx` (includes the custom-order
form — the spec's separate `LearningOrderForm.tsx` was folded in here),
`dashboard/components/LearningNoteOverlay.tsx`, `dashboard/app/page.tsx`,
`dashboard/components/SwingPointsChart.tsx`, `dashboard/components/TimeScrubber.tsx`

**Key details**:
- Six event types are recorded: `candle_advanced`, `signal_accepted`,
  `signal_rejected`, `custom_order_placed`, `order_closed`, `note_added`.
- **Notes (session 63)** — two independent kinds, both meant for later analysis:
  - *Order rationale*: an optional Note field in the Place-custom-order form, stored
    as `LearningOrder.note` and carried on the `custom_order_placed` event.
  - *Standalone candle note*: recordable two ways, both emitting the same
    `note_added` event against the current candle with or without any order on it —
    (a) the sticky "+ Note" button, whose overlay shows the candle's timestamp, index
    and OHLC so it is unambiguous which bar is being annotated, and (b) a
    **Place Custom Note** button sitting next to Place Custom Order in the panel, so a
    "no trade, and here's why" can be captured without leaving the decision controls.
    Both call the same `addNote`, so a session is uniform regardless of entry point.
- **Session log table (session 63)** — a collapsible table at the bottom of the
  Bot Recommendation panel, under the Place Custom Order / Place Custom Note buttons.
  Collapsed by default; the open/closed preference persists in localStorage
  (`db:learning:logOpen`), matching the page's other sections. The header shows the
  row count so the size is visible while collapsed.
  Columns: candle time · type (BUY/SELL/NOTE) · detail · note. Order rows show
  `entry tp sl · outcome ±pnl%` with the outcome colour-coded, reading `open` until
  the order ends. Entry and exit rationales share the note column, the exit one
  prefixed `exit:`.
  Built from the **event stream**, not the orders array, so notes and orders
  interleave chronologically and `order_closed` events fold onto their originating
  order row rather than appearing as separate lines — a closed trade is one row
  showing both why it was entered and why it ended.
  Rendered in all four panel states, so the log stays reachable while the reject,
  note or custom-order form is open.
  - Both event types now embed a `CandleContext` (`{time, open, high, low, close}`).
    Previously a note carried only `candle_index`, which cannot be interpreted later
    without also having the exact kline file the session was recorded against — the
    events are now self-describing.
- Orders are evaluated against each new candle's high/low; TP/SL crossings close the
  order and emit `order_closed` with `tp_hit`/`sl_hit`, `close_price` and `pnl_pct`.
- **Manual close with a note (session 63)** — the sticky panel lists every still-open
  order (side, entry, TP, SL) with a **Close** button. Closing captures an optional
  rationale and emits `order_closed` with `market_outcome: 'manual_close'`, the close
  price, `pnl_pct` and the note. This covers "I would have got out here" for reasons
  the TP/SL geometry cannot express. The list renders above every panel state, so a
  position can be closed even while the custom-order or reject form is open.
  Manual closes settle at the current candle's **close** price.
- Open orders draw TP (green) / SL (red) zones on the chart; closed orders stay
  visible faded.
- Frontend only — uses the existing `/api/replay`; no bot changes.
- **Pick TP/SL/Entry off the chart** (session 63). With the custom-order form open,
  focusing a price field arms price capture: the chart shows a crosshair cursor and
  clicking it fills that field with the price under the pointer. The focused field is
  outlined amber. Works on both the line and candlestick views.
  Chart.js reports clicks in canvas pixels, so `scales.y.getValueForPixel()` converts
  to a price (`makePriceClickHandler` in `SwingPointsChart.tsx`).
  `priceToInputValue` (`lib/formatPrice.ts`) formats it for a number input — precision
  scales with magnitude (8dp under 0.01 → 1dp above 10,000) and never groups digits,
  so `Number()` can parse it back. Blur deliberately does NOT clear the target, since
  clicking the chart blurs the input; the target clears on submit or cancel.
  The page reaches the panel through a `useImperativeHandle` (`LearningPanelHandle`)
  rather than a prop+effect — pushing a value down as a prop and applying it in an
  effect triggers cascading renders (`react-hooks/set-state-in-effect`).
- **Start is chosen by date & time, not candle index** (session 63). The modal shows a
  `datetime-local` picker snapped to 15-minute boundaries, echoes back which candle it
  resolves to and how many remain, and shows the available data range. Mapping lives in
  `dashboard/lib/datetime.ts` (`datetimeLocalToCandleIndex`) — binary search for the
  last candle opening at or before the chosen time, clamping outside the range. The
  resume prompt shows the session's start timestamp rather than its index. Users have
  no way to know candle indexes, so the picker is the only usable entry point.
- **Session 63 fix**: the order panel was rendered *below* the Swing Points chart, so
  it sat off-screen while the sticky note button stayed visible — order placement
  looked unimplemented. It is now rendered directly under the candle scrubber and is
  `sticky top-2`, so Accept / Reject / Place Custom stays in view while stepping.

## Telegram Interactive Menu

### Three-Tier Access Control
Users interact with a Telegram bot via button-driven menu (no text commands required beyond first message). Access levels: owner (full menu + write actions), viewer (read-only, added by owner), unknown (blocked until owner approves via Telegram).

**Files**: `bot/telegram_menu.py`, `bot/telegram_views.py`
**Key details**:
- Menu is button-driven; users navigate by tapping inline keyboards
- Owner can approve/deny access requests; viewers persistent in `data/telegram_viewers.json` (atomic write)
- Pending requests stored in memory only (cleared on bot restart)
- Write actions (pause symbol, enable symbol, reset hard stop, manage viewers) available to owner only; read-only menu for viewers

### Available Screens
- **Status**: mode (test/live), balance, symbol counts (active/disabled/paused), hard stop state, uptime, last candle time
- **Symbols**: list with active/disabled/paused state, tap any symbol for detail
- **Trades → Real Orders**: open positions and recent closed trade history
- **Trades → Virtual Orders**: per-symbol rank 2–6 open positions and recent closed history
- **Backtest**: per-symbol top-5 best presets by profit %
- **Controls** (owner only): reset hard stop, resume paused symbols, manage viewer access requests/list

### Write Actions (Owner Only)
- Pause symbol (blocks new orders on next candle; open positions unaffected)
- Resume paused symbol
- Enable disabled symbol
- Reset hard stop
- Approve/deny viewer access requests
- Revoke existing viewer

### Polling & Message Rendering
- Async long-polling with 30s timeout via `asyncio.to_thread(requests.get, ...)` — no new dependencies
- All user-supplied and bot-internal strings HTML-escaped before Telegram HTML parse mode
- Viewer management persistent in `data/telegram_viewers.json`; requests are in-memory only

### Symbol Pause (Distinct from Disable)
New `SymbolRegistry` methods: `pause_symbol()`, `resume_symbol()`, `is_symbol_paused()`, `get_paused_symbols()`. Pause does NOT redistribute weights (unlike disable); paused symbols skipped in real order candidate loop, but open positions remain unaffected.

---

## Bot Runtime & Operations

### Mode Management
Switches between test (testnet) and live (real) at runtime via command channel. Wipes positions on switch, reruns backtest gate, prompts acceptance of new mode before orders resume.

**Files**: `bot/mode_manager.py`, `main.py`
**Key details**:
- Mode file: `data/bot_mode.json` (current mode state)
- Command channel: `data/bot_command.json` (dashboard → bot), `data/bot_command_result.json` (bot → dashboard)
- Backtest gate: every mode switch runs `backtest.py --mode {new_mode}` before accepting
- 2s polling interval for commands

### Graceful SIGTERM Shutdown (Session 34 enhanced)
Bot handles SIGTERM signal (from `docker stop` or deployment scripts) by closing all open positions gracefully before exit. Wired via `signal.signal(signal.SIGTERM, on_stop_bot)` handler.

**Files**: `main.py` (on_stop_bot), `bot/virtual_order_simulator.py` (get_open_positions), `docker-compose.yml`
**Key details**:
- **Docker grace period** (session 34): `stop_grace_period: 60s` on bot service — increased from default 10s to allow sufficient time for API calls to complete
- **Docker PID 1** (session 34): `exec` prefix on command ensures Python is PID 1 and receives SIGTERM directly (not wrapped by shell)
- **API timeout protection** (session 34): `on_stop_bot()` wraps `close_all_open` + `close_all_orders_at_market` in `asyncio.wait_for(timeout=45s)` to prevent hanging indefinitely if exchange API becomes unresponsive
- On SIGTERM: `on_stop_bot()` triggers, which calls `OrderExecutor.market_close_all()` + `VirtualOrderSimulator.on_stop_bot()`
- All open positions closed at market price and recorded to `data/real_orders_{symbol}_{mode}.json`
- Virtual positions written to final JSON file via `_write_open_positions()`
- Bot then exits cleanly after pending order closes complete (within 45s window)
- **Prevents orphan positions** when bot restarts during deploy; graceful shutdown ensures all exchange orders are cancelled/closed before process exits

### Dashboard auto-login on localhost (session 63)
Local development skips the login screen. Implemented in `dashboard/proxy.ts`
(Next 16 renamed Middleware → Proxy; the file must export `proxy` + `config`).

**Key details**:
- Bypass requires BOTH a loopback hostname (`localhost`, `127.0.0.1`, `::1`) **and**
  a non-production build (`NODE_ENV !== 'production'`).
- **Why both**: `req.nextUrl.hostname` derives from the `Host` header, which any
  client can set. A hostname-only check would let anyone bypass auth on the public
  dashboard (185.237.14.105:3000) by sending `Host: localhost`. The NODE_ENV gate is
  what actually makes it safe — the server runs `next start` (production), so the
  bypass can never engage there regardless of headers.
- `DASHBOARD_LOCAL_NO_AUTH=1` forces the bypass for a *local* production build.
  Never set this in the server `.env`.
- Verified: dev+localhost → 200; production+localhost → 307 to `/login`;
  production+opt-in → 200.

### Notifier & Telegram Alerts
Sends alerts to Telegram (token/chat_id from config). Routes warnings/emergencies to alert state file. Implements cooldown to avoid spam.

**Files**: `bot/notifier.py`, `bot/order_executor.py` (session 42 updates)
**Key details**:
- Alert levels: warning (yellow), emergency (red, re-alert every 30 min by default)
- Logged to system log + alert state file + Telegram (if configured)
- Test notification support for setup verification
- Sample messages built-in for trade wins/losses/balance warnings
- Never raises exceptions (silent failures logged)
- Cooldowns: `emergency_repeat_interval_s`, `warning_repeat_interval_s` per config
- **Session 42 fixes**:
  - **Shutdown closes now notify** — `close_all_orders_at_market()` now calls `notify_trade_close()` after closing each position
  - **Per-symbol rate limit** — Changed from shared `"trade"` key to per-symbol keys `f"trade:{symbol}"` so simultaneous closes on different symbols all send independent Telegram notifications (was: only first close notified, others suppressed by 120s cooldown)

#### Trade closes are never silently dropped (session 63)
`notify_trade_close` no longer uses the generic per-symbol time throttle. It dedups
on **message content** instead (`_trade_content_ok`, 300s window):

- Two *distinct* closes on the same symbol seconds apart **both send**.
- Only a byte-identical repeat (a resend bug) is suppressed, and that is logged.
- The `system_log` entry is written *before* any send decision, so the log is
  complete even when Telegram is suppressed or unreachable.

**Why**: the old `_rate_limit_ok(f"trade:{symbol}")` 120s throttle could swallow a
genuine second close on one symbol. From the user's side that is indistinguishable
from the bot hiding a losing trade — which is exactly the suspicion that opened
session 62. The generic time throttle still applies to ordinary warnings.

**Files**: `bot/notifier.py`; tests in `tests/test_notifier.py`.

#### Trade-close message: Before / Net / Fee / After (session 62)
Every real trade-close notification reports the wallet on both sides of the trade,
the net result, and the commission paid:

```
✅ INJUSDT BUY — Win [Real]
Before: 3,050.18 USDT
Net PnL: +18.03 USDT (net of fee)
Fee: 1.2163 USDT
After: 3,068.06 USDT
Entry: 4.0820 → Close: 4.1340
Preset: oscillating_zone
```

**Files**: `bot/notifier.py` (`notify_trade_close`, `_fmt_balance`), `main.py`
(`_read_wallet_now`, both close-notify loops), `bot/order_executor.py`
(`OpenOrder.wallet_at_open`)

**Key details**:
- `Before` — `OpenOrder.wallet_at_open`: an uncached USDT wallet read taken in
  `_try_place_order` immediately before submission. Distinct from
  `balance_at_open`, which is the allocated per-symbol trade cap (a much smaller
  number — ~296 vs ~3050 USDT).
- `After` — uncached wallet read taken after the close settles, via
  `_read_wallet_now()`.
- `Fee` — actual taker commission both legs, `_order_fee()` at 0.04%.
- `Net PnL` is already net of that fee. It does **not** include funding fees, so
  `Before + Net` can differ from `After` by the funding charged during the hold
  (typically ~0.15 USDT on a 1,500 USDT notional held over a funding window).
- **Unavailable balances print `n/a (balance fetch failed)`** — never a
  substituted or last-known figure. `0.0` is the "read failed" sentinel that
  `fetch_account_balance()` already returns on error.
- `_read_wallet_now()` bypasses the `_BALANCE_TTL` cache entirely and returns
  `0.0` rather than a cached value. `_get_fresh_balance()` keeps its stale-cache
  fallback because sizing needs *a* number; it must not be used for reporting.

**Bug this replaced** (2026-08-19): the old single `Balance:` line was fed from
`_get_fresh_balance()`, whose 5-second TTL cache had been populated by the
placement pass at the top of `on_candle_close`. Closes detected ~2s later in the
same handler therefore reported the balance from *before* the close settled.
Trades closing via the price-tick path were unaffected, so 2 of 4 messages on
Aug 19 were correct and 2 were not — the last one understated the wallet by
49 USDT. A concurrent `-1003` IP ban made the final message stale a second way,
via the cache fallback on fetch failure.

### Market Condition Error Handling
Transient API errors (Binance -4131 PERCENT_PRICE filter violations) are caught as `MarketConditionError` and do not trigger order retry loops or auto-disable. Order is silently deferred and retried on next candle.

**Files**: `bot/order_executor.py`, `main.py`
**Key details**:
- Error -4131: "Price is not good with respect to %PRICE filter" — occurs during fast market moves before order executes
- Handled as transient: exception is not re-raised; bot continues to next candle
- Candidate filtering skips price-checking logic to allow retry
- No symbol auto-disable, no cascading failures

### System Logging
Rolling 100-entry JSON log. Atomic writes via tmp→rename. All events (orders, errors, alerts, leverage advances) logged with timestamp, level, title, body.

**Files**: `bot/system_log.py`
**Key details**:
- File: `dashboard/public/system_log.json`
- Max 100 entries (oldest trimmed on overflow)
- Levels: info, warning, error
- Atomic write: prevents partial/corrupt entries
- Sourced: module name that generated the entry

### Decision Log
Append-only log of every order-placement decision (placed or skipped). Used for post-run analysis: "Which valid signals were skipped due to capital limits?"

**Files**: `bot/decision_log.py`
**Key details**:
- File: `data/decision_log_{MODE}.json`
- Max 5000 entries (oldest trimmed)
- Per-entry: timestamp, symbol, candle_ts, decision type, reason, balance, leverage, efficiency_score, preset_name, signal_type, precision_score, level
- Decision types: placed / skip_balance / skip_profit_factor / skip_hard_stop / skip_already_open / skip_no_signal / skip_duplicate_sl
- **Temp file safety** (session 28): Uses PID-qualified filename (`{stem}.{os.getpid()}.tmp`) to prevent concurrent process race conditions during atomic write

### Bot State & Heartbeat
Bot writes its PID and state to `bot_state.json` on startup and updates it every 10s via heartbeat task.

**Files**: `main.py`
**Key details**:
- State file: `dashboard/public/bot_state.json`
- Fields: running (bool), pid, mode (test/live), started_at, last_heartbeat, symbols_active, symbols_disabled, phase (starting/running)
- Dashboard polls every 10s to detect bot crashes or hangs

### Obligatory Backtest Gate
Every bot startup and every mode switch runs `python backtest.py --mode {mode}` as a subprocess. If it fails, bot exits (startup) or aborts the switch (mode change) with an emergency alert.

**Files**: `main.py` (init and mode switch)
**Key details**:
- Startup: blocking `subprocess.run()` before event loop starts
- Mode switch: non-blocking `asyncio.to_thread(subprocess.run, ...)` during runtime
- Failure handling: emergency alert + log entry

---

## Ancillary Components

### Symbol Discovery
Subprocess finds profitable trading symbols by running backtest on candidates. Async thread pool for speed.

**Files**: `bot/symbol_discovery.py`, `discover.py`, `dashboard/app/api/discovery/run/route.ts`, `dashboard/app/api/discovery/cancel/route.ts`, `dashboard/components/SymbolDiscovery.tsx`
**Key details**:
- Pre-candidates: user-supplied list (typically top 100 by volume)
- Fast presets: 3–5 parameter sets optimized for quick filtering
- Baseline: best fast preset result on fastest timeframe (5m)
- Scoring: profit%, win rate, max DD weighted together
- Output: `discovery_candidates.json` with scores, sorted by efficiency
- UI: progress bar, cancel button, sortable candidates table, "Add" per row

### Leverage Scenario (Pluggable)
Strategy for how leverage increases over time. Protocol allows different implementations. Currently only DefaultScenario.

**Files**: `bot/leverage_scenario.py` (base + DefaultScenario), `bot/leverage_tracker.py`
**Key details**:
- `LeverageScenario` protocol: name, level tracking, advance logic
- DefaultScenario: all symbols must complete level N before advance
- `LeverageTracker`: reads/writes persistent level state, dispatches advance events

### Exporter (Results JSON)
Converts live Analyzer state to dashboard-compatible JSON. Exports klines, trend levels, swing points (active + inactive), signals, mode, timestamp.

**Files**: `bot/exporter.py`
**Key details**:
- Output: `dashboard/public/results_{SYMBOL}.json`
- Called: on bot startup and every candle close
- Max 1000 klines exported (reaches back to oldest active swing point)
- Fallback: if Analyzer has no points, traverses live trend
- For newly-added symbols: placeholder export with live price

### Backtest API for Visualization (Session 30 updated)
Dashboard "Visualize preset" feature runs backtest_api.py to simulate a preset on current klines and show results. Settings object correctly instantiated with all required fields including early-exit controls.

**Files**: `backtest_api.py`, `dashboard/app/api/preset-visualization/route.ts`
**Key details**:
- Accepts preset_name and symbol parameters
- Returns summary: win%, max DD, trade count, profit%
- DEFAULTS dict includes all Settings fields: core (tp_multiplier, trailing_stop_pct, etc.) + early exit (`max_losing_pct`, `max_losing_amount_usdt`, `max_losing_candles`)
- Settings() constructor call passes all three early-exit fields with safe defaults (0.0, 0.0, 0)
- Fix in session 30: added missing early-exit fields that were added in session 29

---

## Testing & Configuration

### Test Suite
Unit tests for modules: RiskManager (21 tests), SymbolDiscovery (10 tests), Notifier (16 tests), VirtualTracker, and more. Uses pytest, mocking for file I/O and exchange APIs.

**Files**: `tests/test_*.py`
**Key details**:
- Filesystem isolation: tmpdir fixtures, patchable paths
- Mock Binance API responses for exchange tests
- Mock file I/O for system-log and alert tests

### Environment & Settings
All parameters loaded from `.env` and `config/settings.py`. Supports per-symbol overrides (e.g., `BTCUSDT_TIMEFRAME=1h`). Validates on startup; fails fast with clear error if missing.

**Files**: `.env` (gitignored, example in `.env.example`), `config/settings.py`
**Key env vars**:
- `TRADING_MODE` = test | live
- `SYMBOL` = primary symbol (fallback if `symbol_registry.json` missing)
- `SYMBOLS` = comma-separated list (seed registry on first startup)
- `TIMEFRAME` = 15m (default), per-symbol override via `{SYMBOL}_TIMEFRAME`
- API keys: `TESTNET_API_KEY` / `TESTNET_API_SECRET`, `LIVE_API_KEY` / `LIVE_API_SECRET`
- Risk thresholds, telegram config, etc. (see Settings dataclass)

---

## Deployment & Infrastructure

### Critical Docker Build Requirement (Session 51)
**IMPORTANT: Python source code is NOT volume-mounted. Code changes MUST rebuild the Docker image.**

**Files**: `docker-compose.yml`, `Dockerfile`
**Key details**:
- Python source (`main.py`, `bot/`, `config/`, `backtest.py`, etc.) is baked into Docker image at build time
- Only mounted volumes: `data/`, `logs/`, `risk_config.json`, `symbol_registry.json`, `dashboard/public/`
- **Incorrect deploy** (runs stale bytecode): `git pull` + `docker stop bot` → auto-restart uses old image
- **Correct deploy** (required for any code changes):
  ```bash
  cd /opt/bot
  git pull origin <branch>
  docker compose build bot       # ← MANDATORY
  docker compose up -d --no-deps bot
  ```
- Without `docker compose build bot`, the container runs old compiled Python bytecode from the previous image build
- **Impact if skipped**: Code changes on server disk are invisible to running container; bot executes old logic

### File-Based Command Channel
Dashboard writes commands to `data/bot_command.json` (with UUID). Bot polls every 2s, executes, writes result to `data/bot_command_result.json` with matching UUID. Dashboard polls for result. Enables graceful mode switches, stop signals, telegram tests without signal handling complexity.

**Files**: `bot/mode_manager.py`, dashboard API routes
**Key details**:
- Commands: switch_mode, stop_bot, test_telegram
- 60–120s timeout per command (with SIGTERM fallback)

### Docker Deployment — Separate Bot & Dashboard Services (Session 30)
Dockerfile and docker-compose.yml in repo root. Bot and dashboard are now independent services (`bot` and `dashboard` containers) so dashboard can be redeployed without stopping the bot. Both services mount shared host volumes for file-based communication.

**Files**: `Dockerfile`, `docker-compose.yml`, `scripts/push.sh`, `scripts/push_dashboard.sh`
**Key details**:
- **Bot service** (`bot` container): Python 3.12 runtime, command `cd /app && .venv/bin/python3 main.py`, restart: unless-stopped, no exposed ports
- **Dashboard service** (`dashboard` container): Node.js runtime (Next.js), command `cd /app/dashboard && next start -p 3000`, port 3000, restart: unless-stopped
- Both mount same volumes: `./data`, `./logs`, `./dashboard/public`, `./risk_config.json`, `./symbol_registry.json`
- **Full deploy** (`bash scripts/push.sh`): git pull, rebuild both images, graceful bot stop before pull, restart both services
- **Dashboard-only deploy** (`bash scripts/push_dashboard.sh`): git pull, rebuild dashboard only (`--no-deps`), bot never stopped — trading continues uninterrupted
- **Bot spawn disabled**: `/api/bot/start` route now returns HTTP 503; bot auto-starts via Docker restart policy
- **Mode command simplified**: `/api/mode` route directly writes `bot_mode.json` without cross-container PID checks

### Graceful Deployment Process
Push.sh implements graceful shutdown before rebuild:
1. Send SIGTERM to bot process (gracefully closes all open positions)
2. Wait up to 60s for bot to close positions and exit
3. `docker stop bot` to ensure container stops (prevents restart race condition during git pull)
4. Git pull and rebuild
5. `docker compose up -d` restarts both services

### Monitoring & Logging
Rotating logs with 10 MB cap per file, 5 backups kept. Separate log files: `logs/bot.log` (general), `logs/trades.log` (order events only).

**Files**: `main.py` (setup_logging), logs produced by all modules
**Key details**:
- General log: all modules (data fetch, errors, lifecycle events)
- Trades log: order open/close only (quick reference for performance review)
- System log (JSON): summary of key events (dashboard visible)

### STOP File Emergency Halt
If `data/STOP` file exists, main loop checks and halts gracefully.

**Files**: `main.py` (checks `_STOP_PATH` every iteration)
**Key details**:
- Create file via SSH: `touch /opt/bot/data/STOP`
- Bot detects on next candle close, closes all positions, exits
- Graceful shutdown: respects pending order closes

---

## Key Dependencies & Versions

- **Python**: 3.10+
- **Binance**: `python-binance` (REST + WebSocket fixture)
- **Async**: `websockets` (WebSocket streams), `asyncio` (main event loop)
- **Storage**: JSON files (all state), gitignored `data/` directory
- **Dashboard**: Next.js 15, TypeScript, Tailwind v4, Chart.js, react-chartjs-2
- **Deployment**: Docker + docker-compose, systemd service file (ready, not yet integrated)

---

## Known Limitations & Design Notes

1. **Partial take**: architecture ready, requires exchange-side trailing order (not implemented).
2. **Allocation weighting**: archived feature (disabled by default); can re-enable via Settings.
3. **Multiple bot instances**: only one instance per mode should run (race condition on kline cache + results.json writes).
4. **Testnet artifacts**: testnet price spikes form valid-looking swing points (realistic, not a bug).
5. **Order sizing**: currently uses minimum notional margin; no complex position pyramid or Kelly criterion.
6. **Live mode**: requires explicit creation of live API keys with futures-only and no-withdrawal permissions.

---

### Symbol Precision Fix (Order Executor)
Corrects `-1111` "Precision is over maximum" errors on coarser-precision symbols (TIAUSDT, DOGEUSDT, 1000PEPEUSDT, etc.). Previously, lot cache fallback used float `tick_size=0.00001` which produced wrong decimal counts when converted to string.

**Files**: `bot/order_executor.py`
**Key details**:
- `tick_size` stored as original string from Binance (e.g., `"0.001"`)
- New static method `_price_str(price: float, tick_size: str) -> str` formats SL price to exact decimal count
- Normal operation: per-symbol string from Binance exchange info (always correct)
- Fallback: `"0.00001"` string default (only mismatches coarser symbols, same behavior as before but now string-typed)

### Login Authentication Fix (Dashboard)
Fixes auth cookie not being sent on first request after login. Previously used `router.push(next)` (client-side nav only).

**Files**: `dashboard/app/login/page.tsx`
**Key details**:
- Changed to `window.location.replace(next)` for full page reload
- Guarantees new cookie included in server request

### Symbol Status Indicator Dots (Dashboard — Session 29 updated)
Visual indicators on SymbolSwitcher showing symbol health status. Red = disabled (precision/other error), Green = has live orders. Both indicators can co-exist on same symbol.

**Files**: `dashboard/components/SymbolSwitcher.tsx`, `dashboard/components/ClientLayout.tsx`, `dashboard/components/SymbolContext.tsx`, `dashboard/app/api/open-positions/route.ts`
**Key details**:
- Red dot: symbol auto-disabled (e.g., precision error → `-1111` consecutive failures)
- Green dot: symbol has live open positions (real or virtual)
- Disabled list fetched from `/api/symbols` every 30s
- Live orders source (Session 29): `/api/open-positions` (reads `data/bot_mode.json` + `data/open_positions_{mode}.json`; returns `{ symbols: string[] }` of symbols with open orders; empty list if bot not running)

### Chart DateTime Range Pickers (Dashboard Trades Page)
Filter chart klines by date range. Two `datetime-local` inputs with in-memory filtering (no re-fetch) and reset button.

**Files**: `dashboard/app/trades/page.tsx`
**Key details**:
- From / To pickers on chart widget
- Filters already-loaded klines array in memory
- Shows "N of M candles" when filtered
- Reset button clears both pickers
- Charts update immediately without server round-trip

### Strategy Page Time Travel (Session 39)
Replay bot's trend analysis state at any historical candle index. Users can scrub backward through the strategy timeline to see what signals were active at past moments, including swing points, trend levels, and indicators at that candle.

**Files**: `replay_api.py`, `tests/test_replay_api.py`, `dashboard/app/api/replay/route.ts`, `dashboard/components/TimeScrubber.tsx`, `dashboard/lib/types.ts`, `dashboard/app/page.tsx`

**Key details**:
- **`replay_api.py`**: Python script that re-runs `Analyzer.build_from_klines(klines[:idx+1])` on stored results JSON. Returns `{trend_levels, all_points, signals}` for historical moment. Symbol validation (regex), negative index guard, 10s CLI usage.
- **`tests/test_replay_api.py`**: 6 pytest tests covering symbol validation, negative index guard, boundary cases.
- **`dashboard/app/api/replay/route.ts`**: POST route validates `{symbol, candle_index}`, spawns `replay_api.py`, 10-second subprocess timeout guard.
- **`dashboard/components/TimeScrubber.tsx`**: React component with `input[type=range]` slider, ◀ ▶ tick buttons (±10 klines), LIVE badge (green pulsing dot) when at live position, datetime label when historical, "updating…" while loading.
- **Travel range**: Limited to klines in `results_{symbol}.json` (up to 1000 candles, ~10 days at 15-min).
- **Swing neighbours**: Hardcoded `swing_neighbours=2` in replay script (matches live analyzer default).
- **Overlay sync**: Klines AND overlays (swing points, trend levels, signals) all clip together — no visual mismatch.
- **Live polling**: Continues in background during replay; returning slider to max position instantly resumes live view.
- **Clear on load**: `replayData` cleared immediately on each scrub position so stale overlay data never persists during loading.

### New feature: global_blocked_signal_types filter (Session 52)
- **What**: Blocks specific signal type strings globally before any scoring. Set `global_blocked_signal_types: ["type_name"]` in risk_config.json.
- **Files**: `bot/recommendation_engine.py` → `_score_and_filter()` reads `cfg.get('global_blocked_signal_types', [])`, checks `rec.getType().value in g_blocked_signals`
- **Config**: `global_blocked_signal_types` key in risk_config.json (array of strings). Default: empty (disabled). Currently set to `["lowering_near_last_low"]`
- **No redeploy needed** to change the blocked list — edit risk_config.json on server directly
- **Deployed**: commit d5d4fee, 2026-06-14

### New feature: global_max_level filter (Session 52)  
- **What**: Blocks signals from trend levels deeper than N. Level = depth in trend chain (root trend = L1, its parent = L2, grandparent = L3). L3 signals have 73.4% historical loss rate.
- **Files**: `bot/recommendation_engine.py` → `_score_and_filter()` reads `cfg.get('global_max_level', 0)`, checks `rec.getLevel() > g_max_level`
- **Config**: `global_max_level` key in risk_config.json (integer). 0 = disabled. Currently set to `2` (blocks L3+)
- **No redeploy needed** — edit risk_config.json on server
- **Deployed**: commit d5d4fee, 2026-06-14

### Updated feature: max_loss_usdt (phantom SL) (Session 52)
- **What it does**: When non-zero, places a hard dollar-capped exit SL at `entry ± cap/quantity` at order open. At high quantities (e.g. TIAUSDT qty~5900), even $25 cap produces SL at 0.42% from entry — overrides all structural SLs.
- **Current state**: Disabled (`max_loss_usdt: 0`). Only use if position quantities are well understood.
- **Files**: `bot/virtual_order_simulator.py` lines 251–268, `bot/order_executor.py` lines 217–252
- **Warning**: Never re-enable with a global cap without checking typical quantities per symbol first.

### Global RR & SL Filters (Session 47)
Hard global filters on all signals across all symbols and presets. Applied after signal generation but before scoring, ensuring no signal can bypass fundamental risk/reward constraints.

**Files**: `bot/recommendation_engine.py`, `config/settings.py`, `config/risk_config.py`
**Key settings**:
- `global_min_rr: float` (default 0.0 = disabled) — blocks all signals with RR < threshold (e.g., 3.0 = minimum 3:1 risk/reward)
- `global_max_rr: float` (default 0.0 = disabled) — clips TP to enforce max RR = threshold (e.g., 4.0 = cap at 4:1)
- `global_min_sl_pct: float` (default 0.0 = disabled) — floors SL to N% of entry (e.g., 0.5 = minimum 0.5% stop)
- `global_trend_regime_filter: bool` (default False) — blocks BUY in descending regime, SELL in ascending regime
- `global_trend_regime_lookback: int` (default 3) — number of consecutive H/L pairs to check for regime confirmation

**Implementation**:
- In `recommendation_engine.py::_score_and_filter()`: after generating signal and RR, check all global gates BEFORE scoring
- RR floor gate: skip if `rr < global_min_rr`
- RR ceiling gate: if `rr > global_max_rr`, recompute TP to achieve exact max RR using `eff_loss_dist` (floored SL distance)
- SL floor gate: computed with `eff_loss_dist = max(loss_dist, entry × global_min_sl_pct/100)` — ensures engine and main.py agree on effective SL
- SELL adjustment: SELL SL floor uses `global_min_sl_pct / 1.5` as minimum (account for ×1.5 spike adjustment in main.py)
- Regime gate: calls `getTrendRegime(lookback)`, skips BUY if descending, SELL if ascending

**Session 47 critical fix**: SL floor was being computed AFTER max_rr clipping, causing RR mismatches. Now computed BEFORE all RR calculations. Also fixed SELL SL floor: engine was using 0.5% but main.py uses 0.333%, causing invalid signals. Engine now uses 0.5%/1.5 for SELL to match.

### Signal Direction Gate (Hard Directional Filter)
Restricts signal generation to buy-only, sell-only, or both directions via hard gate. When `signal_direction != 'both'`, all recommendations for the opposite side are discarded before scoring.

**Files**: `config/settings.py`, `bot/recommendation_engine.py`, `config/presets.py`
**Key setting**:
- `signal_direction: str` (default 'both') — 'buy', 'sell', or 'both'

**Implementation**:
- In `recommendation_engine.py::_score_and_filter()`: after generating all recommendations, filter to only those matching `signal_direction` before scoring
- Presets can hard-lock to directional trading (e.g., `l2_trend_sell` locks to SELL, `l2_trend_buy` locks to BUY)

### Trend Regime Detection & Filter
Dynamically detects structural market regime (ascending/descending/neutral) per candle and blocks contra-trend signals. When enabled, checks if the last N consecutive highs (or lows) form a series. Blocks BUY signals in confirmed downtrends and SELL signals in confirmed uptrends.

**Files**: `config/settings.py`, `bot/trend.py`, `bot/recommendation_engine.py`, `config/presets.py`
**Key settings**:
- `trend_regime_filter: bool` (default False = disabled) — enable per-candle regime detection
- `trend_regime_lookback: int` (default 3) — number of consecutive H/L pairs to check for regime confirmation

**Implementation**:
- `bot/trend.py::getTrendRegime(lookback: int) -> str`: Examines last `lookback` swing highs and lows. Returns:
  - `'descending'` if all recent highs are lower AND all recent lows are lower
  - `'ascending'` if all recent highs are higher AND all recent lows are higher
  - `'neutral'` if mixed or insufficient data
- In `recommendation_engine.py::_score_and_filter()`: if `trend_regime_filter=True`, check regime and skip BUY in descending, SELL in ascending

**New presets (config/presets.py)**:
- `l2_trend_sell` — signal_direction='sell', ignore_parent_alignment=True, lower_high_sell=True. Pure SELL entry when L2 trend is descending.
- `l2_trend_buy` — signal_direction='buy', ignore_parent_alignment=True, higher_low_buy=True. Pure BUY entry when L2 trend is ascending.
- `l2_regime_aware` — trend_regime_filter=True, ignore_parent_alignment=True, both higher_low_buy and lower_high_sell enabled. Per-candle regime detection blocks contra-trend signals automatically.
- `l2_regime_aware_strict` — same as `l2_regime_aware` but ignore_parent_alignment=False (requires L3 agreement too, fewer/higher-quality signals)

### Duplicate-Signal Skip (Avoid Churning After SL-Hit)
Prevents re-entry on similar signals shortly after stop loss. When enabled, skips new signal if it closely matches a recent SL-hit signal (same direction, entry/sl/tp within threshold %, within N candles). **Fully visible in logs** with dedicated logger.info() call.

**Files**: `config/settings.py`, `config/presets.py`, `bot/backtester.py`, `main.py`, `bot/virtual_order_simulator.py`
**Key config**:
- `duplicate_skip_candles: int` (default 0 = disabled) — how many candles to check backward
- `duplicate_skip_pct: float` (default 2.0%) — allowed variation in entry/sl/tp prices

**Implementation details**:
- `bot/backtester.py`: tracks `last_sl_signal` per side (BUY/SELL) during backtest; checks before opening FakeOrder
- `main.py`: tracks `_pending_signals` on placement, `_recent_sl_hit` on real loss close; `_tf_to_ms` module function converts timeframe to ms; checks in `_try_place_order` before placement. **Logs skip decision** with symbol, preset, side, and candles-ago reference.
- `bot/virtual_order_simulator.py`: tracks `_recent_sl_hit` on virtual loss close; checks in `_try_open` before entry
- Matching logic: same side AND entry within ±pct AND sl within ±pct AND tp within ±pct AND within N candles = skip
- **Decision visibility** (session 28): `skip_duplicate_sl` decision now logged to `bot.log` before being written to decision_log.json

### TATS Scenario — "Took All The Shoes" (Sessions 41–42–47)
Scenario-based capital allocation where only symbols with proven positive live performance are permitted to trade. Locked presets' efficiency scores are evaluated every candle close; symbols that fail the profitability gate are silently excluded from real order placement.

**Files**: `config/risk_config.py`, `main.py` (_try_place_order), `bot/virtual_tracker.py`

**Key details**:
- **Config fields**:
  - `scenario: string` (default "default"; "tats" enables gate)
  - `tats_min_profit_usdt: float` (default 0.0) — locked preset score must exceed this threshold to be eligible
  - `tats_degradation_max_drop_pct: float` (default 50.0) — max recent-loss percentage before excluding symbol
  - `tats_min_weight: float` (default 0.0, added session 47) — minimum weight threshold for single-candidate full allocation; candidates below this get proportional allocation instead
- **Gate logic** (session 42 refined): In `_try_place_order`, before placing real order, check `is_tats_eligible(symbol)`:
  - Explicit gates that remain active: `is_disabled()` (registry), `is_symbol_paused()`, `get_state() != IDLE`
  - Gate removed from TATS path: weight=0 check, is_tats_eligible() performance quality gate, is_virtual_only() floor gate. These were unintended quality filters that contradicted TATS philosophy. Only explicit registry disable should exclude a symbol.
  - If symbol has a locked preset: evaluate that preset's live efficiency score via is_tats_eligible()
  - If score < `tats_min_profit_usdt`: skip (not eligible)
  - If recent trades show > `tats_degradation_max_drop_pct` decline: skip (degrading)
  - Otherwise: eligible, proceed with order
- **Single-candidate allocation** (session 47 addition): When TATS has only one eligible candidate:
  - If candidate's weight >= `tats_min_weight`: allocate full deployable budget (original behavior)
  - If candidate's weight < `tats_min_weight`: allocate proportional budget slice (`deployable × weight / sum(weights)`) instead of full budget
  - Use case: DOGEUSDT (w=1) previously got $5,000 when sole candidate; now gets ~$90 (1% of 50-symbol weight total)
- **Eligible vs excluded** (as of 2026-06-12):
  - ELIGIBLE: DOGEUSDT (w=1, capped to ~$90), MEMEUSDT (w=8), 1000PEPEUSDT (w=10), SOLUSDT (w=20, silent), TIAUSDT (w=15, silent)
  - EXCLUDED: THETAUSDT, REZUSDT, APTUSDT, AVAXUSDT, ETHFIUSDT, EIGENUSDT, JUPUSDT, WLDUSDT, SHIBUSDT (disabled or w=0)
- **Critical data source**: Gate evaluation uses LOCKED PRESET stats from `preset_efficiency_test.json`, not best-overall. When analyzing preset performance for lock decisions, always check server's live efficiency file, never dashboard JSON or virtual simulator rank data.

**Key lesson from session 41**: Seeded scores (from backtests) and live scores (from actual trades) can diverge dramatically. Lock decisions require live data verification on the server, not hypothetical rankings from the dashboard. Analysis that uses wrong data source (virtual sim rank instead of live efficiency) leads to incorrect locks that later fail at gate time.

**Session 42 refinement**: Removed three unintended quality gates from TATS path that were too strict. TATS philosophy: allow ALL enabled symbols to trade if deployable budget available. Symbols that perform poorly must be explicitly disabled in registry, not auto-excluded by performance filters.

**Session 47 refinement**: Added `tats_min_weight` knob (default 0.0) to cap oversized single-candidate allocations. Root cause: DOGEUSDT (w=1) was sole TATS candidate on 2026-06-12 02:45, received full ~$3,200 deployable instead of proportional ~$90, resulted in -$20.77 loss on $5,000 position. With tats_min_weight=3.0, DOGEUSDT now caps to proportional allocation when sole candidate.

**Confirmed working**: TATS gate deployed 2026-06-04, DOGEUSDT placed order under TATS control on first eligible signal. Gates refined 2026-06-06 to remove over-filtering. Weight cap deployed 2026-06-12 (commit 8249da8). Monitored daily as more trades accumulate.

### TATS Minimum Weight (Session 47)
Caps single-candidate allocation in TATS mode when the candidate's weight is below a configured threshold. Prevents low-weight symbols from receiving full deployable budget when they are the only eligible candidate.

**Files**: `main.py` (_try_place_order), `config/risk_config.py`
**Key details**:
- **Setting**: `tats_min_weight: float` in risk_config.json (default 0.0 = disabled, full allocation for any candidate)
- **Logic**: In TATS mode, when single eligible candidate found:
  - If `symbol_weight >= tats_min_weight`: allocate full deployable budget (original behavior)
  - If `symbol_weight < tats_min_weight`: allocate proportional slice `= deployable × weight / sum(all_weights)` instead
- **Use case**: DOGEUSDT weight=1 on 2026-06-12 02:45 was sole candidate, received $3,200 → $5,000 position (margin $1,250, SL hit for -$20.77). With tats_min_weight=3.0, DOGEUSDT now caps to ~$90 (1/50 of weight total).
- **Default behavior**: tats_min_weight=0 preserves pre-session-47 behavior (any candidate gets full allocation).
- **Current setting**: tats_min_weight=3.0 on server (session 47 deployment).
- **First instance verified**: 2026-06-12 after 18:00 UTC, DOGEUSDT now receives proportional allocation when sole candidate.

### Precision Improvements (Session 53)
Four data-backed mechanisms to elevate trade quality by filtering low-confidence entries and eliminating zero-value trading windows.

**Files**: `bot/recommendation_engine.py`, `main.py`, `config/risk_config.py`

**1. Entry Zone Hard Gate** (`entry_zone_max_pct`)
- **What**: Blocks entries outside inner zone; only allows high-quality (Q1) entries near optimal entry level
- **Setting**: `entry_zone_max_pct: float` in risk_config.json (0.0–1.0, default 1.0 = disabled)
- **Logic**: In `recommendation_engine.py::_score_and_filter()` line ~145: skip if `rec.getHowClose() > proximity_zone_pct * entry_zone_max_pct`
- **Rationale**: Backtest data: Q1 entries (closest to optimal) = 76.7% win rate; Q4 entries (farthest) = 9.4% win rate. At 0.75 threshold, blocks outer 25% of zone (Q4 entries).
- **Expected impact**: ~25% fewer entries, but those eliminated were lowest-quality. Net precision improves.
- **Current setting (server)**: 0.75 (inner 75% of zone only)

**2. Precision Reweighting** (reliability vs entry_quality)
- **What**: Recalibrates precision score weights to elevate entry quality (proximity to optimal level) over reliability (variance smoothness)
- **Changes**: In `recommendation_engine.py::_precision()`:
  - `reliability` coefficient: 0.40 → 0.25 (down-weighted)
  - `entry_quality` coefficient: 0.25 → 0.40 (up-weighted)
- **Rationale**: Backtest analysis shows entry-quality correlation with win rate (76.7% Q1 vs 9.4% Q4). Proximity matters more than variance smoothness for trade outcome.
- **Effect**: Precision scores now elevate signals with tight entry zones; winner signals (low adverse move) now rank higher than losers (high adverse move at same level)
- **Current setting**: Applied in code

**3. Global Correction Weight Override** (`global_correction_weight`)
- **What**: Manual override for correction-bonus precision boost (e.g., disable globally if correction patterns consistently hurt)
- **Setting**: `global_correction_weight: float` in risk_config.json (default -1.0 = disabled = use preset's correction_weight)
- **Logic**: In `recommendation_engine.py::_score_and_filter()`, passed to `_precision()` as `correction_weight_override`
- **When >= 0**: Overrides preset's `correction_weight` entirely (e.g., 0.0 = no correction bonus for any preset)
- **Use case**: If correction patterns degrade precision overall, set to 0.0 to disable globally without per-preset edits
- **Current setting (server)**: -1.0 (disabled, use preset values). Deferred pending evaluation.

**4. Trading Blackout Hours** (`trading_blackout_hours`)
- **What**: Skip real order placement during configured UTC hours; virtual orders continue
- **Setting**: `trading_blackout_hours: list[int]` in risk_config.json (0–23 UTC hours, default empty = all hours allowed)
- **Logic**: In `main.py::_try_place_order()` after duplicate check: if `current_utc_hour in trading_blackout_hours`, log message and return 0.0 (real order skipped, virtual continues)
- **Rationale**: Historical analysis: H17–19 UTC = 44 trades at 8% win rate, -$176 net loss. Zero-quality trading window.
- **Expected impact**: Eliminate worst trading window; expect ~$176 P&L recovery if patterns hold
- **Current setting (server)**: [17, 18, 19] (UTC) — blocks 17:00–19:59 UTC

**Server config** (risk_config.json as of 2026-06-16 10:59 UTC):
```json
{
  "entry_zone_max_pct": 0.75,
  "trading_blackout_hours": [17, 18, 19]
}
```

**Deployment**: Commit c01e338 on feature/backtest-live-parity. Docker rebuilt. Bot live.

**Next monitoring** (post-deploy):
- After 50 real trades: verify win rate > 28%, check precision correlation restored
- Confirm zero real orders in H17–19 UTC window
- Evaluate correction-weight impact; consider setting global_correction_weight=0.0 if degrading

---

**Bugs fixed — session 36 (2026-05-27):**

1. **Trail/partial exits with negative PnL not counting as losses** (`main.py`)
   - **Cause**: `_update_loss_streak()` only incremented on `result == 'loss'`; trail/partial exits with negative PnL fell into else branch and RESET streak to 0.
   - **Effect**: Rapid re-entry after losing trail exits, overtrading in choppy markets.
   - **Fix**: Changed to: `is_loss = c.get('result') == 'loss' or (c.get('result') in ('trail', 'partial') and c.get('pnl_usdt', 0.0) < 0)`. Trail and partial exits with negative PnL now count as losses in streak tracking.

**Bugs fixed — session 35 (2026-05-26):**

1. **Negative efficiency presets placed real orders** (`main.py`, `bot/virtual_tracker.py`, `config/risk_config.py`)
   - **Cause**: No gate preventing presets with negative efficiency from real order execution. Floor threshold existed only in documentation.
   - **Effect**: 12/54 live orders (22%) came from negative-efficiency presets, losing -$25 (46% of daily -$52 loss).
   - **Fix**: Added `is_virtual_only(symbol)` method. In main.py `_try_place_order`, gate checks: if best-ranked preset is virtual-only (score < floor AND trade_count ≥ min_trades) → skip real order, log warning. Locked presets bypass gate.

2. **Last-N window ranking not implemented** (`bot/virtual_tracker.py`, `config/risk_config.py`, `dashboard/app/api/trades/route.ts`)
   - **Cause**: All-time cumulative ranking continued even after preset accumulated live trades, preventing recent performance improvement from elevating preset rank.
   - **Effect**: Presets with poor all-time record but recent winning streak remained deprioritized.
   - **Fix**: Added `recent_trades: float[]` field to VirtualTracker. Once trade_count ≥ min_trades_for_ranking (default 3), ranking uses sum of last N (default 10) instead of all-time cumulative. Fallback to cumulative during warm-up. Dashboard effectiveScore() now mirrors Python window logic.

**Bugs fixed — session 33 (2026-05-24):**

1. **Notional cap poisoning virtual tracker** (`bot/order_executor.py`)
   - **Cause**: Quantity was capped in `_submit_to_exchange()` after `OpenOrder` was created with uncapped qty. Virtual tracker recorded uncapped qty, creating phantom PnL that didn't match exchange fills.
   - **Effect**: For INJUSDT, virtual tracker thought position was much larger than actually filled, leading to phantom loss calculations and trading suspension.
   - **Fix**: Moved notional cap logic from `_submit_to_exchange()` into `place_order()` before `OpenOrder` is created. Now stored quantity in order record matches what exchange actually fills.

2. **BGF weight multiplier missing** (`main.py`)
   - **Cause**: When using Best-Gets-First (BGF) scenario, efficiency scores were ranked without considering symbol weights, so losing symbols competed equally with winners for capital.
   - **Effect**: Capital distribution ignored symbol allocation preferences; weights became cosmetic.
   - **Fix**: Added weight multiplier: `efficiency_score *= symbol_weights[sym]` before candidate ranking. Losing symbols now get proportionally less allocation even if they rank higher on raw score.

**Bugs fixed — session 37 (2026-05-28):**

1. **May 23-25 all-loss pattern (out-of-trend continuations)** — Root cause: RISING_BELOW_LAST_HIGH and LOWERING_ABOVE_LAST_LOW signals (continuation types) were firing in opposing parent trends. BUY continuations placed during L2 descending trend = systematic loss pattern. **Fix**: Hard parent-trend alignment gate added to recommendation_engine.py; continuation types now checked against parent trend and skipped if opposing. Reversal types remain allowed for counter-trend entry.

2. **DOGEUSDT $8.95 loss from zone re-entry** — Root cause: After winning trade, bot re-entered same price zone 5 times on subsequent candles, each hitting SL. **Fix**: Zone SL cooldown added (settings: `zone_sl_max`, `zone_sl_cooldown_candles`); after N consecutive SL hits at same level, block that side for cooldown period. Prevents churn on stale support/resistance.

3. **INJUSDT 75 signals blocked by global cap** — Root cause: 75 profitable signals at 4.3–5.0% TP distance were blocked by global `max_profit_pct=3.0%` gate, unable to override. **Fix**: Per-symbol Settings overrides added; risk_config.json["per_symbol_settings"]["INJUSDT"]["max_profit_pct"] now unlocks those signals.

---

## Bugs fixed — session 47 (2026-06-12)

1. **SL floor × max_rr RR collapse** (`bot/recommendation_engine.py`)
   - **Cause**: Engine computed eff_loss_dist from raw loss_dist. When max_rr clamping clipped TP (0.42% profit from 0.105% SL), then main.py floored SL to 0.5%, resulting in recomputed RR = 0.42% / 0.5% = 0.84 (below min 1.5). Engine and main.py disagreed on actual effective RR.
   - **Effect**: MEMEUSDT BUY at RR=4.0 in trades log but decision_log showed `skip_rr: rr=0.84 < min=1.5`. Structural disagreement between modules.
   - **Fix**: Compute `eff_loss_dist = max(loss_dist, entry × global_min_sl_pct/100)` BEFORE all RR computations and TP clipping. Ensures both engine and main.py use same floored SL distance for all calculations.

2. **SELL SL floor mismatch between engine and main.py** (`bot/recommendation_engine.py`)
   - **Cause**: Engine used full 0.5% floor for SELL eff_loss_dist; main.py uses 0.5%/1.5 = 0.333% (account for harsher SELL spikes). This caused engine to compute different TP clipping than main.py's actual behavior.
   - **Effect**: For SELL signals with raw SL between 0.333% and 0.5%, engine over-clipped TP, resulting in actual RR exceeding max_rr at execution time (invalid).
   - **Fix**: Engine now uses `global_min_sl_pct / 1.5` as minimum for SELL signals, matching main.py's behavior exactly.

**Last updated**: Session 47 (2026-06-12) — Critical RR/SL floor fixes, global filters deployed.
