# CLAUDE_NOTES.md — Binance Futures Bot Session Log

## Last updated: 2026-05-07 (session 11)

---

## Session continuity protocol

**Save decisions immediately, not at end of session.** VS Code can close unexpectedly, losing all context. After any resolved question, design decision, or implementation choice — update CLAUDE_NOTES.md and TODO.md right then. Do not wait for a "summary at the end." If a session is going long, stop and write a checkpoint entry mid-session.

---

## Project status

| Component | Status |
|---|---|
| Project instructions (CLAUDE.md) | done |
| `.gitignore` | done |
| `.env` (testnet keys present) | done |
| `.env.example` | done |
| `requirements.txt` | done |
| Project folder structure | done |
| `config/settings.py` — load/validate .env | done |
| `bot/utils.py` — timezone-aware time helpers | done |
| `bot/point.py` — swing point model | done |
| `bot/trend.py` — multi-level trend tracker | done |
| `bot/recommendation.py` — signal model | done |
| `bot/kline_processor.py` — swing detection | done |
| `bot/analyzer.py` — trend state manager + permanent point history | done |
| `bot/data_feed.py` — REST + WebSocket | done |
| `bot/chart.py` — ASCII swing-point chart | done |
| `bot/display.py` — full console UI | done |
| `bot/exporter.py` — writes results.json for dashboard | done |
| `main.py` — entry point | done |
| Logging (structured, rotating) | done |
| STOP file emergency halt | done |
| Timezone support (TIMEZONE env var) | done |
| **Dashboard — Next.js 15 + Tailwind + Chart.js** | **done** |
| `bot/recommendation_engine.py` — scorer + selector | **done** |
| `bot/fake_order.py` — trailing stop, candle-direction priority, serialization | **done** |
| `bot/backtester.py` — preset runner, all new settings | **done** |
| `backtest.py` — CLI, 75 presets, dual output | **done** |
| `bot/paper_trader.py` — live fake-order engine, state persistence, JSON export | **done** |
| `paper_trade.py` — CLI entry point, 10 curated presets | **done** |
| Kline cache rename (`_{MODE}.json`) + migration | **done** |
| Dashboard `/backtest` page + summary table + trade list | **done** |
| Dashboard `/paper` page — open orders panel + summary table + drill-down | **done** |
| `config/settings.py` — sl_adjust_to_rr, max_profit_pct | **done** |
| `bot/order_manager.py` — 3-order live structure + reconciliation | **done** |
| Symbol registry (add/remove symbols from Settings page) | **done** |
| `config/risk_config.py` — load/save with atomic writes, default-merging | **done** |
| `bot/risk_manager.py` — full RiskManager (balance, allocation, leverage, drawdown) | **done** |
| `dashboard/app/api/risk/route.ts` — GET config+state, POST save config | **done** |
| `dashboard/app/risk/page.tsx` — Risk page sections A–E with live polling | **done** |
| `dashboard/components/NavBar.tsx` — Risk nav link | **done** |
| Risk management module | **done** |
| Tests (risk module — 21 tests) | **done** |
| `bot/symbol_discovery.py` — SymbolDiscovery class + CandidateResult | **done** |
| `discover.py` — CLI entry point for discovery subprocess | **done** |
| `dashboard/app/api/discovery/run/route.ts` — POST spawn discover.py | **done** |
| `dashboard/app/api/discovery/cancel/route.ts` — POST SIGTERM | **done** |
| `dashboard/components/SymbolDiscovery.tsx` — discovery UI on Settings page | **done** |
| `dashboard/app/api/_utils.ts` — shared BOT_ROOT + isAlive | **done** |
| Tests (symbol_discovery — 10 tests) | **done** |
| Tests (other modules) | not started |
| Deployment files | not started |

---

## Dashboard — `dashboard/`

Standalone Next.js 15 app under `dashboard/`. Reads `dashboard/public/results.json`
written by the bot on every candle close. Start with `npm run dev` inside `dashboard/`.

### Stack
- Next.js 15 App Router, TypeScript, Tailwind v4 (CSS-based config, no `tailwind.config.ts`)
- Chart.js + react-chartjs-2 + chartjs-adapter-date-fns (`type: 'time'` scale)

### Components
| File | Purpose |
|---|---|
| `app/page.tsx` | Main page — data load, level filter state, date range state, derived filtered datasets |
| `components/Header.tsx` | Symbol, timeframe, mode badge, current price, snapshot timestamp |
| `components/LevelFilter.tsx` | L1/L2/L3 segmented button control (ceiling filter) |
| `components/SwingPointsChart.tsx` | Price chart with 4 lines + swing dots + amber trend line |
| `components/TrendLevelsTable.tsx` | Trend level summary (direction, BoS, last high/low) |
| `components/AllPointsTable.tsx` | Two-column sortable table — active points only |
| `components/SignalsPanel.tsx` | Active trading signals |
| `lib/types.ts` | TypeScript interfaces for all JSON fields |

### Chart datasets
1. **Close Price** — indigo filled line
2. **Open Price** — slate dashed line
3. **Max Price** — green dashed line (kline highs)
4. **Min Price** — red dashed line (kline lows)
5. **Trend Line** — amber, connects active swing points only (straight segments)
6. **Swing Points** — colored dots per level × high/low; click legend to toggle any series

All price lines are clamped to start at the **earliest active** swing point visible on the chart.

### Point display rules
- `active: true` — full-color dot (L1 green/red, L2 amber/orange, L3 violet/sky); radius 5/7/9
- `active: false` — small gray mark (radius 3, 35% opacity) — historical context only
- **Tables** show only active points
- Inactive points that predate the oldest active point in the current filter are removed entirely

### Filtering (toolbar — one row)
- **Level selector** (L1/L2/L3): ceiling — L2 shows L1+L2 points and trend levels
- **From / To** datetime-local pickers: filter both klines and points by date range
- **Clear** button: resets both date pickers

---

## Decisions made

### Architecture
- Folder structure: `bot/`, `config/`, `data/`, `logs/`, `tests/`, `dashboard/`
- Dashboard is a separate Next.js app, not coupled to bot runtime
- Bot and dashboard communicate only via `dashboard/public/results.json`

### Strategy
- Price Action: swing highs/lows detected via `SWING_NEIGHBOURS` rule (default 3)
- Multi-level `Trend` hierarchy — L1 finest, L3 coarsest
- `removePointsUpTo()` is called on every BoS crossing, wiping older points from the live trend

### Permanent swing point history (added 2026-04-21)
Problem: `removePointsUpTo()` left only 1 point in exports after a BoS event.
Solution: `Analyzer` maintains `_all_points` — a list that accumulates every detected point with level assignments, captured **before** any BoS wipeout can occur:
- L1 points captured at detection time (before `checkPointObject`)
- L2/L3 points captured via `_capture_bigger_trends()` after each `checkPointObject`
- `get_all_points()` computes `active` flag by comparing to current live trend state
- Exporter uses this history; fallback to live-trend traversal if not provided

