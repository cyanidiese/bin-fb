# TODO.md — Binance Futures Bot

Legend: [ ] pending  [~] in progress  [x] done

---

## Phase 1 — Foundation

- [x] `.gitignore`
- [x] `.env.example`
- [x] `requirements.txt`
- [x] Project folder structure (`bot/`, `config/`, `data/`, `logs/`, `tests/`)
- [x] `config/settings.py` — load + validate .env, fail fast on missing vars
- [x] `TRADING_MODE` toggle with live-mode confirmation guard

## Phase 1 — Core Bot

- [x] `bot/utils.py` — timezone-aware time helpers (short_time, chart_time, time_to_str)
- [x] `bot/point.py` — swing point model
- [x] `bot/trend.py` — multi-level trend tracker
- [x] `bot/recommendation.py` — placeholder Recommendation + RecommendationTypes
- [x] `bot/kline_processor.py` — swing high/low detection (2-neighbour rule)
- [x] `bot/analyzer.py` — full build + incremental candle updates + permanent point history
- [x] `bot/data_feed.py` — REST kline fetch with cache + WebSocket stream + reconnect
- [x] `bot/exporter.py` — writes results.json for dashboard (1000 klines + all_points history)
- [x] `main.py` — entry point wiring everything together
- [x] Rotating log file (`logs/bot.log`)
- [x] `STOP` file emergency halt
- [x] Separate `logs/trades.log` for signal/order events
- [x] `bot/chart.py` — ASCII rank-based swing point chart
- [x] `bot/display.py` — full console UI (chart + trend table + all-points table + signals)
- [x] Timezone support — TIMEZONE env var wired through all display functions
- [x] Order state reconciliation on startup

## Phase 1 — Risk management

- [x] `config/risk_config.py` — load/save with atomic writes, default-merging, DEFAULT_CONFIG
- [x] `bot/risk_manager.py` — full RiskManager: balance tiers, weighted allocation, leverage formula, drawdown guard, TTL-cached performance score
- [x] `dashboard/app/api/risk/route.ts` — GET config+state, POST save config atomically
- [x] `dashboard/app/risk/page.tsx` — Risk page sections A–E with live polling
- [x] NavBar Risk link
- [x] Paper trader gate — `can_open_sync()` before every entry
- [x] Backtester compound balance tracking + drawdown hard-stop gate per preset
- [ ] Daily loss limit with bot pause (deferred — handled via drawdown guard for now)
- [ ] Leverage validation on startup (warn if set above threshold in exchange settings)
- [ ] Merge `feature/risk-module` into main

## Phase 2 — Validation

- [ ] Run stably on testnet for minimum 7 days
- [ ] Review all critical log events
- [ ] Validate recommendation quality manually from logs

## Phase 3 — Dashboard (Next.js 15)

- [x] Next.js 15 + Tailwind v4 + Chart.js scaffold under `dashboard/`
- [x] `lib/types.ts` — TypeScript interfaces for all JSON fields
- [x] `components/Header.tsx` — symbol, timeframe, mode badge, current price, timestamp
- [x] `components/LevelFilter.tsx` — L1/L2/L3 segmented button (ceiling filter)
- [x] `components/SwingPointsChart.tsx` — price chart with 4 lines + swing dots + trend line
- [x] `components/TrendLevelsTable.tsx` — trend level summary table
- [x] `components/AllPointsTable.tsx` — two-column sortable table (active points only)
- [x] `components/SignalsPanel.tsx` — active trading signals panel
- [x] Active/inactive point distinction — active shown full-color, inactive as gray marks
- [x] Level filter (L1/L2/L3 ceiling) + date range pickers + Clear button in one toolbar row
- [x] Close / Open / Max / Min price lines on chart
- [x] Price lines clamped to earliest active swing point
- [x] Inactive points earlier than oldest active point removed from view
- [x] Auto-refresh — polls results.json every 15s with cache-buster
- [ ] Account info panel — balance, available margin, unrealised PnL via Binance REST

## Kline cache improvement

