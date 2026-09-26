# Per-mode risk config — risk_config_test.json / risk_config_live.json

Date: 2026-09-26.

## What

Test and live trading each get their own risk configuration file:
`risk_config_test.json` and `risk_config_live.json`. Every setting the Risk page edits
(weights, tiers, leverage, drawdown, scenario, max-loss, ranking, …) is saved to the file
of the mode being edited. Each bot instance reads the file of the mode it trades.

## Why

User requirement: setting a weight from 8 to 9 in one mode must leave the other mode at 8.
Today one `risk_config.json` serves both instances (the mirror mounts it read-only), so the
Risk page shows and saves one set — only `locked_presets` is split per mode.

Also a prerequisite for going live: testnet-tuned weights and limits must not silently
become live-money settings, and switching `bot_mode` one day must bring each mode's own
settings with it.

## Chosen approach

1. **Files.** `risk_config_{mode}.json` at the repo root, gitignored, like the old file.
   The mode is the *trading mode*, not the instance: the primary reads
   `risk_config_{bot_mode}`, the mirror reads `risk_config_{opposite}`. Switching
   `bot_mode` therefore swaps configs automatically on restart — nothing to migrate.
2. **Seeding** (the user's rule: a missing live config comes from test):
   - `risk_config_test.json` missing → copy of the legacy `risk_config.json`.
   - `risk_config_live.json` missing → copy of the test file.
   - `locked_presets` keeps its nested `{mode: {...}}` shape; each file carries only its
     own mode's entry (`{"test": {...}}` / `{"live": {...}}`), seeded from the legacy
     file's matching entry. The existing `locked_presets_for(cfg, mode)` helpers work
     unchanged, and test locks can never leak into live.
   - Done by `scripts/split_risk_config.py` on the host BEFORE containers restart (Docker
     single-file bind mounts need the file to exist, or Docker creates a directory).
3. **Key-level fallback on read.** Loading live = `DEFAULTS ⊕ test file ⊕ live file`: any
   key the live file lacks (a key added to test later) is read from test. Loading test =
   `DEFAULTS ⊕ test file`. Because locks are nested per mode, the fallback cannot import
   test's locks.
4. **Bot-wide keys** — `telegram`, `telegram_notify_interval_s`, `startup_backtest` — are
   about the bot process, not a trading mode. They are still stored in both files (so
   each file is complete on its own), and every dashboard write of them goes to BOTH.
5. **Symbol roster** is shared (`symbol_registry.json`), so adding/removing a symbol adds/
   removes its weight entry in both files.
6. **Python.** `config/risk_config.py` gains `set_active_mode(mode)` and
   `config_path(mode=None)`. `load_risk_config()` / `save_risk_config()` with no path use
   the active mode (fallback: `TRADING_MODE` env, else test). `main.py` sets the active mode
   first thing in `run()` (primary: bot_mode.json; mirror: opposite), before its first
   config read. Explicit-path callers switch to `config_path(mode)`: RiskManager,
   WeightRebalancer, backtest.py/Backtester, symbol_discovery. Loading never creates a
   missing file (the mirror's mount is read-only).
7. **Dashboard.** New `app/api/_risk-config.ts` (paths, read with fallback, write, write
   bot-wide keys to both, seed). Every reader/writer takes a mode:
   - `/api/risk` GET/POST `?mode=` (default: bot mode). Full save → that mode's file,
     merged onto a fresh read, never taking `locked_presets` from the body. Partial saves
     of bot-wide keys → both files.
   - `lock-preset`, `/api/trades` (locks), `symbol-scores` (locks) → the viewed mode's file.
   - `symbols` add/remove → both files. `telegram/test` → bot mode's file.
   - Risk page: the Instance switcher now selects the config being edited AND the live
     state shown; switching reloads that mode's config. The header names the file.
   - Trades page allocation labels read the viewed mode's config.
8. **Legacy `risk_config.json` is left untouched** as the seed and the rollback path (the
   previous image still reads it). Nothing writes it any more.

## Rejected

- **One file with every key nested per mode** (`{"test": {...}, "live": {...}}`): every
  reader in Python and TS would change shape at once; the user asked for suffixed files
  like the rest of the per-mode data.
- **Shared base file + per-mode overrides**: two places to look for any value, and a
  base-file edit would silently change both modes — the exact problem being fixed.
- **Per-instance files** (`risk_config.json` / `risk_config_live.json` by instance, like
  `risk_state`): would follow the instance, not the market, so a `bot_mode` switch would
  put testnet settings on live money.
- **Separate file for bot-wide keys**: a third config file for three keys; syncing them
  into both files keeps "one file = one complete mode".

## Touch points

`config/risk_config.py`, `main.py`, `bot/risk_manager.py`, `bot/weight_rebalancer.py`
(via main), `backtest.py`, `bot/symbol_discovery.py`, `docker-compose.yml`, `.gitignore`,
`scripts/split_risk_config.py` (new), `dashboard/app/api/_risk-config.ts` (new),
`dashboard/app/api/risk/route.ts`, `.../risk/lock-preset/route.ts`,
`.../symbols/route.ts`, `.../symbols/[symbol]/route.ts`, `.../telegram/test/route.ts`,
`.../trades/route.ts`, `.../trades/symbol-scores/route.ts`, `dashboard/app/risk/page.tsx`,
`dashboard/app/trades/page.tsx`, settings components (bot-wide keys), tests.

## Risk flags

- **Touches the live trading config path.** Mitigations: seeding copies the current values
  byte-for-byte (the test file equals today's config), so the primary's behaviour is
  unchanged on day one; verified before restart by diffing effective configs.
- **Deploy order matters:** run the split script on the host, then rebuild all three
  services (bots need a graceful stop). If the new files were missing, Docker would create
  directories in their place.
- **Rollback:** the old `risk_config.json` is untouched; reverting the commit and
  rebuilding restores the previous behaviour exactly.
- **Weight rebalancer** (primary only) now writes its own mode's file; the mirror still
  never writes config.