### Active / inactive point distinction
- Active = currently present in the live trend (post-BoS state)
- Inactive = historically detected but wiped by a subsequent BoS
- Both exported; dashboard uses `active` flag to style differently
- Tables filter to active only; chart shows both

### Exporter
- `_MAX_KLINES = 1000` — exports full kline cache (≈10 days at 15m) so price lines reach oldest active point
- Exports on startup and every candle close

### Data
- Kline cache: `data/{SYMBOL}_{TIMEFRAME}.json`, up to 1000 candles, gitignored
- On startup: load cache → fetch only missing since last cached → merge → save

### WebSocket
- `{symbol}@kline_{timeframe}` stream; reconnect with exponential backoff
- `on_candle_close` fires on `k.x == true`

### Config
- `.env`: `SYMBOL`, `TIMEFRAME`, `KLINE_LIMIT`, `SWING_NEIGHBOURS`, `TRADING_MODE`, `TIMEZONE`
- Live mode requires `LIVE_MODE_CONFIRMED=yes` as second guard

---

## Known issues / notes
- **Testnet price spikes**: Testnet produces artificial prices (e.g. 83,000 when real BTC ~75,000). These form valid-looking L2 swing points in history. Dashboard shows them accurately.
- **Multiple bot instances**: Two concurrent `python main.py` processes race to write `results.json` and the kline cache. Always kill old process before starting new one.
- **Cache corruption**: If a bot instance is killed mid-write, the cache JSON can truncate. Next run logs "No cache found" and re-fetches 1000 klines from testnet automatically.

---

## Rejected alternatives
- Polling price — rejected for WebSocket (exact candle-close events)
- Database for kline storage — deferred, JSON cache sufficient
- python-binance WebSocket manager — rejected for direct `websockets` (explicit testnet/live URL)
- Traversing live trend for `all_points` export — rejected; BoS wipeouts cause single-point exports
- Showing only active points on chart — rejected; inactive shown as gray context marks

---

## Open questions
- `Recommendation` / order placement logic — placeholder only, not built
- MySQL credentials in `.env` — not needed for bot, ignored

---

## .env current state
- `TRADING_MODE=testnet` ✓
- `TESTNET_API_KEY` / `TESTNET_API_SECRET` present ✓
- `SYMBOL=BTCUSDT`, `TIMEZONE=Europe/Kyiv` ✓
- Live `API_KEY` / `API_SECRET` also present — keep separate from testnet usage

---

## Multi-symbol support — design approved 2026-05-04

Full spec: `docs/superpowers/specs/2026-05-04-multi-symbol-design.md`  
Summary in: `UPCOMING_FEATURES.md`

Key decisions:
- Config: `SYMBOLS=BTCUSDT,XAUUSDT` in `.env`; per-symbol overrides via `{SYMBOL}_{SETTING}` env vars
- Concurrency: pure asyncio — `asyncio.gather` over per-symbol coroutines; klines loaded sync at startup
- File naming: all output files get `{SYMBOL}_` prefix (`results_{SYMBOL}.json` etc.)
- Symbol discovery: bot writes `dashboard/public/symbols.json` at startup
- New module: `bot/risk_manager.py` — async-safe capital budget gating for live orders
- Backtest: serial loop over symbols; no parallelism needed
- `main.py`: stays single-symbol for now (display UI not multi-symbol-ready)
- Dashboard: `SymbolSwitcher.tsx` + `useSymbol.ts` already exist and are compatible — just need wiring
- New dashboard component: `CrossSymbolComparison.tsx` (3-tab preset comparison across symbols)
- Combined efficiency metric: average profit% across symbols (default sort in Tab 3)

---

## Next steps
1. ~~**Dashboard auto-refresh**~~ — done (polls every 15s with cache-buster)
2. ~~**Dashboard backtest page**~~ — done (`/backtest` route, summary table + per-preset trade drill-down)
3. ~~**Run real backtest**~~ — done; multiple rounds of preset tuning on testnet 15m data
4. ~~**Old TrendAnalyzer ideas**~~ — done: candle-direction priority, SL-adjust-to-RR, max_profit_pct, SELL SL ×1.5
5. ~~**Direction-based cooldown + global pause**~~ — done (candle-based, not time-based)
6. ~~**OrderManager (live order structure + startup reconciliation)**~~ — done (`bot/order_manager.py`)
7. ~~**Corrections as sub-trends**~~ — done (`correction_weight` setting, default 0.0 — safe no-op)
8. ~~**BoS close-price trigger**~~ — done (`point.getCloseValue()`, kline_processor passes `close`)
9. ~~**Locked presets system**~~ — done (`LOCKED_PRESETS` dict + API enforcement + dashboard UI)
10. ~~**Run Backtest button + klines count selector**~~ — done (`/api/run-backtest`, step-50 number input)
11. ~~**Live lock/unlock from dashboard**~~ — done (`/api/toggle-preset-lock`, confirmation UX, persists across reruns)
12. **Account info panel** — balance, available margin, unrealised PnL via Binance Futures REST
13. ~~**Risk management module**~~ — done (`feature/risk-module` branch, 21 tests)
14. **Order placement** — wire `OrderManager` into main.py; requires risk module for sizing
15. **Merge `feature/risk-module`** — review + merge into main when ready

### Partial take — real orders (Phase 4 note)
For live/testnet orders, partial take requires a **trailing stop** on the exchange side (e.g. a trailing-stop-limit order that activates once price reaches `partial_price`). This is not trivial via Binance API. Deferred to Phase 4 order manager design.

### Session 8 — 2026-05-02: corrections, locked presets, backtest dashboard controls

#### BoS close-price fix
`checkIfHigherThanDescBreakOfStructure` / `checkIfLowerThanAscBreakOfStructure` now compare `point.getCloseValue()` against the BoS level instead of the wick high/low. This prevents wicks from falsely triggering trend flips. `Point` stores `_value_close` (from `klines[i][4]`); `KlineProcessor` passes `'close': float(klines[i][4])` in the returned dict.

#### Corrections as sub-trends
When L1 flips direction (BoS confirmed), `trend.py` captures correction metadata just before the flip into `_correction_end_info`:
- `depth_pct` — how far L1 retraced into the L2 impulse move
- `swing_count` — number of L1 swings after the last L2 impulse peak/trough (not all-time count)
- `bos_level`, `bos_direction`

`_correction_quality()` in `recommendation_engine.py` scores 0.0–1.0 multiplicatively: both `swing_score` and `depth_score` must be non-zero. Hard gates: depth > 100% → potential reversal → 0; depth < 30% → noise → 0. Peak at 50% Fibonacci.