- [x] Rename cache files to `data/{SYMBOL}_{TIMEFRAME}_{MODE}.json`
- [x] Auto-migrate old `{SYMBOL}_{TIMEFRAME}.json` on first run
- [x] Implement smart append: merge new klines, detect gaps, trim to `KLINE_CACHE_LIMIT`
- [x] Add `KLINE_CACHE_LIMIT` env var (default 5000)

## Phase 3.5 — Backtesting / preset comparison

- [x] `bot/fake_order.py` — FakeOrder model with TP/SL check logic + two-stage partial take
- [x] `bot/backtester.py` — preset runner: replay klines, fake order lifecycle, stats aggregation
- [x] `backtest.py` — CLI: 57 presets, dual output (archive + dashboard feed), summary table
- [x] Same-candle TP+SL spike → loss (SL priority, conservative default)
- [x] Candle-direction same-candle priority in FakeOrder.check() (ascending/descending)
- [x] Presets via `dataclasses.replace()` — no env mutation, backtest-safe
- [x] Partial take (two-stage arm+trigger) in FakeOrder and backtester
- [x] Trailing stop (arm + _max_favorable + trail_price) — 'trail' result type
- [x] Dashboard `/backtest` page — sortable summary table + per-preset trade drill-down
- [x] P&L stats block: actual pts, potential win/loss, avg TP reach
- [x] Preset settings chips displayed below trade list
- [x] `trailing_stop_pct`, `tp_multiplier`, `min_sl_pct`, `max_sl_pct` settings + presets
- [x] `sl_adjust_to_rr` — tighten SL to meet RR instead of skipping
- [x] `max_profit_pct` — skip trades with TP distance wider than N%
- [x] SELL SL ×1.5 adjustment for min_sl_pct check (spikes harsher on SELL)
- [x] Absolute SL floor (0.01% of entry) — reject degenerate micro-swing signals
- [x] Candle-direction same-candle TP+SL priority in FakeOrder.check()
- [x] Direction-based consecutive loss cooldown (candle-based, per side)
- [x] Global pause (both sides lose within N candles → pause all entries)
- [x] `OrderManager` — 3-order live structure + startup reconciliation (`bot/order_manager.py`)
- [x] 4 rounds of preset tuning — best result: `trail_15_from_30_full` (62.5%, +795pts, MaxDD=2)
- [x] `bot/paper_trader.py` — live fake-order engine, per-candle lifecycle, state persistence, JSON export
- [x] `paper_trade.py` — CLI entry point (10 curated presets, DataFeed + PaperTrader wiring)
- [x] `dashboard/app/paper/page.tsx` — auto-refresh, open orders panel, summary table, trade drill-down
- [x] `dashboard/lib/types.ts` — PaperOpenOrder, PaperPreset, PaperResults interfaces
- [x] Analyse existing presets, identify improvement levers, create 7 improved variants per family
- [x] BoS close-price fix — `point.getCloseValue()`, `kline_processor` passes close price
- [x] Corrections as sub-trends — `correction_weight` setting (default 0.0, no behavior change)
- [x] Dashboard datetime pickers — min/max from kline range enforced at browser level
- [x] Fix `backtest_api.py` missing `correction_weight` arg in `Settings()` call
- [x] Round 5 presets (13 new, best: `r5_arm15_cooldown` +1.74% 66.7% 18T)
- [x] Locked presets system — `LOCKED_PRESETS` dict, API enforcement (403), dashboard 🔒 icon
- [x] Live lock/unlock from dashboard — `/api/toggle-preset-lock`, amber confirmation UX
- [x] Dashboard-added locks preserved across reruns (merged into output before overwrite)
- [x] `--klines-count` arg for `backtest.py` — controls fetch + clips loaded klines
- [x] `POST /api/run-backtest` — spawns backtest.py, waits for completion
- [x] Run Backtest button + step-50 klines input + loading overlay on backtest page
- [x] `lower_high_sell` setting — DESCENDING_NEAR_LOWER_HIGH signal, 6 new presets
- [x] `higher_low_buy` setting — ASCENDING_NEAR_HIGHER_LOW signal (mirror), 6+4 new presets
- [x] **Critical fix**: `bot/trend.py` — BUY signals were unreachable (`is_last_high is not None` → `is_last_high`)
- [ ] Re-evaluate all presets now that both BUY and SELL signals fire correctly
- [ ] Run backtest with lh_sell presets and evaluate results
- [ ] Backtest on larger dataset (fetch 5000 candles) for more statistical confidence
- [x] Wire `OrderManager` into `main.py` (requires risk module for quantity sizing)

