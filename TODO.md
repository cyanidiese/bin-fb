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

- [ ] Max position size per trade (% of account or fixed USDT)
- [ ] Max concurrent open positions
- [ ] Daily loss limit with bot pause
- [ ] Leverage validation on startup (warn above threshold)

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
- [ ] Auto-refresh — poll results.json every N seconds without manual reload
- [ ] Account info panel — balance, available margin, unrealised PnL via Binance REST

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