`correction_weight` setting (default **0.0**) multiplies the correction bonus — zero behavioral change unless explicitly enabled. All existing presets unaffected.

**Bug fixed**: original `get_correction_info()` counted all L1 swings since history began, giving `swing_count` of 26–87 and `depth_pct` of 200–500%. Fixed by using `bigger.getTimeOfLastHigh()` / `getTimeOfLastLow()` as the correction start reference, giving realistic 1–5 swing counts.

#### Round 5 presets (13 new)
Added `r5_tight`, `r5_rr3`, `r5_sl_filter`, `r5_sl_adjust`, `r5_tight_rr3`, `r5_tight_sl`, `r5_all_filters`, `r5_trail10`, `r5_arm25`, `r5_arm20`, `r5_arm15_cooldown`, `r5_sl_adj_cooldown`, `r5_trail10_rr3`.

**Standout result**: `r5_arm15_cooldown` → **+1.74%, 18 trades, 66.7% win rate, MaxDD=5** — best overall.

Also added 5 correction presets (`correction_w10/w20/w30`, etc.) — all using `correction_weight > 0` for experimental evaluation.

Total presets: **99**.

#### Locked presets system
**Problem**: top-performing presets were being lost when code changed or during exploration. User explicitly requested lock protection.

**Design**:
- `LOCKED_PRESETS` dict in `backtest.py` — separate from `PRESETS`, merged at run time with `{**LOCKED_PRESETS, **PRESETS}`. Code-level locks are always enforced.
- `main()` outputs `'locked_presets': list(code_locked) + extra_locked` where `extra_locked` = presets locked via dashboard that aren't in `LOCKED_PRESETS` (preserved across reruns by reading existing JSON before overwrite).
- `DELETE /api/delete-preset` — reads `locked_presets` array from JSON, returns 403 if preset is locked.
- `POST /api/toggle-preset-lock` — adds/removes name from `locked_presets` array in JSON.
- `BacktestSummaryTable` — `lockedPresets: Set<string>` prop; locked rows show 🔒 icon, no delete button; on hover shows 🔓 Unlock (locked) or 🔒 Lock + 🗑 Remove (unlocked).
- Confirmation UX: unified `pendingAction: { name, type: 'delete' | 'lock' | 'unlock' }`. Delete confirmation = red Yes; lock/unlock confirmation = amber Yes. Row background: red tint for delete, amber tint for lock/unlock.

**Locked presets (code-level, permanent)**:
| Preset | Profit | Win% | MaxDD |
|---|---|---|---|
| `trail_15_from_30_full` | +1.12% | 53.8% | 5 |
| `trail_15_from_30_cooldown` | +1.09% | 53.8% | 5 |
| `sl_adjust_rr_tp95` | +1.02% | 54.5% | 5 |
| `trail_20_from_30_cooldown` | +0.97% | 53.8% | 5 |

#### Dashboard Run Backtest + klines selector
- `--klines-count N` arg added to `backtest.py`: controls both `fetch_count` passed to `DataFeed.refresh_klines()` and clips loaded klines array to most recent N (so smaller counts give faster reruns on cached data).
- `POST /api/run-backtest` — spawns `python backtest.py --klines-count N`, waits for completion, returns `{ ok: true }`.
- Backtest page: header now has a number input (step 50, min 50, max 10 000, persisted in localStorage) + **▶ Run Backtest** button. While running: button shows spinner + "Running…" and is disabled; all panels below dim to 30% opacity with pointer-events blocked; a large centered spinner overlay appears with kline count message.

#### Dashboard datetime pickers — min/max enforcement
Both the main strategy page and the Visualize Preset panel derive `klineMinDate` / `klineMaxDate` from the loaded klines and pass them as `min=` / `max=` HTML attributes to `datetime-local` inputs, disabling out-of-range dates at the browser level.

### Backtest analysis — 2026-05-01 (session 7): preset optimisation across all families

#### Dataset at time of analysis
BTCUSDT 15m, 1065 klines (~11 days). All 77 presets, 22 signals all `lowering_above_last_low` SELL type.

#### Key findings (empirical)

| Setting | Effect |
|---|---|
| `trailing_stop_pct` ↓ | Monotonically better — exits closer to best price. 0.10→0.01 captures 32% more per trade |
| `partial_take_pct` (arm threshold) 0.15 | Sweet spot — arms early enough to catch short moves, avoids noise at <0.05 |
| `min_profit_pct=0.7` | Filters out the 2 worst entries entirely (price goes straight to SL with 0% favorable). Drop 2 losses, keep all winners |
| `tp_multiplier=0.95` | Best value. 0.90 or lower → fewer qualifying trades, net negative |
| `loss_streak_max=2`, `cooldown=2` | Optimum. Streak=3 lets bad runs through (+2 losses). Streak=1 blocks recovery trades |
| `min_profit_loss_ratio` | Higher RR = fewer trades. Best not to add on top of tight trail — it filters winners along with losers |
| Adding trail to high_rr | Reduces profit — high_rr relies on full TP hits which never trigger trail arm |

#### Improvements created per family

| Family | Original preset | Improvement | Old profit | New profit | Distinctive change |
|---|---|---|---|---|---|
| tight trail + protection (custom) | custom_tp095_partial010_trail010_ls2 | optimized_arm15_trail01_minp07 | 1.91% | **2.52%** | trail=0.01, minp=0.7 |
| arm=0.30 + cooldown | trail_15_from_30_full | improved_arm30_trail02_protect | 0.97% | **1.38%** | trail→0.02, add minp=0.7 |
| SL-adjust mechanism | sl_adjust_rr_tp95 | improved_sladj_trail01_protect | 0.87% | **1.27%** | trail→0.01, add protection |
| arm=0.30 + trail=0.20 | trail_20_from_30_tp95_cooldown | improved_arm30_trail20_minp07 | 0.85% | **1.49%** | tighter cooldown (cd=2), minp=0.7 |
| wide arm=0.50 + medium rr | medium_rr_partial_50 | improved_arm50_trail02_rr2 | 0.74% | **1.31%** | add trail=0.02, tp=0.95, protection |
| arm=0.15, no protection | trail_15_from_15 | improved_arm15_trail05_protect | 0.69% | **2.40%** | add protection + minp=0.7 + trail→0.05 |
| zone=20 + rr=3 | db_layer_1 | improved_zone20_rr3_trail01 | 0.68% | **1.27%** | trail→0.01, zone=20 kept, add protection |
| high_rr (rr=2.5, minp=1) | high_rr | *(not improved)* | 0.92% | — | 3 trades, price never moves favorably on losses, no parameter helps |