## Phase 3.6 — Multi-symbol support (design approved 2026-05-04)

See full spec: `docs/superpowers/specs/2026-05-04-multi-symbol-design.md`

### Python backend
- [x] `config/settings.py` — add `load_symbols()` + `load_settings(symbol)` with per-symbol env overrides
- [x] `bot/risk_manager.py` — new: async-safe capital budget tracker (asyncio.Lock, can_open/open/close)
- [x] `bot/exporter.py` — write to `results_{symbol}.json` instead of `results.json`
- [x] `bot/paper_trader.py` — accept optional `risk_manager` param
- [x] `paper_trade.py` — rewrite: loop over symbols, asyncio.gather, write symbols.json
- [x] `backtest.py` — loop over symbols, write `backtest_results_{symbol}.json` per symbol
- [x] `backtest_api.py` — accept `symbol` param; default to first symbol
- [x] `main.py` — write `symbols.json` at startup (stays single-symbol for display)
- [x] Migrate existing `paper_state.json` → `paper_state_BTCUSDT.json`

### Dashboard
- [x] Fetch `symbols.json` in all pages; wire `useSymbol` + `SymbolSwitcher` into header
- [x] `app/page.tsx` — fetch `results_{symbol}.json`; scope localStorage keys to symbol
- [x] `app/backtest/page.tsx` — fetch `backtest_results_{symbol}.json`; add CrossSymbolComparison
- [x] `app/paper/page.tsx` — fetch `paper_results_{symbol}.json`
- [x] `components/CrossSymbolComparison.tsx` — new: 3-tab cross-symbol preset comparison
- [x] `lib/types.ts` — add `SymbolConfig` type for symbols.json
- [x] `useSymbols.ts` — poll every 3s (was: single fetch on mount); newly added symbols reflect live
- [x] `POST /api/symbols` — write placeholder `results_{symbol}.json` immediately on symbol add
- [x] `app/page.tsx` — fix selectedLevel localStorage bug (reset when prev < min available level)
- [x] `app/page.tsx` — "Waiting for bot analysis…" state when klines + trend_levels both empty
- [x] `components/SymbolSwitcher.tsx` — selected symbol pinned left, rest scrollable, max 50% width
- [x] `components/NavBar.tsx` — `max-w-[50%] min-w-0` wrapper for SymbolSwitcher

## Phase 3.7 — Symbol Discovery

See plan: `docs/superpowers/plans/2026-05-07-symbol-discovery.md`

- [x] `bot/symbol_discovery.py` — SymbolDiscovery class: get_precandidates, get_fast_presets, compute_baseline, score_candidate
- [x] `tests/test_symbol_discovery.py` — 10 tests all passing; uses _DASHBOARD_PUBLIC patch for fs isolation
- [x] `discover.py` — project-root CLI: ThreadPoolExecutor, SIGTERM via threading.Event, atomic writes
- [x] `dashboard/app/api/discovery/run/route.ts` — POST: spawn discover.py, track PID, update state on close
- [x] `dashboard/app/api/discovery/cancel/route.ts` — POST: SIGTERM to running discover.py
- [x] `dashboard/components/SymbolDiscovery.tsx` — controls + progress + sortable candidates table
- [x] `dashboard/app/api/_utils.ts` — shared BOT_ROOT + isAlive utilities
- [x] `dashboard/app/settings/page.tsx` — SymbolDiscovery section added
- [x] `dashboard/lib/types.ts` — CandidateResult, DiscoveryState, DiscoveryCandidatesFile interfaces
- [ ] End-to-end test: run discovery from UI, verify candidates appear, add one, verify it disappears

## Phase 3.8 — Order Execution & Infrastructure

