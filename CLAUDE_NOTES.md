# CLAUDE_NOTES.md — Binance Futures Bot Session Log

## Last updated: 2026-05-01 (session 7)

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
| Risk management module | not started |
| Tests | not started |
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

## Next steps
1. ~~**Dashboard auto-refresh**~~ — done (polls every 15s with cache-buster)
2. ~~**Dashboard backtest page**~~ — done (`/backtest` route, summary table + per-preset trade drill-down)
3. ~~**Run real backtest**~~ — done; 4 rounds of preset tuning on testnet 15m data
4. ~~**Old TrendAnalyzer ideas**~~ — done: candle-direction priority, SL-adjust-to-RR, max_profit_pct, SELL SL ×1.5
5. ~~**Direction-based cooldown + global pause**~~ — done (candle-based, not time-based)
6. ~~**OrderManager (live order structure + startup reconciliation)**~~ — done (`bot/order_manager.py`)
7. **Account info panel** — balance, available margin, unrealised PnL via Binance Futures REST
8. **Risk management module** — position sizing, daily loss limit, leverage check on startup
9. **Order placement** — wire `OrderManager` into main.py; requires risk module for sizing

### Partial take — real orders (Phase 4 note)
For live/testnet orders, partial take requires a **trailing stop** on the exchange side (e.g. a trailing-stop-limit order that activates once price reaches `partial_price`). This is not trivial via Binance API. Deferred to Phase 4 order manager design.

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
| `FAILED_ORDER_COOLDOWN_CANDLES` | Candles to skip re-entry after same-signal SL hit | `2` |