#### What makes presets distinct from each other
- `optimized_arm15_trail01_minp07`: arm=15%, trail=1%, minp=0.7 — tightest trail, entry filter
- `improved_arm30_trail02_protect`: arm=30% (later arm) + trail=2%, cd=5 — waits for deeper move
- `improved_sladj_trail01_protect`: sl_adjust_to_rr=True — SL tightened to match RR target (unique mechanism)
- `improved_arm30_trail20_minp07`: arm=30% + trail=20% (significantly looser trail) — different exit behaviour
- `improved_arm50_trail02_rr2`: arm=50% (very wide arm, only arms when halfway to TP) + rr=2 filter
- `improved_arm15_trail05_protect`: arm=15% + trail=5% — middle ground between tight and loose trail
- `improved_zone20_rr3_trail01`: zone=20 proximity filter — only enters when price is close to level

### Backtest findings — 2026-04-30 (4 rounds of tuning, 1000 × 15m candles)

#### Best preset: `trail_15_from_30_full`
Settings: `partial_take_pct=0.30`, `trailing_stop_pct=0.15`, `tp_multiplier=0.95`,
`loss_streak_max=2`, `loss_streak_cooldown_candles=5`, `global_pause_trigger_candles=3`, `global_pause_candles=10`
Result: **62.5% win rate, 8 trades, +1.03% / +795 pts, MaxDD=2**

#### Runner-up cluster (all identical 3 trades, 67% win)
RR≥4× + arm 15% + trail 20% → db_full_clone, db_layer_0/3, etc.
Too few trades to be statistically reliable.

#### Proven parameter effects (empirical, not theoretical)
| Change | Effect |
|---|---|
| arm 15% → 30% | Trade count 3→10, win rate 67%→60% — more trades, slightly lower precision |
| trail 20% → 15% | +76 pts per run, same win rate — tighter trail captures more of each move |
| tp_multiplier 0.95 | +~36 pts for free — TP slightly easier, same trades hit |
| cooldown (2 losses) | 10T→8T, 60%→62.5%, MaxDD 3→2 — drops 2 loss trades without losing any trail wins |
| RR filter ≥3.0 on arm-30 | 10T→4T, 60%→50% — kills trail wins, counterproductive |
| SL filter (0.05%, 1.5%) | No effect on this dataset (all 10 trades already in range) |
| wider zone (20%) | No effect — same 10 trades selected |
| min_swing_points=4 | No effect — same 10 trades selected |

#### Dead ends
- arm ≥50% (partial_50/60/70): ≤44% win rate, high MaxDD
- Low RR (<2.0): ≤14% win rate, negative profit
- TP multiplier alone (no trail): same bad trades, smaller wins
- structure_sensitive (swing_neighbours=3, min_swing=5): worst preset (-1312 pts)

### Backtest presets (16 total) — 2026-04-26
Groups:
- **Base**: `default`
- **Entry zone**: `tight_entry`, `medium_entry`, `loose_entry`, `broad_zone`
- **RR**: `high_rr`, `low_rr`
- **Structure sensitivity**: `conservative`, `aggressive`, `structure_sensitive`
- **Partial take**: `partial_50` (50%), `partial_60` (60%), `partial_70` (70%)
- **Combined**: `partial_tight`, `partial_high_rr`, `partial_conservative`

### Dashboard backtest page design — 2026-04-26
- Route: `/backtest` (Next.js App Router, `app/backtest/page.tsx`)
- Reads `/backtest_results.json` from `dashboard/public/` on page load
- `BacktestSummaryTable` — sortable by any column, default sort by `total_profit_pct` desc, click row to drill down
- `BacktestTradeList` — per-preset trade table, color-coded win/partial/loss
- Nav bar added to `layout.tsx`: Strategy / Backtest links
- `backtest.py` writes both `data/backtest_{timestamp}.json` (archive) and `dashboard/public/backtest_results.json` (live feed)

---

## Code analysis — old TrendAnalyzer (2026-04-26)

User provided the old DB-backed `TrendAnalyzer` code for reference. Key insights extracted:

### What the old code confirmed
- **Entry zone**: signal fires when price is within a `CORIDOR_ALLOWED_START_MARGIN`% of the corridor boundary (e.g. 30%). Our `whichIsCloser()` threshold (10%) is the same concept, tighter.
- **R:R adjustment**: old `prepare_profit_and_loss()` shrank/modified TP/SL. We do NOT do this — structural levels stay intact; R:R is a filter, not a modifier.
- **Level 1 skip**: old code explicitly returns None for level == 1. Our `MIN_SWING_POINTS` guard achieves the same.
- **`getSupposedNextPoints()`** in `trend.py` already implements the projection logic we designed, and refines with bigger-trend extremes (takes the more extreme value if bigger trend has one).
- **`getRecommendation()`** in `trend.py` already implements the correct signal logic:
  - Last swing = LOW (pullback done) → BUY context
  - Last swing = HIGH (peak made) → SELL context
  - Proximity to projected extreme → reversal signal
  - Uses `smaller_trend.getBreakOfStructure()` as SL

### What's different / new in our implementation
- Multi-level candidate scoring (old code returns first valid level, we score all and pick best)
- Precision scoring with 3 components (reliability, parent alignment, entry quality)
- Failed-order cooldown (deferred to after backtesting)

### `how_close` and entry quality
`whichIsCloser()` returns `(direction, how_close)` where `how_close` is distance from the boundary as % of swing range. Entry quality = `max(0, 1 − how_close / PROXIMITY_ZONE_PCT)`. This is computed inside `getRecommendation()` and needs to be stored on the returned Recommendation object.

For recommendation types that don't go through a proximity check (e.g. RISING_BELOW_LAST_HIGH, LOWERING_ABOVE_LAST_LOW — price is clearly inside the range, no closeness computed), store `how_close = PROXIMITY_ZONE_PCT` (threshold value → entry quality = 0 for those).

---

## Recommendations module — design (brainstorm 2026-04-26)

### Architecture decision
Each trend level generates an independent candidate recommendation. A selection step then picks the single best one to act on.

There is NO hard block based on level conflicts: L1 DESC within L2 ASC is a valid pullback entry. Conflict is handled via the precision score, not rejection.

### Per-level candidate fields
- `level` — which trend level generated this
- `side` — BUY or SELL
- `entry` — see open question #2
- `tp` — Take Profit price
- `sl` — Stop Loss price (BoS invalidation level)
- `projected_profit_pct` — (TP − entry) / entry
- `projected_loss_pct` — (entry − SL) / entry
- `rr` — projected_profit / projected_loss
- `precision` — float 0.0–1.0 (see below)