- [x] `bot/system_log.py` — rolling 100-entry log
- [x] `bot/notifier.py` — Telegram + alert state + log wrapper
- [x] `bot/mode_manager.py` — mode state and command poll loop
- [x] `bot/order_executor.py` — state machine, SL/TP monitoring, exchange API wired
- [x] `bot/virtual_tracker.py` — virtual order efficiency tracking
- [x] `config/risk_config.py` — extended with Telegram, min_balance, failure threshold fields
- [x] `bot/risk_manager.py` — renamed paper→test, added min_balance check, Notifier wired
- [x] `bot/data_feed.py` — reinit() for runtime mode switching
- [x] `config/settings.py` — test/live mode model (testnet→test rename)
- [x] `main.py` — Notifier, ModeManager, RiskManager, OrderExecutor, VirtualTracker wired
- [x] Dashboard: Start/Stop Bot controls (settings page)
- [x] Dashboard: Trading Mode switcher with obligatory backtest gate
- [x] Dashboard: Telegram Alerts section + test button
- [x] Dashboard: ModeBadge in NavBar
- [x] Dashboard: AlertBanner (warning/emergency alerts, dismissible)
- [x] Dashboard: System log page (/log) with level filter + NavBar unread badge
- [x] `TELEGRAM_SETUP.md` — step-by-step Telegram bot creation guide
- [x] `_submit_to_exchange()` + `_market_close()` wired to real Binance Futures API
- [x] Combined WebSocket stream (`stream_combined`) — all symbols on one WS connection
- [x] Price feed fallback (`start_watchdog`) — REST polling when WS silent >15s
- [x] Kline gap detection and re-fetch — in `refresh_klines` + `_merge`
- [x] Leverage bracket fetch from Binance API (`fetch_leverage_brackets`)
- [x] SL stop-market order placed on exchange after each real order open (crash protection)
- [x] SL order cancelled before any software-triggered market close
- [x] All order closes (TP/SL, bulk-close, single-close) recorded to `real_orders_{symbol}_{mode}.json`
- [x] `seed_from_backtest` skips symbol if already in efficiency file
- [x] RiskManager peak_balance persistence bug fix — initialize from `data/virtual_balance_{mode}.json`
- [x] `/api/public-file` route for runtime-generated JSON files (bypass Next.js build manifest)
- [ ] Allocation weight step 0.01 + initial rebalance on symbol add
- [ ] End-to-end test: start bot from dashboard, switch modes, verify backtest gate fires
- [ ] Fix `.env` — change `TRADING_MODE=testnet` → `TRADING_MODE=test` (currently warns on startup)
- [ ] Apply Global Capital Rules recommendation to risk_config.json (user has proposal, not yet applied)
- [ ] Archive viewer in dashboard (possible future: browse archived sessions)

## Phase 3.9+ — Rank-Based Virtual Pools & Telegram Cooldown Config (session 18)

- [x] Rank-based virtual pools: ranks 2–6, one balance per rank (independent), evict on rank change
- [x] Real/virtual balance strict separation (virtual pools never touch RiskManager)
- [x] Remove `_MAX_PER_DIRECTION` guard and `_loss_cooldowns` (no longer needed)
- [x] Configurable Telegram cooldowns: `emergency_repeat_interval_s` + `warning_repeat_interval_s` in risk_config
- [x] Notifier: emergency/warning repeat intervals as constructor params (not hardcoded)
- [x] TelegramSettings widget: two new dropdowns for emergency/warning cooldowns
- [x] Trades page: "Rank" + "V.Bal" columns in preset table
- [x] Trades page: virtualBalance removed from header
- [x] `/api/trades` returns rank_orders, rank_balances, preset_ranks
- [x] `/api/trades/balances` returns rankBalances (no virtualBalance)
- [ ] Reset hard stop on server (manual: user must dismiss via Risk page dashboard)
- [ ] Update `test_virtual_order_simulator.py` for new rank-based internals (Tester task)
- [ ] Consider XAUUSDT removal (gold is highly correlated to USD macro, may not fit bot strategy)

## Phase 3.9 — Trades Page & Virtual Order Simulation

See spec: `docs/superpowers/specs/2026-05-09-trades-page-and-virtual-orders-design.md`

**Preset cleanup** (done):
- [x] Remove 22 presets with Total% < −10 from `backtest.py` (100 remain + 4 locked)

