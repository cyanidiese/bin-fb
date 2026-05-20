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

### Multi-Symbol Support
Bot processes multiple symbols concurrently via asyncio. Symbol list stored in `symbol_registry.json`. Can add/remove symbols at runtime via dashboard Settings page.

**Files**: `bot/symbol_registry.py`, `main.py` (on_candle_close per symbol)
**Key details**:
- Registry persists to `symbol_registry.json` (seed from `SYMBOLS` env var on first startup)
- Per-symbol status tracking: backtest state, active/disabled
- Subscriber callback system for registry changes
- Per-rank symbol disable: can disable rank 2–6 positions per symbol without affecting rank 1 (real orders)

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

### Recommendation Generation & Scoring
Analyzes trend state at candle open and generates a single best trading signal (BUY or SELL) with entry, TP, SL, and a 0.0–1.0 precision score.

**Files**: `bot/recommendation_engine.py`, `bot/recommendation.py`, `bot/trend.py`
**Key details**:

**Signal types**:
- `RISING_ABOVE_LAST_LOW` / `LOWERING_BELOW_LAST_HIGH` (primary entries after swing confirmation)
- `ASCENDING_NEAR_HIGHER_LOW` / `DESCENDING_NEAR_LOWER_HIGH` (pre-confirmation entries, if enabled)

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

### Cooldown Mechanisms
Prevents repeated entries after losses and reduces overtrading in choppy markets.

**Files**: `bot/recommendation_engine.py`, `config/settings.py`
**Key details**:
- **Loss streak cooldown**: After `loss_streak_max` consecutive losses on one side (BUY or SELL), block that side for `loss_streak_cooldown_candles` candles
- **Global pause**: If both BUY and SELL lose within `global_pause_trigger_candles` of each other, pause ALL entries for `global_pause_candles` candles

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

### Fake Order Engine
Simulates order entry/exit without exchange API. Entry at next-candle open. Exit on TP hit, SL hit, or trailing stop trigger. Computes PnL, win/loss/partial result.

**Files**: `bot/fake_order.py`
**Key details**:
- Trailing stop: when profit % reaches `partial_take_pct`, exit on next candle if price retraces by `trailing_stop_pct`
- Conservative multi-hit logic: if same candle hits both TP and SL, SL assumed first (loss)
- Stores: entry price, TP, SL, entry type, result, profit_pct, symbol, timeframe
- Serializable to JSON for backtest result export

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
- **Min notional safeguards** (session 22):
  - When balance < min_notional / leverage: auto-bump leverage up to needed level (capped at bracket_max)
  - If even bracket_max insufficient: skip order with `skip_min_notional` decision log entry
  - 2% quantity buffer applied after margin calculation to prevent rounding below Binance floor
- Leverage gated by RiskManager; per-symbol leverage determined by LeverageScenario

**Real order persistence**:
- Stored in `data/real_orders_{SYMBOL}_{MODE}.json` (one file per symbol per mode)
- Includes: entry price, TP, SL, quantity, filled PnL, result, signal metadata, balance at open
- Old sessions archived to `real_orders_{SYMBOL}_{MODE}_archive_{YYYYMMDDTHHMMSSZ}.json` on bot restart

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

**Virtual tracker**:
- Tracks efficiency score per preset per symbol (wins USDT + trade count)
- `best_preset(symbol)` returns highest-score preset (fallback to seeded backtest score until ≥8 live trades)
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

---

## Risk Management

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

### Balance History
Append-only log of balance snapshots (cap 10k entries). Records at: startup, order open (before), order close (after), or >0.5% change.

**Files**: `bot/balance_history.py`
**Key details**:
- File: `data/balance_history_{MODE}.json`
- Each entry: timestamp, balance, trigger (startup / order_open / order_close / change_threshold)
- Correlates with decision log for post-run analysis

### Allocation Weighting (Archived)
Optional feature for distributing capital by preset weight. Disabled by default (`use_allocation_weighting: false`). Can be re-enabled via Settings checkbox.

**Files**: `config/risk_config.py`, `config/settings.py`
**Key details**:
- `symbol_weights` dict in risk config (weight per symbol)
- When enabled: deployable capital = account balance × symbol allocation %
- When disabled: all symbols compete equally for capital (ranking determines priority)

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
Virtual order tracking (`/trades`). Real and virtual preset efficiency, performance chart with trade markers, recent order table.

**Files**: `dashboard/app/trades/page.tsx`, `dashboard/components/PresetEfficiencyTable.tsx`
**Key features**:
- Preset efficiency table: preset name, rank badge (★ Real / #2–#6 / —), trade count, total PnL%, balance
- Hide virtual-only checkbox: filters out presets with 0 real+virtual trades (seeded from backtest, not yet executed)
- Candlestick chart with trade entry/exit markers (▲ BUY, ▼ SELL)
- Recent real orders table (most recent first): symbol, preset, side, entry/exit price, PnL, status

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

### Notifier & Telegram Alerts
Sends alerts to Telegram (token/chat_id from config). Routes warnings/emergencies to alert state file. Implements cooldown to avoid spam.

**Files**: `bot/notifier.py`
**Key details**:
- Alert levels: warning (yellow), emergency (red, re-alert every 30 min by default)
- Logged to system log + alert state file + Telegram (if configured)
- Test notification support for setup verification
- Sample messages built-in for trade wins/losses/balance warnings
- Never raises exceptions (silent failures logged)
- Cooldowns: `emergency_repeat_interval_s`, `warning_repeat_interval_s` per config

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
- Decision types: placed / skip_balance / skip_profit_factor / skip_hard_stop / skip_already_open / skip_no_signal

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

### File-Based Command Channel
Dashboard writes commands to `data/bot_command.json` (with UUID). Bot polls every 2s, executes, writes result to `data/bot_command_result.json` with matching UUID. Dashboard polls for result. Enables graceful mode switches, stop signals, telegram tests without signal handling complexity.

**Files**: `bot/mode_manager.py`, dashboard API routes
**Key details**:
- Commands: switch_mode, stop_bot, test_telegram
- 60–120s timeout per command (with SIGTERM fallback)

### Docker Deployment Ready
Dockerfile and docker-compose.yml in repo root. Bundles bot and dashboard; runs both inside containers on a production VPS.

**Files**: `Dockerfile`, `docker-compose.yml`, `scripts/push.sh`
**Key details**:
- Bot container: Python runtime, logs mounted to host
- Dashboard container: Node.js runtime (Next.js)
- Compose file: service definitions, volume mounts, environment passthrough
- Deploy script: `bash scripts/push.sh` (reads `SERVER_HOST/USER/DIR` from `.env`)

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

**Last updated**: Session 22 (2026-05-17) — Min notional order sizing fix (leverage bump + 2% buffer) + preset refactoring (centralized config/presets.py) + deployment to VPS.