### Projection logic (ASC, generalized)
Use the last `PROJECTION_LOOKBACK` completed swings (not just the last one):
```
asc_diffs  = [HH_i − HL_i  for each completed upswing][-PROJECTION_LOOKBACK:]
desc_diffs = [HH_i − HL_{i+1} for each completed pullback][-PROJECTION_LOOKBACK:]
avg_asc    = mean(asc_diffs)
avg_desc   = mean(desc_diffs)
projected_HH = latest_HL + avg_asc
projected_HL = projected_HH − avg_desc
```
DESC trend: mirror (project LowerLows/LowerHighs).

Trend weakening signal: if `asc_diffs` values are decreasing → lower precision score.

### Precision score (0.0–1.0) — agreed breakdown
| Component | Weight | Calculation |
|---|---|---|
| Projection reliability | 0–0.40 | 1 / (1 + coeff_of_variation(asc_diffs + desc_diffs)); lower variance = higher score |
| Parent alignment | 0–0.35 | Parent trend agrees with signal: 0.35; neutral/no parent: 0.175; opposes: 0.0 |
| Entry quality | 0–0.25 | How close current price is to ideal entry zone (as fraction of swing range) |

### Candidate selection algorithm
```
1. Skip level if active_points < MIN_SWING_POINTS
2. Skip level if avg_asc ≈ 0 (range too small) or avg_desc >= avg_asc (exhaustion)
3. Compute projected_profit_pct, projected_loss_pct, rr, precision for each level
4. Filter: discard if projected_profit_pct < MIN_PROFIT_PCT
5. Filter: discard if rr < MIN_PROFIT_LOSS_RATIO
6. Among remaining:
   a. max_precision = max(candidate.precision)
   b. similar = [c for c if max_precision − c.precision <= PRECISION_SIMILARITY_THRESHOLD]
   c. If similar has >1 candidate → pick highest projected_profit
   d. Else → pick highest precision
7. If no candidates survive → emit nothing
```

### Env variables for Recommendations
| Variable | Purpose | Default |
|---|---|---|
| `MIN_SWING_POINTS` | Min active points per level to generate signal | `3` |
| `MIN_PROFIT_PCT` | Min profit as % of entry price | `0.5` |
| `MIN_PROFIT_LOSS_RATIO` | Min profit-to-loss ratio (R:R) | `1.5` |
| `PRECISION_SIMILARITY_THRESHOLD` | Max precision gap to treat as "similar" | `0.10` |
| `PROJECTION_LOOKBACK` | Number of completed swings to average for diffs | `3` |
| `PROXIMITY_ZONE_PCT` | "Close to level" threshold as % of swing range | `0.15` |

### Preset testing system (planned)
Goal: run the analyzer with different env-variable presets and compare resulting recommendations and efficiency. Design for this from the start — all parameters come from env vars, nothing hardcoded.

### Open questions (need answers before implementation)
1. ~~**Precision weights**~~ — resolved: 0.40 / 0.35 / 0.25
2. ~~**Entry price**~~ — resolved: current market price at the moment the recommendation is generated (candle open). In backtesting: open price of the candle immediately after the signal fires.
3. ~~**Recommendation expiry**~~ — resolved: recommendations are ephemeral. Generated at candle start (after new klines fetched and trends rebuilt), used immediately, then discarded. No persistence, no expiry logic needed. Next candle start produces a fresh set.

   **Exception — failed-order cooldown:** if an order was opened from a recommendation and subsequently hit SL, the bot must NOT re-enter on an identical recommendation for `FAILED_ORDER_COOLDOWN_CANDLES` candles (env var, default `2`). "Identical" = same side (BUY/SELL) AND SL at the same swing-point level (the concrete BoS invalidation price). This prevents churning in flat/choppy markets where consecutive candles produce the same signal repeatedly.
4. ~~**Concurrent positions**~~ — resolved: only one real order allowed at a time (live or testnet API). Flow: generate all level candidates → score → pick single best → create order → block all new orders until it hits TP or SL. In backtesting each preset runs independently with its own one-order-at-a-time constraint.

### All design questions resolved (2026-04-26) — ready to implement

---

## Recommendation timing (agreed 2026-04-26)

Recommendations (and open-order checks, once the order module exists) are evaluated **only on new candle open** — i.e. when `on_candle_close` fires. There is no mid-candle re-evaluation. This avoids noise from intra-candle price movements and keeps the logic simple and deterministic.

---

## Backtesting / preset comparison flow (planned)

### Goal
Replay historical klines to compare different env-variable presets and identify the most efficient parameter combinations, without any real orders.

### Simulation rules
- Iterate klines one by one from index 0 to N (`klines[i]`): feed only `klines[0..i]` to the analyzer — no lookahead, simulating real-time conditions.
- On each candle close (`klines[i]`): for each preset, if no fake order is currently open → generate a recommendation → if one exists → open a fake order using `klines[i+1].open` as entry price.
- While a fake order is open for a preset → skip recommendation generation for that preset (no stacking).
- On subsequent candles, check `klines[j].high >= TP` (win) and `klines[j].low <= SL` (loss):
  - High hits TP only → win, close fake order
  - Low hits SL only → loss, close fake order
  - Same candle hits both (spike to both sides) → **loss** (conservative: assume SL triggered first)
  - Neither → keep order open

### Output per preset
- Total trades, win count, loss count, win rate
- Total profit (sum of TP distances on wins minus SL distances on losses)
- Average R:R achieved
- Max consecutive losses (drawdown signal)

### Implementation notes
- Each preset is a full set of env-variable overrides (dict)
- The analyzer must be re-initialised for each preset on each backtest run
- No live API calls during backtesting — use cached kline files only
- Store backtest results in `data/backtest_{preset_name}_{timestamp}.json` for later comparison

---

## Kline cache improvement (planned, not yet implemented)

### Goal
Store up to `KLINE_CACHE_LIMIT` candles (env var, default 5000) per symbol/timeframe/mode so the analyzer has deeper history to build L2/L3 structure from.

### File naming convention
`data/{SYMBOL}_{TIMEFRAME}_{MODE}.json`
Examples: `data/BTCUSDT_15m_test.json`, `data/XAUUSDT_1h_live.json`

Currently the file is named `data/{SYMBOL}_{TIMEFRAME}.json` (no mode suffix) — migration needed.

### Append logic on startup
1. Load existing cache file (if present)
2. Fetch latest klines from REST (up to `KLINE_LIMIT` candles)
3. Check for time gap: if `fetched[0].open_time > existing[-1].close_time + 1 candle_duration` → gap detected → discard existing, store only fetched
4. No gap → merge: append fetched candles that are newer than the last existing candle
5. Trim to last `KLINE_CACHE_LIMIT` candles
6. Write back to file

### New env variable
| Variable | Purpose | Default |
|---|---|---|
| `KLINE_CACHE_LIMIT` | Max candles to keep in the cache file | `5000` |

---

## Session 9 — `lower_high_sell` setting

