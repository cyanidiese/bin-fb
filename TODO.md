# TODO.md — Binance Futures Bot

Legend: [ ] pending  [~] in progress  [x] done

---

## Session 62 (2026-08-19) — Trade-Close Reporting Fix (deployed)

- [x] **Audit the 4 INJUSDT win notifications** — verified against `bot.log`,
      `balance_history_test.json` and Binance income history. Nothing hidden: exactly
      4 real orders since the 08-18 16:12 restart, all INJUSDT, all genuinely wins.
- [x] **Fix pre-close balance in trade-close message** — `_read_wallet_now()` bypasses
      the 5s TTL cache; unavailable balances print `n/a`, never a stale figure.
- [x] **Fix PnL computed off the signalled entry instead of the fill** —
      `_reconcile_entry_fill()` + `_effective_entry()`. Error on the four real trades
      drops from +6.45 USDT (+5.9%) to +0.13 USDT (+0.12%).
- [x] **Message shows Before / Net PnL / Fee / After** — with `(net of fee)` marker.
- [x] **Reconcile branch history** — `feature/mean-reversion-overlay` was missing
      `3583a73`/`82ca600`/`c77dc4a` (live on server, content hand-copied). Merged;
      zero-diff tree confirmed equivalence.
- [x] **Deploy `ced0757` + restart, MR dormant** — verified dormant on two levels
      (`ENABLE_MEAN_REVERSION` absent from `.env`; `mr_fade` in `preset_blocklist`).

- [ ] **HIGH: stop the `-1003` IP bans.** 14 in 24h, spreading from `futures_account`
      to `load_klines`. Blocks the balance that feeds `risk_manager.update_balance()`
      and sizing (`main.py:1029-1031`) — same surface as the 08-18 phantom-drawdown
      incident. Binance's error text recommends the websocket over polling
      `futures_account`. Options to weigh: user-data-stream balance updates, raising
      `_BALANCE_TTL` above 5s, or one balance read per candle batch instead of per symbol.
- [ ] **Trade-close messages can still be silently dropped** — `notifier.py:161`
      throttles 1 per 120s per symbol. Two closes on one symbol inside 2 min → second
      never sends (log entry still written). Decide whether trade closes should bypass
      the throttle entirely.
- [ ] **Fix or retire `test_notifier.py::test_rate_limit_drops_second_trade_message`** —
      pre-existing failure; asserts a global throttle, code is per-symbol since
      session 42. Test encodes stale intent.
- [ ] **Decide: align FakeOrder trigger geometry to the actual fill?** Currently trails
      arm and stops fire off the *signalled* entry. Left unchanged deliberately in
      session 62 because it moves live trigger levels.
- [ ] **Model funding fees in PnL** — `Before + Net` differs from `After` by funding
      (−0.1511 USDT over these four trades). Needs an income-history call per close.