**Python backend:**
- [x] Fix `VirtualTracker.seed_from_backtest` — skips if symbol already in efficiency file
- [x] Build `bot/virtual_order_simulator.py` — open/close/persist lifecycle, TP/SL checks, early-close on stop/mode-switch
- [x] Real order recording in `OrderExecutor` — `real_orders_{symbol}_{mode}.json` on ALL close types
- [x] Real order opening guard — `_last_opened_preset[symbol]` + exchange verify on preset change
- [x] Wire `VirtualOrderSimulator` into `main.py` — candle close, price update, stop, mode switch
- [ ] Add `Analyzer.get_recommendation_for_preset(overrides: dict) -> Optional[Recommendation]` (deferred)

**Dashboard:**
- [x] `GET /api/trades?symbol=BTCUSDT` — implemented
- [x] `/trades` page — preset efficiency table + trade chart + real orders table
- [x] Merge Logs and Log nav pages (done)
- [x] Fix AlertBanner hidden behind navbar (done)
- [x] Run Backtest button blocking API (done — now non-blocking with polling)
- [x] Stop Bot error when bot already stopped (done)
- [x] Add scenario to decision log entries (done)

**Tests:**
- [x] `tests/test_virtual_order_simulator.py` — lifecycle, dedup, TP/SL, early-close, persistence

**Session 2026-05-14 refinements (session 16–17):**
- [x] Virtual tracker `seed_from_backtest` redesign — trade_count vs seeded_winning_usdt separation
- [x] Raise `_MIN_TRADES` from 4 to 8 — more conservative maturation threshold
- [x] Session data clearing on bot start — archive old real_orders files instead of delete
- [x] Hide virtual-only checkbox on Trades page — hide seeded orders with no runtime trades
- [x] Fix RiskManager hard stop on restart — read persisted balance before initializing peak
- [x] Cross-Symbol Comparison showing only 5 symbols (caused by 404 on runtime files) — fixed with /api/public-file route

## Phase 3.10 — Balance & Leverage Progression ✅ COMPLETE (2026-05-10)

Design approved + implemented in session 14.

- [x] `bot/leverage_tracker.py` — LeverageTracker: graduated level advancement, persistence, add/remove symbol, reset_for_mode
- [x] `bot/balance_history.py` — append-only balance event logger (MAX 10k entries, atomic write)
- [x] `bot/decision_log.py` — append-only placement decision logger (MAX 5k entries, atomic write)
- [x] `bot/virtual_tracker.py` — added `get_efficiency_score(symbol)` + `get_preset_efficiency(symbol, preset_name)`
- [x] `config/risk_config.py` + `bot/risk_manager.py` — `max_leverage_level`/`use_allocation_weighting` defaults; `can_open_sync(symbol)` simplified (removed `estimated_size_usdt` + allocation checks)
- [x] `bot/order_executor.py` — `balance_at_open`, `signal_level`, `precision_score` in records; `leverage` in close result dicts
- [x] `bot/virtual_order_simulator.py` — rewrote: virtual balance pool, leverage_tracker, preset-efficiency sorting, persistence
- [x] `main.py` — efficiency-ranked cross-symbol loop, `_get_fresh_balance()` 5s TTL, LeverageTracker + bh_record + dl_record wired, real min_notionals fetched at startup
- [x] `dashboard/app/api/balance-history/route.ts` — GET /api/balance-history?mode=&limit=
- [x] `dashboard/app/api/risk/route.ts` + `dashboard/app/risk/page.tsx` — max_leverage_level input + use_allocation_weighting checkbox

**Deferred (not in Phase 3.10 scope):**
- [ ] Balance history chart on Risk page (time-series line + order event markers)

## Phase 4 — Order placement

- [ ] Design order placement logic based on Recommendation
- [ ] Order manager (market/limit, SL, TP)
- [ ] Position state tracker

## Phase 5 — Deployment

- [ ] `README.md` with full setup instructions
- [ ] `systemd` service file
- [ ] VPS environment setup guide
- [ ] Remote log viewing instructions
- [ ] Emergency stop file instructions via SSH
- [ ] Security checklist
- [ ] Go-live checklist completed and signed off
