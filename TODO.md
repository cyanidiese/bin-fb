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
- [ ] Order state reconciliation on startup

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
- [ ] Wire `OrderManager` into `main.py` (requires risk module for quantity sizing)

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
- [ ] Migrate existing `paper_state.json` → `paper_state_BTCUSDT.json`

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