- [ ] **`JUPUSDT` has weight 0 but produced 40 `BEST` signals in 24h** (2nd most, after
      INJUSDT's 67) — all dropped at `main.py:1041`. Worth a data-backed look at
      re-enabling, per the usual win-rate/trade-count/USDT thresholds.
- [ ] **11 pre-existing test failures remain** (5 `test_risk_manager`,
      5 `test_virtual_order_simulator`, 1 `test_virtual_tracker`) — untouched by
      session 62, but they mean the suite is not a clean gate.

---

## Session 61 (2026-08-13) — Trail-Widen Transfer Analysis (negative result recorded)

- [x] **Test whether `14b014b` wider-trail lever extends to non-l2 trail families** — fee-inclusive resim of every real trade, per-symbol. Result: **net-negative on all 15 trailing presets** (widening lowers avg win + total). Lever does NOT transfer.
- [x] **Diagnose why** — arming style: l2 arms late (activation) → widen helps; other families arm early (partial price) → widening just gives back more of a modest pop.
- [x] **Test opposite direction** — tightening 0.15→0.10 robustly positive across 6/6 live-relevant presets & 3 symbols; live-relevant bucket $766→$864 (+$99/+13%), payoff up, losers unaffected.
- [x] **Record finding** — `docs/profit-analysis/2026-08-13-trail-widen-does-not-transfer.md`. User chose "just record"; NO preset changed.
- [ ] **DEFERRED decision: tighten trail 0.15→0.10 on early-armed live presets** (hl_buy_trail15, r5_sl_filter, r5_tight_rr3, trail_15_from_15) — spec + stage on main + deploy with other staged changes. Revisit likely after the staged `main` commits are deployed.
- [ ] **Do NOT extend `14b014b` widening beyond the 5 l2 presets** — confirmed net-negative elsewhere.

---

## Session 59 (2026-07-16) — Deep Pipeline Analysis, Trail Min-Arming Fix, Structural Defect Specs

- [x] **Pull live trade data and analyze full history** — 260 all-time trades, 92 Jun18-Jul13 window analyzed via parallel agents
- [x] **Identify structural pipeline defects** — BoS hard-wipe (signal droughts) + cross-level stop sourcing (artifact geometry) both documented
- [x] **Deploy Code Fix #1 (trail min-arming)** — commit c77dc4a: min(partial_price, activation_price) arming rule for mixed-preset family
- [x] **Add regression tests for trail min-arming** — 3 new tests, all 14+50 existing tests pass
- [x] **Apply hot-reload guard** — per_symbol_settings.max_sl_pct = 8.0 on 5 symbols (blocks artifact SL zone)
- [x] **Write structural fix specs** — docs/specs/2026-07-16-trend-structure-fixes.md (Fix A + Fix B, validation plan, commit 9d96b8a)
- [x] **Verify deploy and trail fix working** — checked on 2026-07-22: TIAUSDT 1.14%/4.6% healthy position, INJUSDT +$138.51 trail exit
- [ ] **Implement Fix A (same-level stops)** — next session after backtest validation (touch: trend.py getRecommendation, getSupposedNextPoints, find*InBiggerTrends)
- [ ] **Implement Fix B (soft-prune on BoS)** — paired with Fix A; restore min_swing_points_projection=3 on workaround presets
- [ ] **Investigate EIGENUSDT -$138.70 loss (2026-07-17, l2_bos_trend)** — no data yet on cause, flagged for next profit session
- [ ] **Monitor partial_high_rr preset** — 2 catastrophic losses on DOGEUSDT (-$40, -$86 = 67% of DOGE's all-time loss); thin sample, watch for more

---

## Session 58 (2026-07-16) — Dead-Trail Bug Fix, Stuck Positions Closed

- [x] **Diagnose zero-trade silence (3 days post-deploy)** — root cause: dead trailing stop in FakeOrder arming logic
- [x] **Fix dead trailing stop for trail-only presets** — when partial_take_pct==0, now arm at entry±trail_activation_pct (commit 82ca600)
- [x] **Add regression tests** — 3 new tests in tests/test_fake_order_trail_activation.py
- [x] **Deploy fix to feature/backtest-live-parity** — commit 82ca600, graceful stop, Docker rebuild verified
- [x] **Force-close TIAUSDT SELL** — closed at 0.4047, realized -$66.34 loss (avoided worst-case)
- [x] **Force-close DOGEUSDT SELL** — closed at 0.07345, realized -$19.27 loss
- [x] **Keep EIGENUSDT/INJUSDT BUY positions open** — both in profit, 96-candle-capped per user choice
- [x] **Harden bfb-deploy procedure** — verify "Bot stopped." in log before rebuild (SIGTERM was ignored this session — NOW FIXED per session 59)
- [ ] **Investigate Binance REST -1003 rate limits** — 160→23/week improving trend; check kline refresh pattern (5000-candle per symbol per close)
- [x] **Monitor EIGENUSDT/INJUSDT BUY outcomes** — verified 2026-07-22: EIGENUSDT -$18.76, INJUSDT +$138.51 trail exit (exactly what session 59 fix targets)
- [x] **Mark resolved: TIAUSDT/DOGEUSDT stuck positions** — closed Jul 16, total -$85.61 realized

---

## Session 57 (2026-07-13) — Stuck-Position Root Cause Fix, Weight Rebalancing

- [x] **Diagnose TIAUSDT 13-day trading silence** — root cause: max_losing_candles defaults to 0, preset l2_regime_aware set none
- [x] **Implement max_losing_candles safety net** — added `max_losing_candles: 96` to 6 non-locked trend presets
- [x] **Deploy to feature/backtest-live-parity** — commit 3583a73, Docker rebuild verified
- [x] **Hot-reload symbol weights** — MEMEUSDT 8→2, EIGENUSDT 5→8, INJUSDT 5→7
- [x] **USER DECISION: Open positions (TIAUSDT -$75.52, DOGEUSDT -$8.17)** — force-close now or let ride to SL/TP? RESOLVED in session 58 (closed)
- [ ] **Clarify global_min_rr reversion** — currently 2.0, session 53 said 3.0 deliberately set. Intentional or regression? BLOCKED on user clarification
- [ ] **Commit config/presets.py fix to main** — currently uncommitted on main branch after git stash pop; should commit or confirm merge plan from feature/backtest-live-parity
- [ ] **Monitor trading after stuck positions close** — verify bot resumes normal order flow post-fix

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

## Session 33 — Lock Preset, Drag-and-Drop Weights, Rebalancer Restyle, Notional Cap, BGF Multiplier (completed 2026-05-24)

- [x] Lock preset per symbol — API endpoint + risk_config field + dashboard UI (🔒/🔓 button, amber highlight)
- [x] Drag-and-drop symbol weights in PerSymbolAllocation — dnd-kit libraries + SortableRow + onDragEnd handler
- [x] WeightRebalancerSection restyle — standard layout, LabeledInput, matching colors
- [x] Notional cap bug fix — move cap from _submit_to_exchange into place_order before OpenOrder creation
- [x] BGF weight multiplier — multiply raw efficiency score by symbol_weights[sym] before ranking
- [ ] Deploy all five features to server 185.237.14.105 via `bash scripts/push.sh`

## Session 32 — Two-Tier Preset Ranking (completed 2026-05-24)

- [x] Design two-tier tuple-based ranking (tier 1 live-proven vs tier 0 seed-only)
- [x] Replace `_MIN_TRADES=8` with configurable threshold per symbol
- [x] Add `get_min_trades_for_ranking()` helper to risk_config.py
- [x] Update `bot/virtual_tracker.py` with module-level `_score()` function
- [x] Wire `get_min_trades` callable into VirtualTracker constructor (both startup + mode-switch)
- [x] Add tests for tier ordering, TIAUSDT scenario, seed-only fallback, custom thresholds
- [x] Create PresetRankingSection dashboard component with global/per-symbol controls
- [x] Add TypeScript types for new risk_config fields
- [x] All 10 VirtualTracker tests pass; TypeScript clean build
- [x] Deploy two-tier preset ranking to server — NOT YET (included in session 33 batch)

## Session 31 — Dynamic Weight Rebalancer (completed 2026-05-23)

- [x] Design weight rebalancer algorithm (rank-normalize, soft-blend, floor clamp)
- [x] Create bot/weight_rebalancer.py with full WeightRebalancer class
- [x] Write 19 unit tests for weight rebalancer
- [x] Wire WeightRebalancer into main.py candle-close loop
- [x] Add weight_rebalancer config block to risk_config.py
- [x] Create dashboard WeightRebalancerSection component
- [x] Add weight_rebalancer types to risk-types.ts
- [x] Final code review (APPROVED_WITH_CONCERNS)
- [ ] Fix IMPORTANT issue: live config changes require bot restart (hot-reload needed for production)
- [ ] Fix IMPORTANT issue: datetime.fromisoformat() uses local timezone (needs explicit UTC handling)
- [ ] Push main branch to remote (currently local only)
- [ ] Add .claude/worktrees/ to .gitignore (minor housekeeping)
- [ ] Deploy to server when ready

## Session 30 — Docker service split + backtest_api fix (completed 2026-05-23)

- [x] Fix backtest_api.py missing max_losing_pct/amount/candles Settings fields
- [x] Split docker-compose into bot + dashboard services
- [x] Update push.sh for split-container deploy
- [x] Create push_dashboard.sh dashboard-only deploy script
- [x] Disable bot/start API route (returns 503)
- [x] Simplify mode API route (remove cross-container isBotAlive)
- [ ] Deploy session 29+30 changes to server: `bash scripts/push.sh` (stop bot first)

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
- [x] Startup kline fallback — bot survives rate-limit bans by using cached klines when update fetch fails
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
- [ ] Reset hard stop on server (manual: user must dismiss via Risk page dashboard) — pending
- [ ] Update `test_virtual_order_simulator.py` for new rank-based internals (Tester task) — pending
- [ ] Consider XAUUSDT removal (gold is highly correlated to USD macro, may not fit bot strategy) — pending

## Phase 3.11 — Telegram Interactive Menu (session 21)

- [x] `bot/telegram_menu.py` — TelegramMenu class with async polling, button dispatch, persistence
- [x] `bot/telegram_views.py` — pure HTML-escaped rendering functions for all screens
- [x] Three-tier access control: owner (full), viewer (read-only), unknown (blocked)
- [x] All screens: Status, Symbols, Trades (Real/Virtual), Backtest, Controls
- [x] Write actions (owner only): pause, resume, enable, reset hard stop, manage viewers
- [x] Viewer management: persistent JSON + in-memory pending requests with dedup
- [x] Async long-polling (30s timeout) with `asyncio.to_thread(requests.get, ...)`
- [x] Symbol pause feature: `SymbolRegistry.pause_symbol()`, `resume_symbol()`, `is_symbol_paused()`, `get_paused_symbols()`
- [x] All tests passing (191 total)

## Phase 3.9 — Trades Page & Virtual Order Simulation

See spec: `docs/superpowers/specs/2026-05-09-trades-page-and-virtual-orders-design.md`

**Preset cleanup** (done):
- [x] Remove 22 presets with Total% < −10 from `backtest.py` (100 remain + 4 locked)
- [x] Centralize presets in `config/presets.py` (PRESETS, LOCKED_PRESETS, ALL_PRESETS)

**Python backend:**
- [x] Fix `VirtualTracker.seed_from_backtest` — skips if symbol already in efficiency file
- [x] Build `bot/virtual_order_simulator.py` — open/close/persist lifecycle, TP/SL checks, early-close on stop/mode-switch
- [x] Real order recording in `OrderExecutor` — `real_orders_{symbol}_{mode}.json` on ALL close types
- [x] Real order opening guard — `_last_opened_preset[symbol]` + exchange verify on preset change
- [x] Wire `VirtualOrderSimulator` into `main.py` — candle close, price update, stop, mode switch
- [x] Live positions on Trades page (LIVE badge, open positions displayed before closed orders)
- [x] SIGTERM graceful shutdown on deploy (closes virtual + real orders before exit)
- [x] Weight=0 trading gate (weight-zero symbols excluded from both real and virtual order placement)
- [ ] Add `Analyzer.get_recommendation_for_preset(overrides: dict) -> Optional[Recommendation]` (deferred)

**Dashboard:**
- [x] `GET /api/trades?symbol=BTCUSDT` — implemented
- [x] `/trades` page — preset efficiency table + trade chart + real orders table
- [x] Merge Logs and Log nav pages (done)
- [x] Fix AlertBanner hidden behind navbar (done)
- [x] Run Backtest button blocking API (done — now non-blocking with polling)
- [x] Stop Bot error when bot already stopped (done)
- [x] Add scenario to decision log entries (done)
- [x] Live positions shown with LIVE badge (real=green, virtual=blue)

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

## Bug fixes — five batches completed (2026-05-19, session 23–24)

**First batch (commit b915ebf)**:
- [x] `VirtualTracker._set_efficiency()` preserves `seeded_winning_usdt` on trade count updates
- [x] `Notifier._fmt_price()` dynamic precision for Telegram display
- [x] `OrderExecutor._market_close()` fallback parameter for avgPrice=0
- [x] `OrderExecutor._place_sl_on_exchange()` downgraded to info level

**Second batch (commit 4c80dd2)**:
- [x] C1+A3: Full preset filter chain applied live + current market price as entry
- [x] G1/I2: `_placed_this_candle` dict prevents double-orders per candle
- [x] E4: Skip trade recording when pnl=0 and close==entry
- [x] C3: Weight allocation tracks deployed vs deployable capital
- [x] H1/C4: Leverage change failure raises (aborts order)
- [x] G2: `check_symbol_price()` early return when PLACING
- [x] B1: `best_preset()` returns at score>=0
- [x] E2: Taker fee deducted from PnL in both OrderExecutor and VirtualOrderSimulator

**Third batch (commit 241ae28)**:
- [x] Retry guard: set `_placed_this_candle` after both success and failure
- [x] D1: OHLC-level SL/TP checks via `check_symbol_candle()` for gaps
- [x] C2: Post-rounding min-notional bump with FundsError fallback
- [x] H2: `_market_close()` warning when avgPrice fallback used
- [x] H4: `_auto_disable()` raises BotHaltError instead of sys.exit(1)

**Fourth batch (commit 12696db)**:
- [x] A1: `analyzer.add_candle()` calls `_refresh_recommendations()` every candle

**Fifth batch (session 24 — deep audit fixes, commit 36caeea)**:
- [x] BUG-02: `virtual_order_simulator.py:233-234` read preset_settings not base_settings (critical)
- [x] BUG-01: `risk_manager.py:true_pf()` returns 99.0 for 100% win (was 0.0, blocking orders)
- [x] BUG-03: Drawdown events now notify Telegram (was logger-only)
- [x] BUG-04: Virtual SL skipped when `sl <= 0` (no fallback for high-price instruments)
- [x] BUG-05: `analyzer.add_candle()` sets `_current_price` from REST close before recommendations
- [x] BUG-06: Non-weight allocation loop breaks when budget exhausted (was redundant iteration)
- [x] BUG-11: `_klines` list capped at 3000 (was unbounded, memory degradation)
- [x] BUG-17: Negative efficiency scores returned for net-losing symbols (was 0, same as no-data)
- [x] BUG-18: `_existing_bigger_points` set now persistent (was O(N²) rebuild every call)

**Open audit findings (not yet fixed)**:
- [ ] BUG-07: Virtual order sizing uses real account allocation, not rank-pool balance
- [ ] BUG-09: Swing point timestamps use close-time not open-time (shows 15m ahead)
- [ ] BUG-16: `get_symbol_allocation` reads risk_config.json on every call (hot path caching)

## Deferred ideas (analysed, not yet planned)

- [ ] **Trend cross-level validation gap** — L2/L3 trend states go stale when L1 crosses a parent BoS without completing its own internal reversal. Proposed fix: "early parent notification" — on every new L1 point, also check if it crosses a parent BoS and push the extremal point up immediately. Three trigger options: close-only (recommended), wick, hybrid. Full analysis: `docs/superpowers/specs/2026-05-22-trend-cross-level-validation-analysis.md`

## Open investigations (pending session)

- [x] ETHFIUSDT -4164 notional error (order quantity precision) — FIXED in session 22 (leverage bump + 2% buffer)
- [x] Loss streak cooldown missing from live bot — FIXED in session 28 (full implementation in main.py)
- [x] Decision log tmp file race condition — FIXED in session 28 (PID-qualified temp names)
- [x] Duplicate skip decisions invisible in logs — FIXED in session 28 (added logger.info())
- [x] SL floor × max_rr RR collapse — FIXED in session 47 (compute eff_loss_dist before RR calcs)
- [x] SELL SL floor mismatch — FIXED in session 47 (engine now uses 0.5%/1.5 for SELL)
- [ ] SOLUSDT/TIAUSDT structural silence (L3 swings stale, prices far from targets) — pending investigation
- [ ] Hard stop still active after restart (balance recovery edge case) — pending user investigation
- [ ] XAUUSDT zero signals (strategy fit or data issue) — pending investigation
- [ ] Pre-existing test failures: test_place_order_happy_path, test_perf_cache_ttl — pending investigation

## Session 53 Continuation — Precision Improvements (2026-06-16)

- [x] **Implement entry zone hard gate** — entry_zone_max_pct config in recommendation_engine.py, rejects outer-zone low-quality entries
- [x] **Apply precision reweighting** — reliability 0.40→0.25, entry_quality 0.25→0.40 based on Q1 vs Q4 backtest data (76.7% vs 9.4% win rates)
- [x] **Add global correction weight override** — global_correction_weight config key for manual correction-bonus control (deferred, currently -1.0)
- [x] **Implement trading blackout hours** — trading_blackout_hours config in main.py, skips real orders H17–19 UTC (expected +$176 recovery)
- [x] **Set risk_config values** — entry_zone_max_pct=0.75, trading_blackout_hours=[17,18,19] on server
- [x] **Deploy to production** — Commit c01e338, Docker rebuild, bot live as of 10:59 UTC
- [ ] **Monitor: after 50 real trades** — Check win rate (target >28%), verify precision correlation restored
- [ ] **Monitor: H17–19 UTC blackout** — Confirm zero real orders in window; verify no phantom orders
- [ ] **Consider: global_correction_weight=0.0** — If correction bonus continues to hurt, disable globally (currently deferred)
- [ ] **Merge feature/backtest-live-parity → main** — Still pending from session 52
- [ ] **Investigate SOLUSDT zero orders** — weight=20 but no real orders; still pending from session 52

## Session 52 — Loss Reduction: Risk Config Optimization (2026-06-15)

- [x] **Diagnose phantom SL from max_loss_usdt** — Identified root cause: dollar cap at high quantities creates microscopic SL distance
- [x] **Disable max_loss_usdt globally** — Set to 0 in risk_config.json (cap was overriding all structural exits)
- [x] **Raise global_min_sl_pct** — Changed 0.5% → 0.7% (backtest data shows 38.9% good rate vs 33.4% at 0.5%)
- [x] **Block Level 3 signals globally** — Added global_max_level=2 (L3 signals: 73.4% loss rate vs 66.9% for L2)
- [x] **Implement global signal-type filter** — Added global_blocked_signal_types=["lowering_near_last_low"] (100% loss rate)
- [x] **Expand preset_blocklist** — Added loose_entry, broad_zone, aggressive, default, low_rr (all high loss rates)
- [x] **Deploy code filters to production** — Commit d5d4fee: global_blocked_signal_types + global_max_level in recommendation_engine.py

## Session 51 — Deploy Procedure Documentation (2026-06-14)

- [x] **Discover Docker image code-not-mounted issue** — Python source baked in image, not volume-mounted
- [x] **Document critical deploy procedure** — `docker compose build bot` is MANDATORY for code changes
- [x] **Identify three undeployed sessions** — commits 4276319, f0323a1 were live on disk but not in running image (Jun 12-14 17:38)
- [ ] **Update deploy script/documentation** — Add `docker compose build bot` step to official deploy process
- [ ] **Monitor DOGEUSDT (r6_arm15_rr4)** — Should now place orders with correct locked preset (fix deployed 17:38)
- [ ] **Monitor MEMEUSDT (sl_adjust_rr_tp95)** — Should now place orders (fix deployed 17:38)
- [ ] **Monitor WLDUSDT/THETAUSDT/disabled symbols** — Virtual orders should accumulate at rank 2–6 after rebuild
- [ ] **Test position persistence** — Verify `data/restart_positions_{mode}.json` created on next graceful shutdown with open position
- [ ] **Verify SELL SL floor fix** — Commit 36d8863; confirm on next real SELL close

## Session 50 — Locked presets blocklist bypass bug fix (2026-06-14)

- [x] **Fix locked_presets blocklist bypass** — Line 441 main.py: changed `if preset_name in _blocklist:` to `if not is_locked and preset_name in _blocklist:` (commit 4276319). Locked presets now bypass global blocklist.
- [x] **Verify DOGEUSDT and MEMEUSDT locked presets now execute** — Both were silently blocked before fix; orders should flow again after 15:57 UTC (but NOT deployed in image until session 51)

## Session 49 — RR epsilon fix, per-symbol locked presets (2026-06-14)

- [x] **Fix RR floating-point epsilon** — Changed comparison to use epsilon (1e-9) in recommendation_engine.py lines 144-147 (commit eb42fef)
- [x] **Lock DOGEUSDT to r6_arm15_rr4** — Configured in risk_config.json (but orders blocked by blocklist bug — FIXED in session 50)
- [x] **Lock MEMEUSDT to sl_adjust_rr_tp95** — Configured in risk_config.json (but orders blocked by blocklist bug — FIXED in session 50)
- [x] **Configure logrotate** — `/etc/logrotate.d/trading-bot` weekly rotation, 8 weeks retention, copytruncate
- [x] **Archive cleanup** — Deleted 941 stale virtual_orders_rankN_*.json files from /opt/bot/data/

## Session 48 — Overnight trading freeze + TIAUSDT unblock + EIGENUSDT analysis (2026-06-13)

- [x] **Fix min_profit_factor too strict** — Lowered from 1.15 → 1.08 in risk_config.json (hot-reload, no code change)
- [x] **Fix TIAUSDT locked_presets typo** — Changed key from TIASDT → TIAUSDT in risk_config.json
- [x] **Fix TIAUSDT TATS fallback bypass** — main.py line 1028 now checks locked_presets before falling back to best_preset (commit 997a5ac)
- [x] **Analyze EIGENUSDT exclusion** — All-negative recent trades; TATS correctly excludes; system will self-heal when VirtualTracker turns positive
- [x] **Re-enable EIGENUSDT weight** — Set weight back to 5 (was 0 from session 46)
- [ ] **Monitor TIAUSDT BUY outcome** — hl_buy_trail15 placed at 11:15 UTC, entry=0.3356, TP=0.3928 (+17.1%)
- [ ] **Monitor DOGEUSDT SELL** — placed at 10:30 UTC, entry=0.08729, high stop risk at 0.11%
- [ ] **Verify SELL SL floor fix** — Next SELL close will confirm commit 36d8863 applied correctly
- [ ] **Investigate EIGENUSDT SELL candidate/best discrepancy** — Why only candidate, never BEST (regime or scoring)
- [ ] **Run EIGENUSDT backtest** — Refresh preset efficiency data after 1-2 trading sessions

## Session 47 — Critical SL floor bugs fixed, global filters deployed, TATS weight cap added (2026-06-12)

- [x] **Fix SL floor × max_rr RR collapse** — Engine used raw loss_dist for all RR computations; main.py floored SL separately, causing RR mismatch (commit ~deaf4ce)
- [x] **Fix SELL SL floor inconsistency** — Engine used 0.5% for SELL, main.py uses 0.333%; now engine uses 0.5%/1.5 for SELL (commit 36d8863)
- [x] **Add global trend regime filter** — Blocks BUY in descending, SELL in ascending regimes
- [x] **Add global min/max RR filters** — min_rr=3.0, max_rr=4.0 global gates on all signals
- [x] **Block trail_15_from_30_tp95** — TIAUSDT -$65.29 loss on 4 trades; add to preset blocklist
- [x] **Deploy 3 commits to feature/backtest-live-parity** — All fixes live on server
- [x] **Add TATS minimum weight cap** — tats_min_weight=3.0 prevents low-weight symbols from allocating full budget (commit 8249da8)
- [x] **Verify all critical audit bugs fixed/mitigated** — A1, A2, B1-B5 all resolved per session 43 audit
- [ ] **Monitor MEMEUSDT after RR fix** — Verify placements now have correct RR (was 4.0, decision_log showed 0.84)
- [ ] **Investigate SOLUSDT/TIAUSDT structural silence** — L3 swings stale (prices moved far from TP targets)
- [ ] **After balance >$4,500: widen leverage** — base=3, max=8
- [ ] **Monitor for SELL trade** — First SELL placement after SELL SL floor fix will confirm fix is working
- [ ] **Backtest-live gap 7-step fix** — From reference_gap_analysis.md, still pending

## Session 46 — Weight rebalancing + Lock removal + EIGENUSDT mute (2026-06-11)

- [x] **EIGENUSDT weight → 0** — Mute symbol from real trading (virtual-only); profit_factor=0.92 < 1.15 consistently blocks every candle
- [x] **Remove SOLUSDT lock** — Virtual_tracker fix (session 45) now filters blocklisted presets correctly; lock workaround no longer needed
- [x] **Remove 1000PEPEUSDT lock** — Same fix; bot now auto-selects best eligible preset without deadlock
- [x] **INJUSDT weight 10→3** — All presets negative recently; reduce exposure while virtual tracking accumulates data
- [x] **TIAUSDT weight 12→15** — Best performer (trail_15_from_15, score +63.28); reward with modest increase
- [x] **Record session state** — Document symbol weights, P&L, balance, pending items
- [ ] **Monitor SOLUSDT without lock** — Expect db_layer_1 to be selected automatically
- [ ] **Monitor 1000PEPEUSDT without lock** — Expect r5_rr3 or db_layer_0 to be selected
- [ ] **Monitor INJUSDT weight=3** — If still consistently losing, reduce to 0
- [ ] **Monitor TIAUSDT weight=15** — Verify trail_15_from_15 continues performing at later candles

## Session 45 — Hot-Reload Config + PEPE Preset Bug Fix + Notional Cap (2026-06-11)

- [x] **Deploy max_order_notional_usdt: 5000** — hot-reload to risk_config.json
- [x] **Fix PEPE preset blocklist deadlock** — Blocklisted preset winning score but then blocked at gate, leaving symbol idle (commit b88388c)
- [x] **Lock SOLUSDT to r8_sol_hlbuy_cooldown** — Best performer (+$84.20 on 11 trades) — REMOVED in session 46
- [x] **Lock 1000PEPEUSDT to r5_sl_adj_cooldown** — Alternative to blocklisted db_clone_cooldown — REMOVED in session 46
- [x] **Reduce DOGEUSDT weight 3→1** — Has 60% WR but -$30 net (unfavorable R:R)
- [x] **Discover architecture constraints** — Code baked in Docker image (not volume-mounted); P3 sizing already fixed; blocklist was missing from config during June 6–11 period

## Session 43 — Performance Analysis + Symbol Rebalancing + TATS Bug Fix (2026-06-11)

- [x] **Analyze 355 trades over 19 days** — Identified best/worst symbols and presets, net -$159 USDT
- [x] **Fix TATS n==1 zero-score bypass** — Added candidate filter for zero-score symbols before n==1 check (1 line, main.py ~1042)
- [x] **Disable 5 worst-performing symbols** — THETAUSDT, AVAXUSDT, REZUSDT, ETHFIUSDT, APTUSDT set to DISABLED in symbol_registry.json
- [x] **Re-enable INJUSDT** — Was wrongly disabled June 2, restored to trading (net +$7.61)
- [x] **Blocklist 8 poor presets** — Added to risk_config preset_blocklist: db_clone_cooldown, pre_confirm_prox15_trail15, pre_confirm_trail15, trail_15_from_15_d1, sl_adjust_rr_tp95, r6_arm15_rr4, correction_w20_trail15_30, trail_15_from_15
- [x] **Update active symbol weights** — 1000PEPEUSDT 22→10; active universe now 7 symbols
- [x] **Deploy changes to server** — All changes live (symbol_registry hot-reload + risk_config deployed + code fix deployed)

## Session 42 — Telegram Fixes + TATS Gate Cleanup + Signal Generation Fallback (2026-06-06)

- [x] **Fix Telegram shutdown closes** — close_all_orders_at_market now notifies each close via notify_trade_close
- [x] **Fix multi-symbol Telegram rate limiting** — Changed shared "trade" key to per-symbol keys so simultaneous closes all notify
- [x] **Remove unintended TATS quality gates** — Removed weight=0 check, is_tats_eligible gate, is_virtual_only gate from TATS path (gates too strict, contradicted design)
- [x] **Fix hl_buy/lh_sell signal generation** — Added base-settings fallback: if best_preset requires higher_low_buy=True but base engine returns None, try get_recommendation_for_preset with full overrides
- [x] **Save TATS design spec** — Full spec at docs/superpowers/specs/2026-06-06-tats-fix-and-signal-generation-design.md
- [x] **Analyze WLDUSDT market regime** — Pre/post-May-22 regime break, 74/78 presets negative, recommend disable (user action pending)
- [x] **Analyze June 5 missed signals** — 8 × 1000PEPEUSDT SELL ($360-480), MEMEUSDT now fixed, REZUSDT zero-price anomaly flagged
- [x] **Disable WLDUSDT in registry** — Analyst recommendation now acted on (disabled in session 43)
- [x] **Investigate REZUSDT zero-price signals** — Flagged in session 43 analysis; symbol now disabled

## Session 41 — TATS Deployment & Live Efficiency Analysis (2026-06-04)

- [x] **Deploy TATS scenario to server** — risk_config scenario=tats, gate evaluated on candle close
- [x] **Verify TATS gate working** — DOGEUSDT placed order under TATS control
- [x] **Correct TIAUSDT lock mistake** — Was locked to pre_confirm_prox15_trail15 (losing preset), now auto-selects db_layer_3 (winner)
- [x] **Confirm TATS eligible set** — 5 symbols pass profitability gate (DOGEUSDT, 1000PEPEUSDT, ETHFIUSDT, INJUSDT, TIAUSDT)
- [x] **Learn live-data analysis lesson** — Never use virtual sim rank data or dashboard JSON; always check preset_efficiency_test.json on server
- [ ] **Monitor TATS eligible set daily** — Track as more trades accumulate, adjust locks/config as needed
- [ ] **Add TATS filter logging** — is_tats_eligible silent failures should log (for debugging)

## Session 40 — Bug Fix + Performance Analysis + Config Optimization (2026-05-31)

- [x] **Fix Settings import bug** — main.py line 16 missing Settings class from import statement (commit a79139a)
- [x] **Performance analysis** — analyzed 54 trades May 28–31, identified best/worst symbols and presets
- [x] **Config changes applied** — APTUSDT/REZUSDT weight to 0, THETAUSDT weight boost, INJUSDT max_profit unlock, leverage increases, decision_log reset
- [x] **Infrastructure cleanup** — freed 4.2GB disk space on server with docker system prune
- [x] **Monitor INJUSDT signal flow** — per_symbol_settings now working, signals unblocked
- [x] **Monitor for NameError recurrence** — watch next candles for any Settings NameError
- [ ] **Run backtest for REZUSDT/APTUSDT** — if re-enabling these symbols in future
- [ ] **Enable WeightRebalancer** — after 1–2 weeks of stable operation (currently disabled)
- [ ] **Watch audit bugs** — 2 critical / 5 important bugs still pending from session 26 audit

## Session 39 — Strategy Page Time Travel (2026-05-28)

- [x] **replay_api.py** — Python script re-runs Analyzer.build_from_klines(klines[:idx+1]) on stored results JSON
- [x] **tests/test_replay_api.py** — 6 pytest tests for symbol validation, negative index guard, boundary cases
- [x] **dashboard/app/api/replay/route.ts** — POST route, validate input, subprocess timeout guard
- [x] **TimeScrubber.tsx** — React slider with ◀ ▶ buttons, LIVE badge, datetime label, loading state
- [x] **Integration** — scrubber state in Strategy page, 300ms debounced fetch, data-source switching
- [x] **Update FEATURES.md** — documented Strategy Page Time Travel with design details
- [x] **Update CLAUDE_NOTES.md** — RESUME POINT for session 39

## Session 38 — Range Position Max Sweep & Preset Tuning (2026-05-28)

- [x] **Complete sweep analysis** — 78 presets × 15 symbols × 6 values = 7,020 combinations tested
- [x] **Assign optimal values to all presets** — data-backed assignment in config/presets.py with rationale per value group
- [x] **Verify 0.10 group quality gain** — confirmed monotonic improvement across all sweep values and symbols (genuine quality, not suppression)
- [x] **Update CLAUDE.md** — added decision mandate for deep analysis before metric assignment
- [x] **Update FEATURES.md** — documented Range Position Max Tuning with methodology and results
- [ ] **Run full backtest on server** — validate range_position_max impact on live preset performance

## Session 37 — Signal Quality Improvements (2026-05-28)

- [x] **Hard parent-trend alignment gate** — CONTINUATION_TYPES + _parent_is_opposing() helper, skips out-of-trend continuations (commit 21e480e)
- [x] **Minimum precision floor** — min_precision_score setting, per-preset tuning (commit 21e480e)
- [x] **Zone SL cooldown** — zone_sl_max/cooldown_candles settings, blocks zone re-entry after N losses (commit 21e480e)
- [x] **Per-symbol Settings overrides** — per_symbol_settings dict in risk_config.json, applied in _try_place_order (commit 21e480e)
- [x] **Dashboard UI updates** — PresetSettingsPanel + Create page abbreviations (mprec, zslm, zslc)
- [x] **Investigation doc** — saved signal quality analysis findings

## Session 36 — All 5 Improvement Items Completed (2026-05-27)

- [x] **Item 1: disable ETHFIUSDT** — symbol_registry.json disabled dict + is_disabled() check in on_candle_close()
- [x] **Item 2: remove APTUSDT lock** — removed from locked_presets, now uses virtual tracker scoring
- [x] **Item 3: fix loss streak (Problem 2)** — trail/partial exits with negative PnL now count as losses in streak tracker (commit 6392635)
- [x] **Item 4: backtests for idle symbols** — ran TIAUSDT THETAUSDT INJUSDT EIGENUSDT, updated scores
- [x] **Item 7: enable allocation weighting + rebalance** — use_allocation_weighting=true, 15 symbols weighted 20-0
- [ ] **Item 5: lock top performers** — deferred, awaiting decision
- [ ] **Item 6: raise base leverage** — deferred, user waiting for avg profit > avg loss

## Session 35 — HLB Locks, Last-N Ranking Floor Gate (completed 2026-05-26)

- [x] **Lock HLB presets for 3 positive-balance symbols** — TIAUSDT, SOLUSDT, EIGENUSDT locked; 3 negative-balance symbols reverted
- [x] **Implement last-N sliding-window ranking** — VirtualTracker.recent_trades field + window-based score selection
- [x] **Add virtual-only floor gate** — is_virtual_only() check in main.py, skip real orders for floor-gated presets
- [x] **Analyze daily order data** — 39 orders, -$52 USDT, 12 from negative-efficiency presets
- [x] **Problem 2: trail exit cooldown** — fixed in session 36: trail/partial exits with negative PnL now count as losses
- [x] **Problem 3: min_sl_pct default floor** — deferred, not implemented
- [x] **Re-run all backtests** — completed in session 36 for idle symbols

## Session 34 — Graceful Shutdown Fix (completed 2026-05-26)

- [x] **Fix SIGTERM handler / graceful shutdown** — docker-compose 60s grace period + 45s API timeout wrapping
- [x] **Investigate bot crash pattern (API rate limit)** — confirmed: not crashes, were manual restarts via docker
- [x] **Deploy graceful shutdown fix to server** — already deployed 2026-05-26, monitoring for orphan positions
- [ ] **Deploy session 33 features** (lock preset, drag-and-drop weights, notional cap fix, BGF multiplier) — pending user approval
- [ ] **Fix over-allocation during concurrent trades** — pending

## Session 29 — Code Review Fixes + Early Loss Exit + Green Dot Fix (completed 2026-05-23)

- [x] **Lot constraint detector fixes** — tier selection reversed, `min_bal_pct` from config, fallback to 0.0
- [x] **Symbol registry thread safety** — added lock to `get_weight`, `get_weights`, `set_weight`, `get_leverage_override`
- [x] **Main.py balance detection** — fallback chain: `risk_manager.get_balance()` → `startup_balance` → 1000.0
- [x] **Early loss exit settings** — `max_losing_pct`, `max_losing_amount_usdt`, `max_losing_candles` added to 29 presets
- [x] **Dashboard early exit UI** — PresetSettingsPanel + presetFilters + Create page abbrevs (mlp, mla, mlc)
- [x] **Green dot data source fix** — new `/api/open-positions` route replaces `preset_efficiency_test.json` fetch
- [x] **Deploy to server** — `bash scripts/push.sh` (stop bot first)

## Session 27 — Precision & Login Fixes (completed 2026-05-21)

- [x] **Precision fix** — TIAUSDT `-1111` errors caused by float tick_size fallback. Fixed by storing tick_size as string + added `_price_str()` formatter. Deployed commit `be0d3a2`.

- [x] **Login auth cookie fix** — `router.push()` client-side nav didn't send cookie. Fixed with `window.location.replace()` for full page reload.

- [x] **Symbol selector indicator dots** — red=disabled, green=has live orders. `/api/symbols` every 30s + preset_efficiency JSON source.

- [x] **Chart datetime pickers** — Trades page filter by date range, shows candle count, reset button.

- [x] **Duplicate-signal skip feature** — `duplicate_skip_candles` + `duplicate_skip_pct` settings to prevent re-entry on similar SL-hit signals. Wired into backtester, main.py, and virtual simulator.

## Session 26 — Critical Fixes (identified but not yet fixed)

Priority order (blocker issues identified in deep performance audit):

1. [ ] **Per-symbol loss circuit breaker** — prevent REZUSDT-style cascading losses (8 consecutive -3-4% drops). Consider: disable symbol after N consecutive losses, or reset SL anchor on close, or add loss limit per symbol per session.

2. [ ] **Fix profit_factor backtest field** — `profit_factor` always 0.00 in backtest results → gate blocks BTCUSDT, EGLDUSDT. Either: wire PF calculation into backtester result export, OR switch gate to use win% + total profit% as proxy instead of backtest PF.

3. [ ] **Fix ETHFIUSDT preset incompatibility** — best preset `r6_arm15_full` has `max_profit_pct=3%` cap but signals target 17-20% TP. Either: loosen preset cap, OR create signal-type-compatible preset variant.

4. [ ] **Fix position sizing: use total balance not free balance** — concurrent trades currently use only available margin → oversized positions (SHIB trade 102% of account). Switch `_get_fresh_balance()` to return total account balance for sizing checks.

5. [ ] **Investigate TIAUSDT hard-stop** — symbol is strongest backtest performer (+44.9%) but never traded. Verify: did initialization virtual trade legitimately trigger hard-stop, or is it a phantom? User to review risk_state.json before restart.

## Phase 5 — Deployment

- [ ] `README.md` with full setup instructions
- [ ] `systemd` service file
- [ ] VPS environment setup guide
- [ ] Remote log viewing instructions
- [ ] Emergency stop file instructions via SSH
- [ ] Security checklist
- [ ] Go-live checklist completed and signed off