### Problem analysed (session 8 carry-over)
In a DESCENDING trend, after a swing LOW is confirmed, the `else` branch of `getRecommendation()` fires `RISING_BELOW_LAST_HIGH` → BUY for the entire bounce. During the 75,726→77,750 bounce (Apr 28-29 2026), the supposed_next_high projection correctly computed ~77,750 but was never used as a SELL trigger. The SELL only fired ~500 pts below the optimal entry, after the swing HIGH was confirmed.

### Solution implemented
New `lower_high_sell: bool = False` setting — when True, fires a `DESCENDING_NEAR_LOWER_HIGH` SELL in the `else` branch when:
1. Trend is DESCENDING
2. `supposed_next_high < last_high.getHighValue()` (valid lower high projection)
3. `entry_price ≤ supposed_next_high`
4. `dist / range_size * 100 ≤ proximity_zone_pct` (within approach zone)

Signal parameters: TP = supposed_next_low, SL = last confirmed HIGH.

### Files changed
| File | Change |
|---|---|
| `bot/recommendation.py` | Added `DESCENDING_NEAR_LOWER_HIGH` to `RecommendationTypes` enum |
| `config/settings.py` | Added `lower_high_sell: bool` field + `LOWER_HIGH_SELL` env var (default False) |
| `bot/trend.py` | `getRecommendation()` + `getRecommendations()` — new `lower_high_sell` parameter; DESCENDING_NEAR_LOWER_HIGH logic added to `else` branch before existing RISING_BELOW_LAST_HIGH catch-all |
| `bot/recommendation_engine.py` | Passes `lower_high_sell=self._s.lower_high_sell` to `getRecommendation()` |
| `backtest_api.py` | Added `'lower_high_sell': False` to `DEFAULTS` and `lower_high_sell=bool(p['lower_high_sell'])` to `build_settings()` |
| `backtest.py` | Added 6 new presets: `lh_sell_prox10/15/20`, `lh_sell_trail15`, `lh_sell_prox15_trail15`, `lh_sell_prox15_cooldown` |

### Key design decisions
- Default `False` — all existing presets unaffected.
- SL = last confirmed HIGH (not supposed_next_high × some multiplier): if price breaks above the previous high, the lower-high thesis is invalidated.
- Range for proximity calculation = `last_high.getHighValue() − last_low.getLowValue()` — the full swing range, same metric used by the existing `whichIsCloser` proximity logic.
- The new signal takes priority over RISING_BELOW_LAST_HIGH in the `else` branch — if DESCENDING_NEAR_LOWER_HIGH fires, the BUY is suppressed. This avoids conflicting signals.

### Next step
Run backtest with new presets to see how many DESCENDING_NEAR_LOWER_HIGH / ASCENDING_NEAR_HIGHER_LOW signals fire, what the win rate is, and whether the Apr-29 77,750 entry is captured.

---

## Session 9 (continued) — `higher_low_buy` setting

Symmetric mirror of `lower_high_sell`. In the `if is_last_high is not None:` branch (last confirmed = HIGH), currently the catch-all fires `LOWERING_ABOVE_LAST_LOW` → SELL for the entire pullback. In an ascending trend that pullback forms a higher LOW — this setting fires BUY before confirmation.

**New signal**: `ASCENDING_NEAR_HIGHER_LOW`
- Condition: `higher_low_buy=True`, trend ASCENDING, `supposed_next_low > last_low.getLowValue()`, `entry_price ≥ supposed_next_low`, `dist / range_size * 100 ≤ proximity_zone_pct`
- BUY: TP = supposed_next_high, SL = last confirmed LOW

**Files changed** (additions on top of lower_high_sell changes):
- `bot/recommendation.py` — `ASCENDING_NEAR_HIGHER_LOW` enum value
- `config/settings.py` — `higher_low_buy: bool` field + `HIGHER_LOW_BUY` env var
- `bot/trend.py` — `higher_low_buy` param on both methods; logic in `if is_last_high is not None:` branch
- `bot/recommendation_engine.py` — passes `higher_low_buy=self._s.higher_low_buy`
- `backtest_api.py` — default + build_settings wiring
- `backtest.py` — 6 `hl_buy_*` presets + 4 `pre_confirm_*` presets (both flags together)
| `FAILED_ORDER_COOLDOWN_CANDLES` | Candles to skip re-entry after same-signal SL hit | `2` |

---

### Session 10 — 2026-05-07: Risk module + dashboard cleanup

#### Critical bug fix: BUY signals were unreachable (`bot/trend.py`)

`getRecommendation()` had `if is_last_high is not None:` where `isLastPointHigh()` returns `True`, `False`, or `None`. Since `False is not None` is `True`, both "last swing = HIGH" and "last swing = LOW" entered the SELL-biased block. The BUY-biased `else` block only ran when `is_last_high is None` (no points at all) — which never happens in practice because we return early if there aren't enough points.

**Fix**: changed `if is_last_high is not None:` → `if is_last_high:` (one character).

**Impact**: Before fix: ~99% SELL across all symbols. After fix:
- BTCUSDT: 58% BUY / 42% SELL, best preset +2.57% pf=3.50 (was +1.16% pf=1.56)
- ETHUSDT: best preset +9.61% pf=3.35 (was +1.73% pf=1.39)
- SOLUSDT: best preset +8.36% pf=1.83 (was +6.11% pf=1.75)
- BNBUSDT: best preset +4.07% pf=4.19 (was +0.98% pf=1.57)
- XAUUSDT: best preset +2.19% pf=2.31 (was +2.35% pf=2.52 — slight change due to new mix)

Risk module leverage scores after fix: BTCUSDT 10×, XAUUSDT 9×, ETHUSDT 10×, SOLUSDT 9×, BNBUSDT 10×.

#### Dashboard cleanup
- **Orders table removed** from `PresetResultsPanel` (Backtest → Visualize Preset section). Hover state and `BacktestTradeList` import removed from the component. Chart still shows trade markers.
- **Stale symbol files deleted**: removed data/backtest files for DOGEUSDT and XRPUSDT which were no longer in the symbol registry.
- **Auto file cleanup on symbol deletion**: `DELETE /api/symbols/[symbol]` now calls `deleteSymbolFiles(symbol)` which removes `dashboard/public/backtest_results_{SYMBOL}.json`, `dashboard/public/results_{SYMBOL}.json`, `data/{SYMBOL}_15m*.json`, and all `data/backtest_{SYMBOL}_*.json` timestamped archives.

#### Symbol registry (from previous session — landed in this branch)
Settings page (`/settings`) allows adding and removing symbols. Registry stored in `symbol_registry.json` at project root. Temporary feature — will be disabled once paper trading starts.

#### Risk module — fully implemented on `feature/risk-module` branch

**Spec**: `docs/superpowers/specs/2026-05-07-risk-module-design.md`  
**Plan**: `docs/superpowers/plans/2026-05-07-risk-module.md`

