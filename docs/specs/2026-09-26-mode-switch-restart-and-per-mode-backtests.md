# Coordinated mode switch (close, restart) and per-mode backtest results

Date: 2026-09-26. Follows `2026-09-26-shared-settings-and-per-mode-registry.md`.

## Why

An audit of every file both bots and the dashboard read or write (2026-09-26) found that
config, orders, balances, caches and efficiency are split by mode, but two things are not.

### 1. Flipping the mode leaves the system inconsistent

The dashboard's Trading Mode button writes `data/bot_mode.json` and nothing else
(`/api/mode`; `TradingMode.tsx` says so). Nothing sends the `switch_mode` command, so
`main.py::on_switch_mode` is dead code — and it was also incomplete (never re-pinned the
risk config mode, never rebuilt the registry, resolved locks with the startup mode).
What actually happens today:
- the primary keeps trading the old mode until someone restarts it;
- the mirror (`_mirror_watch`) restarts within ~1 min as the opposite of the NEW mode —
  the mode the primary is still running — so both instances write the same `_test` files
  (virtual orders, preset efficiency, Profit% store, kline cache);
- the dashboard re-runs every backtest for the new mode into the primary's file (item 2).

### 2. Backtest results are keyed by instance, not by market

`backtest_results_{SYM}.json` = "whoever is primary". RiskManager derives real-order
leverage and cross-symbol allocation from it; the virtual tracker seeds from it.
- After a switch the primary sizes from the previous market's backtest until one reruns.
- The dashboard can only backtest the primary's file, so the mirror's are never refreshed:
  all 15 `_live` files are 19 days old; ARB, BTC, EGLD, ENA, ETH, LINK, LTC have none (the
  mirror logs "no preset seeds and base leverage" every start). Add Symbol backtests one mode.
- `/api/refresh-scores` reads every `backtest_results_*` file including `_live` ones and
  patches the primary's `risk_state.json` with whichever it read last.
- `backtest.py` exports the chart file by instance too, so a backtest of the other mode run
  from the dashboard would overwrite the primary's chart.
- `backtest.py` always fetches PRODUCTION klines but saves them into the mode's cache, so
  `data/{SYM}_15m_test.json` — the testnet bot's own analyzer history — got production
  candles mixed with its websocket's testnet candles (APTUSDT: every candle before Sep 7
  identical to production, after that mostly different).
- A live-mode backtest from the dashboard would crash: `load_settings()` demands live API
  keys, which a backtest never uses.
- `data/rate_limit_state.json` is keyed by instance, but bans are per exchange host.

## Chosen approach

### Item 1 — the primary switches by closing everything and restarting (user's choice)

User decision: on a flip, **close all open real positions at market, then restart**.

- `data/primary_mode.json` — written by the primary at startup: the mode it is actually
  running. The mirror derives its mode from this file (fallback `bot_mode.json` when absent),
  so it only follows once the primary has really switched. No collision window.
- `main.py::_primary_mode_watch` (primary only), every 30 s:
  1. `bot_mode.json` valid and ≠ running mode → **switch pending**: new real orders stop
     at once (the placement pass gets no candidates), Telegram notice.
  2. Confirmed on two consecutive checks (as the mirror does), then:
  3. **Refuse** if the target mode cannot run (its API keys are missing from the env): emergency
     notice, pending cleared, the bot keeps trading the current mode. Re-armed when
     `bot_mode.json` changes again.
  4. Close all virtual positions, close all real positions at market, re-check the exchange
     is flat (up to 3 attempts). Not flat → emergency notice, stay pending (still no new
     orders), retry next cycle. Never exit holding positions of the old mode.
  5. Flat → write open positions, notify, exit 0. Docker (`restart: unless-stopped`) starts
     a fresh process in the new mode: config, registry, every data path, backtest seeds,
     endpoints resolved from scratch by the normal startup path.
- Removed: `on_switch_mode`, `ModeManager.switch_mode`, the `switch_mode` command (answers
  "not supported — use the Trading Mode button").
- Dashboard dialog describes the real behaviour.

### Item 2 — backtest results and archives keyed by mode

- `backtest_results_{SYM}_{mode}.json` for every instance and every run
  (`bot/instance_paths.py::backtest_results_name(symbol, mode)`). Read side:
  `backtest_results_path(dir, symbol, mode)` falls back to the legacy unsuffixed file for
  **test only** (it has always been the testnet primary's) — never across markets.
- Chart export: `backtest.py` writes the chart of the instance that runs that mode (primary
  if `mode == primary's running mode`, else the mirror's name).
- Archives: `data/backtest_{SYM}_{mode}_{TS}.json`, keep-5 per symbol per mode.
- Klines: production candles fetched by a backtest go into the production cache
  (`{SYM}_15m_live.json`, same market the mirror streams); the backtest reads that cache in
  both modes — the data it has always actually used. The testnet cache is written only by
  the testnet bot.
- `load_settings(symbol, require_keys=False)` for backtests.
- Dashboard: `GET /api/backtest-results?symbol=&mode=` (resolves the file, test fallback);
  Backtest page and Create page read through it; Backtest page gets a Test/Live selector
  and runs `/api/run-backtest` with that mode; Add Symbol backtests test then live;
  `refresh-scores`, `telegram/test`, `_preset-names`, symbol delete use the mode-keyed names.
- `rate_limit_state_{mode}.json` for both instances.
- `scripts/split_shared_and_registry.py` also copies `backtest_results_{SYM}.json` →
  `backtest_results_{SYM}_test.json` (never overwrites).

## Rejected

- **Drain before switching** / **restart leaving positions**: user chose close-at-market.
- **Fixing `on_switch_mode` in place**: a hot swap of ~15 mode-scoped objects can be left
  half-switched by one failure; a fresh process cannot. The mirror already works this way.
- **Mirror reading `bot_state.json`** for the primary's mode: it is a dashboard heartbeat,
  rewritten every 10 s with `running` flags; a dedicated file written once per start is simpler.
- **Backtesting test mode on testnet klines**: would change what every preset ranking is
  based on — a strategy-data change needing its own decision.

## Touch points

`main.py`, `bot/mode_manager.py`, `bot/instance_paths.py`, `bot/risk_manager.py`,
`bot/telegram_menu.py`, `bot/symbol_discovery.py`, `backtest.py`, `config/settings.py`,
`scripts/split_shared_and_registry.py`, dashboard: `app/api/backtest-results/route.ts` (new),
`app/api/run-backtest/route.ts`, `app/api/symbols/route.ts`, `app/api/symbols/[symbol]/route.ts`,
`app/api/refresh-scores/route.ts`, `app/api/telegram/test/route.ts`,
`app/api/trades/_preset-names.ts`, `app/backtest/page.tsx`, `app/create/page.tsx`,
`components/settings/TradingMode.tsx`, tests.

## Risk flags

- **Closes real positions at market** — only after an explicit mode flip, confirmed twice,
  and only when the target mode can run. Refusal path keeps trading unchanged.
- The switch blocks new real orders from the first sighting (~30–60 s before closing).
- First deploy: the primary writes `primary_mode.json` at startup; until then the mirror
  falls back to `bot_mode.json` (today's behaviour).
- Backtest results rename: Python and dashboard fall back to the legacy file for test, and
  the split script copies it, so no reader sees a gap.