**New files:**
- `config/risk_config.py` — `load_risk_config()` (creates file if missing, merges new defaults), `save_risk_config()` (atomic write), `DEFAULT_CONFIG`
- `risk_config.json` — project root, generated on first Python run; dashboard POST /api/risk saves here
- `dashboard/app/api/risk/route.ts` — GET returns `{ config, state }`; POST saves full config atomically
- `dashboard/app/risk/page.tsx` — full Risk page: sections A (global capital rules + balance tiers), B (per-symbol allocation weights), C (leverage controls), D (drawdown guard + reset button), E (live state polling)
- `tests/test_risk_config.py` — 4 tests
- `tests/test_risk_manager.py` — 17 tests

**Modified files:**
- `bot/risk_manager.py` — full replacement of old async-only stub. Now `threading.RLock`-based, re-entrant. Public interface: `update_balance()`, `can_open_sync()`, `get_leverage()`, `get_allocation()`, `notify()`, `reset_hard_stop()`, `snapshot()`, `async can_open()`
- `bot/backtester.py` — added `initial_balance` + `risk_config_path` params; `PresetResult` gains `balance_start`, `balance_end`, `drawdown_triggered`; `_run_preset()` tracks compound balance per preset and gates entries after hard-stop drawdown
- `backtest.py` — reads `backtest_initial_balance_usdt` from risk config; passes to `Backtester`
- `bot/paper_trader.py` — `can_open_sync()` gate at top of `_try_open()` before all other checks
- `paper_trade.py` — instantiates `RiskManager(mode="paper")`; passes to `PaperTrader`
- `dashboard/components/NavBar.tsx` — added Risk link between Create and Settings

**Key design decisions:**
- `threading.RLock` (not asyncio.Lock) — safe in both sync backtester and async paper trader; re-entrant so internal helpers can call each other
- True profit factor = `sum(positive profit_pct) / sum(abs(negative profit_pct))` from actual trade data — NOT potential win/loss pts
- 60 s TTL cache per symbol for performance scores — new backtests picked up automatically within 1 min
- Minimum 4 trades threshold for preset eligibility in scoring
- Drawdown warning auto-resets; hard stop is latched (requires manual reset via dashboard Reset button)
- `_pending_notify` pattern: store notification inside lock, fire `notify()` after releasing to avoid deadlock
- In backtest mode: `estimated_size_usdt=0` → only hard_stop and profit_factor checks apply (capital gates always pass)
- `risk_state.json` written atomically to `dashboard/public/`; polled by dashboard every 5 s

**Test results (21 tests total, all passing):**
- 4 `test_risk_config.py`: file creation, key merging, save/reload, corrupt-file fallback
- 17 `test_risk_manager.py`: tier selection, allocation, capital gate, drawdown guard, leverage formula, TTL cache, backtester compound balance

---

### Session 11 — 2026-05-07: Symbol Discovery + dashboard fixes

#### Symbol Discovery — fully implemented

**Plan**: `docs/superpowers/plans/2026-05-07-symbol-discovery.md`

**New files:**
- `bot/symbol_discovery.py` — `CandidateResult` dataclass + `SymbolDiscovery` class with 4 methods: `get_precandidates`, `get_fast_presets`, `compute_baseline`, `score_candidate`. Module-level `_DASHBOARD_PUBLIC = Path("dashboard") / "public"` (patchable in tests via `patch('bot.symbol_discovery._DASHBOARD_PUBLIC', tmp_path)`).
- `tests/test_symbol_discovery.py` — 10 tests, all passing. Filesystem isolation via `_DASHBOARD_PUBLIC` patch.
- `discover.py` — project-root CLI. Reads `data/discovery_config.json`, spawns `ThreadPoolExecutor`, writes `discovery_candidates.json` atomically. SIGTERM handled via `threading.Event`.
- `dashboard/app/api/discovery/run/route.ts` — `POST`: validates body, writes initial state, spawns `discover.py`, stores PID, updates status on `close`.
- `dashboard/app/api/discovery/cancel/route.ts` — `POST`: reads PID from state, sends SIGTERM.
- `dashboard/components/SymbolDiscovery.tsx` — collapsible controls, progress bar + cancel button, sortable candidates table with "Add" per row. Filters out candidates already in `availableSymbols`.
- `dashboard/app/api/_utils.ts` — shared `BOT_ROOT` and `isAlive(pid)` used by both symbols and discovery routes.

**Modified files:**
- `dashboard/app/api/symbols/_registry.ts` — removed inline `BOT_ROOT`/`isAlive`, now imports + re-exports from `../_utils`.
- `dashboard/lib/types.ts` — added `CandidateResult`, `DiscoveryState`, `DiscoveryCandidatesFile` interfaces.
- `dashboard/app/settings/page.tsx` — added `<SymbolDiscovery />` section after "Add Symbol".

**Key design decisions:**
- Discovery runs as a subprocess (same pattern as `backtest.py`) so Next.js stays responsive.
- Candidates disappear from the UI once added: `visibleCandidates` filters against `availableSymbols` (polled every 3s from `useSymbolContext`) — works both immediately and after page reload.
- `_DASHBOARD_PUBLIC` as module-level constant (not inside class) so tests can patch it without touching class internals.
- `build_from_klines` does NOT set `_current_price` — must pass `float(klines[-1][4])` explicitly as `current_price` argument to `export()`.

#### Strategy page fixes

**Problem 1 — Newly added symbols showed "No data" forever.**
Root cause: `useSymbols.ts` fetched `symbols.json` only once on mount. New symbols added via POST /api/symbols were never reflected.
Fix: Changed to poll every 3000ms with `?t=${Date.now()}` cache-buster.

**Problem 2 — "No data for THETAUSDT yet" even after polling fix.**
Root cause: No `results_{symbol}.json` existed for newly added symbols, and fetching a 404 kept the page in "no data" state permanently.
Fix: Added `writePlaceholderResults(symbol)` (fire-and-forget `void`) in `POST /api/symbols` immediately after registry write. It inherits mode/timeframe from any existing results file, fetches live price from `fapi.binance.com/fapi/v1/ticker/price`, and writes the placeholder only if file doesn't already exist.

**Problem 3 — Existing symbols (7 of them) had no results files; trend data blank.**
Root cause: `Analyzer.build_from_klines()` processes klines but never sets `_current_price` — only `add_candle()` does. Placeholder's `current_price=0` caused display issues. Results files for non-BTCUSDT symbols were never generated.
Fix: Generated strategy data for all 7 symbols by running `backtest.py`. Also fixed exporter to pass `float(klines[-1][4])` as explicit `current_price`.

**Problem 4 — Trend table and chart showed no data despite results file existing.**
Root cause: `selectedLevel` in localStorage was `0` (set during placeholder era when `trend_levels` was empty and `Math.max(...[])` returned `-Infinity`, then stored as `0`). With real data (levels 1–3), the filter `t.level <= 0` excluded everything.
Fix in `app/page.tsx`:
```javascript
setSelectedLevel(prev => {
  if (d.trend_levels.length === 0) return 0
  const max = Math.max(...d.trend_levels.map(t => t.level))
  const min = Math.min(...d.trend_levels.map(t => t.level))
  if (prev === null || prev < min) return max
  return prev
})
```
Also added "Waiting for bot analysis…" intermediate state when `klines.length === 0 && trend_levels.length === 0`.

#### Symbol switcher improvements

- **NavBar wrapper**: `<div className="ml-auto max-w-[50%] min-w-0">` — caps switcher to half the nav width.
- **Selected symbol pinned left**: Rendered as a `Btn` outside the scroll container; thin gray divider separates it from the rest.
- **Remaining symbols scroll horizontally**: wrapped in `overflow-x-auto scrollbar-none` div.
- `scrollbar-none` utility added via Tailwind; `scrollbarWidth: 'none'` inline style as cross-browser fallback.

#### Bug fixed: wrong import path in `_registry.ts`
`_registry.ts` is at `dashboard/app/api/symbols/` → `_utils.ts` is one level up at `dashboard/app/api/`. Used `../_utils` (not `../../_utils`).

---

## Session: 2026-05-08 — Order Execution & Infrastructure (feature/test-live-preparation)

**Branch**: `feature/test-live-preparation`
**Plan**: `docs/superpowers/plans/2026-05-08-order-execution-and-infrastructure.md`
**Spec**: `docs/superpowers/specs/2026-05-08-order-execution-and-infrastructure-design.md`

### New modules

- **`bot/system_log.py`** — Rolling 100-entry JSON log writer. Atomic tmp→rename writes. `SystemLog(path)` with `write(level, title, body, source)`.
- **`bot/notifier.py`** — `Notifier(log_path, alert_path, telegram_token, telegram_chat_id)`. Routes warning/emergency alerts to `alert_state.json`, logs all events to system log, sends Telegram if configured. Never raises. `send_test() -> (bool, str)`.
- **`bot/mode_manager.py`** — `ModeManager(notifier=None)`. Persists mode to `data/bot_mode.json`. 2s command poll loop reads `data/bot_command.json`, dispatches `switch_mode`/`stop_bot`/`test_telegram` commands. `current_mode` attribute.
- **`bot/order_executor.py`** — `OrderExecutor(mode, settings, risk_manager, notifier)`. Per-symbol asyncio.Lock. State machine: IDLE/PLACING/OPEN/PARTIAL_EXIT/CLOSED. `place_order()` and `close_all_orders_at_market()`. Exchange calls are stubs — must be wired to real Binance API.
- **`bot/virtual_tracker.py`** — `VirtualTracker(mode, orders_path, efficiency_path)`. Seeds from backtest results. Tracks `total_winning_usdt` and `trade_count` per (symbol, preset). `best_preset(symbol)` requires ≥4 trades.
- **`config/risk_config.py`** — Extended with new fields: `telegram`, `min_balance_usdt`, `consecutive_failure_threshold`, `test_starting_balance_usdt`, `max_leverage`, `price_stale_threshold_s`.

### Modified modules

- **`bot/risk_manager.py`** — Renamed `"paper"` → `"test"` throughout. Added optional `Notifier` param. Added `min_balance_usdt` floor check in `update_balance()`.
- **`bot/data_feed.py`** — Fixed `== 'testnet'` → `== 'test'`. Added `reinit(mode, api_key, api_secret)` for runtime mode switching.
- **`config/settings.py`** — Default mode `'testnet'` → `'test'`. Legacy alias `testnet` → `test` with warning. Removed `LIVE_MODE_CONFIRMED` guard.
- **`backtest.py`** — Added `--mode` CLI arg. Fixed `== 'testnet'` → `== 'test'` in cache path.
- **`main.py`** — Wired Notifier, ModeManager, RiskManager, OrderExecutor, VirtualTracker. Obligatory startup backtest gate (exits on failure). Mode switch and stop callbacks. poll_loop runs as asyncio task alongside heartbeat.

### Deleted

- `bot/paper_trader.py`, `paper_trade.py` — replaced by OrderExecutor
- `dashboard/app/paper/` — replaced by new dashboard sections

### Dashboard additions

- **`dashboard/app/settings/page.tsx`** — Start/Stop Bot controls, Trading Mode switcher, Telegram Alerts section (test button), UI Preview section.
- **`dashboard/components/ModeBadge.tsx`** — Shows current mode + RUNNING/STOPPED, polls `bot_state.json` every 10s. Renders in NavBar left side.
- **`dashboard/components/AlertBanner.tsx`** — Shows undismissed warning/emergency alerts above NavBar. Dismissible. Polls `alert_state.json`.
- **`dashboard/app/log/page.tsx`** — System log page with level filter. NavBar shows unread badge count.
- **`dashboard/app/api/bot/start/route.ts`** — POST: spawns `main.py` detached.
- **`dashboard/app/api/bot/stop/route.ts`** — POST: writes stop command, polls 10s, SIGTERM fallback.
- **`dashboard/app/api/mode/route.ts`** — GET/POST mode switching with 60s poll.
- **`dashboard/app/api/alerts/dismiss/route.ts`** — POST: adds alert ID to dismissed list.
- **`dashboard/app/api/log/route.ts`** — GET: serves system log reversed.
- **`dashboard/app/api/telegram/test/route.ts`** — POST: writes test_telegram command, polls 15s.

### File-based command channel

Bot polls `data/bot_command.json` every 2s. Dashboard writes command with UUID. Bot writes result to `data/bot_command_result.json`. Dashboard polls for matching UUID. SIGTERM fallback after timeout.

### Mode model

- **`test`** → `testnet.binancefuture.com` (paper money, real-ish market data)
- **`live`** → `fapi.binance.com` (real money, real orders)
- Mode is runtime-switchable via dashboard. Switching closes all orders and reruns backtest before accepting new orders.

### Obligatory backtest gate

Every bot start and every mode switch runs `backtest.py --mode {mode}` as a subprocess before any orders can be placed. If it fails, the bot exits (startup) or aborts the switch (mode change) with an emergency alert.

### Key decisions

- `subprocess.run` for startup backtest (blocking before event loop hot path) — acceptable.
- `asyncio.to_thread(subprocess.run, ...)` for mode-switch backtest — non-blocking.
- OrderExecutor exchange calls left as stubs until testnet API wiring is separately scoped.
- VirtualTracker `_MIN_TRADES = 4` guard — prevents best_preset() from picking noisy results.
