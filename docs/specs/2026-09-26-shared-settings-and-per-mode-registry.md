# Shared settings file and per-mode symbol registry

Date: 2026-09-26. Follows `2026-09-26-per-mode-risk-config.md` (deployed the same day).

## What

1. **Symbol roster shared, symbol decisions per mode.** `symbol_registry.json` splits into
   - `symbol_registry_shared.json` — `symbols` (the roster) and `status` (backtest runs);
   - `symbol_registry_test.json` / `symbol_registry_live.json` — `disabled`, `paused`,
     `disabled_ranks`, `weights`, `leverage_overrides`.
   Adding/removing a symbol affects both modes (it is one list). Disabling, pausing,
   rank-disabling, weights and leverage overrides are per mode.
2. **Shared risk settings get their own file**, `risk_config_shared.json`. Keys in it are
   the same for both modes; everything else stays in `risk_config_{mode}.json`.

## Why

User requirement (2026-09-26): symbols shared between modes; Telegram-like settings the
same for all modes; disable, weights, leverage etc. per mode. User chose **shared** for the
strategy rules (signal filters and preset ranking).

Problems with today's single registry (measured on the server 2026-09-26):
- 6 symbols disabled on **testnet** evidence are disabled on the live mirror too. A disabled
  symbol gets no rank-1 virtual slot (`virtual_order_simulator.py:249`), so there is no
  "would have traded" track on real live charts for 6 of 22 symbols — the evidence the
  go-live plan depends on — and it cannot be collected without re-enabling real test orders.
- At a future `bot_mode` switch the primary would trade real money with a testnet-derived
  disabled list and testnet-derived leverage overrides (lot_constraint_detector reads the
  testnet exchange).
- `symbol_registry.json` is tracked by git; the Sep 14 registry reset that re-disabled REZ,
  ETHFI and AVAX came from that. New files are gitignored.
- The bot's `_persist()` rewrites `status` (dashboard-owned backtest state) on every
  decision, racing the dashboard. With the roster in its own file the bot never writes it.

## The rule: what is shared

**Shared = anything that shapes signals, preset ranking or virtual/backtest accounting,
plus process settings.** Two instances with different values here would be measuring
different strategies, and test-vs-live Profit% would stop being comparable.
**Per mode = everything that decides real orders and money.**

`SHARED_KEYS` (Python `config/risk_config.py`, TS `dashboard/app/api/_risk-config.ts`):

| Group | Keys |
|---|---|
| Process | `telegram`, `telegram_notify_interval_s`, `emergency_repeat_interval_s`, `warning_repeat_interval_s`, `analysis_log_enabled`, `analysis_log_max_mb`, `analysis_log_backups` |
| Backtest method | `startup_backtest`, `backtest_klines`, `backtest_initial_balance_usdt`, `backtest_seed_leverage_factor`, `backtest_entry_slippage_pct` |
| Virtual accounting | `virtual_max_age_candles`, `slippage_model_enabled`, `slippage_default_pct`, `slippage_min_samples`, `slippage_per_symbol` |
| Signal filters | `global_min_rr`, `global_max_rr`, `global_min_sl_pct`, `entry_zone_max_pct`, `global_trend_regime_filter`, `global_trend_regime_lookback`, `global_blocked_signal_types`, `global_max_level`, `global_correction_weight`, `global_enforce_parent_alignment`, `per_symbol_settings` |
| Preset ranking | `preset_blocklist`, `ranking_window_size`, `min_trades_for_ranking`, `min_trades_for_ranking_per_symbol`, `preset_hysteresis_pct`, `preset_cooldown_trades` |

Per mode (not listed = per mode): weights/allocation (`symbol_weights`, `scenario`,
`bgf_top_n`, `tats_min_weight`, `use_allocation_weighting`, `weight_rebalancer`), leverage
and size (`base_leverage`, `max_leverage`, `max_leverage_level`, `symbol_leverage`,
`balance_tiers`, `max_trade_pct`, `min_balance_pct`, `max_order_notional_usdt`), loss limits
(`drawdown_*`, `max_loss_*`, `max_peak_jump_pct`), real-order gates
(`min_profit_factor`, `tats_min_profit_usdt`, `tats_degradation_max_drop_pct`,
`virtual_only_floor`, `trading_blackout_hours`, `substitution_enabled*`), execution
(`consecutive_failure_threshold`, `price_stale_threshold_s`, `close_positions_on_stop`),
`locked_presets`, and every registry decision.

## Chosen approach

### Risk config
- **Read** (Python `load_risk_config`, TS `readRiskConfig`):
  `DEFAULTS ⊕ (test file if live) ⊕ own mode file ⊕ pick(shared file, SHARED_KEYS)`.
  The shared file wins for shared keys, so a stale copy in a mode file can never take
  effect. If the shared file is missing, the mode files' copies apply (deploy order and
  rollback stay safe).
- **Write (dashboard)**: shared keys → `risk_config_shared.json`, *and* mirrored into both
  mode files so each mode file stays a complete snapshot (rollback to the previous image
  reads correct values). Other keys → the selected mode's file only. This replaces the
  `BOT_WIDE_KEYS` "write to both" mechanism (`BOT_WIDE_KEYS` ⊂ `SHARED_KEYS`).
- **Write (Python)**: only the weight rebalancer writes (its mode file, per-mode keys).
  Unchanged.

### Registry
- `SymbolRegistry(seed_symbols, mode=..., read_only=...)` with
  `roster_path = symbol_registry_shared.json`, `state_path = symbol_registry_{mode}.json`.
- **Load**: roster ← shared file, else legacy `symbol_registry.json`, else `.env` seed.
  State ← own mode file, else (live) the test file, else the legacy file's fields.
- **Persist**: decisions write the state file only. The roster file is written only by
  `add_symbol`/`remove_symbol` (no bot callers today — the dashboard owns the roster).
  Mirror (`read_only`) writes nothing, as now.
- `reload_from_disk()` reads both files each candle.
- `main.py` passes the already-resolved `_cfg_mode` (primary: bot mode, mirror: opposite).
- Readers of the roster only (`config/settings.py::load_symbols`, `discover.py`,
  `sweep_range_position.py`, dashboard clear-history) read the shared file with legacy
  fallback. `sweep_range_position.py` also reads `disabled` from the test state file.

### Dashboard
- `app/api/symbols/_registry.ts`: `readRoster`/`writeRoster` (also keeps
  `public/symbols.json` in sync), `readSymbolState(mode)`/`updateSymbolState(mode, fn)`
  with in-memory seeding (live ← test ← legacy), `readRegistry(mode)` = roster ⊕ state for
  existing consumers.
- `GET /api/symbols?mode=` → roster ⊕ that mode's state (default bot mode) plus
  `modes: {test, live}` state for the Settings page.
- `enable`, `disable`, `rank-disable`, `enable-all` take `?mode=` (default bot mode).
- Trades page: disabled banner, enable buttons, rank toggles and picker red dots follow
  the viewed instance's mode (`/api/trades` reads that mode's state).
- Settings → Symbol Registry: each row shows Test and Live status side by side with its own
  enable/disable button; add/remove stays one action for both.
- Risk page / Settings: shared fields labelled "shared by both modes".

### Deploy
`scripts/split_shared_and_registry.py` (host, stdlib, dry run by default, `--apply`,
never overwrites) — run after the bots stop, before rebuild (single-file bind mounts):
- `risk_config_shared.json` ← SHARED_KEYS present in `risk_config_test.json`; reports any
  shared key whose live value differs (today: none).
- `symbol_registry_shared.json` ← legacy `symbols`, `status`.
- `symbol_registry_test.json` ← legacy decision fields; `symbol_registry_live.json` ← copy
  of test. Day one = identical behaviour.
- `docker-compose.yml`: mount the four new files rw in `bot`/`dashboard`, `:ro` in
  `bot_mirror`.
- Legacy `symbol_registry.json` stays tracked and frozen (rollback path). After this ships
  nothing reads or writes it, so the "deploy wipes the roster" hazard ends.

## Rejected

- **Roster per mode** (full registry copy per mode): the user wants one symbol list;
  two copies drift (the primary's `_persist` would write a stale roster back).
- **Shared keys stored in both mode files, test file canonical** (no third file): smaller,
  but "the test file is the home of live's Telegram token" breaks down once live is the
  primary. An explicit shared file says what it is.
- **Strategy rules per mode / live-inherits-test**: user chose shared.
- **Moving the configs into a mounted directory** (avoids single-file-mount hazards):
  worth doing some day, but it would move the files deployed today; separate change.

## Touch points

`config/risk_config.py`, `bot/symbol_registry.py`, `main.py`, `config/settings.py`,
`discover.py`, `sweep_range_position.py`, `docker-compose.yml`, `.gitignore`,
`scripts/split_shared_and_registry.py` (new), `dashboard/app/api/_risk-config.ts`,
`dashboard/app/api/risk/route.ts`, `dashboard/app/api/symbols/_registry.ts`,
`dashboard/app/api/symbols/route.ts`, `.../symbols/[symbol]/route.ts`,
`.../symbols/[symbol]/{enable,disable,rank-disable}/route.ts`,
`.../symbols/enable-all/route.ts`, `dashboard/app/api/trades/route.ts`,
`dashboard/app/api/bot/clear-history/route.ts`, `dashboard/app/trades/page.tsx`,
`dashboard/components/settings/SymbolRegistry.tsx`, Risk/Settings shared-field labels,
tests, `.claude/skills/bfb-config`, `.claude/skills/bfb-deploy`.

## Risk flags

- **Touches the real-order gate** (`is_disabled`, `is_symbol_paused`). Mitigation: the test
  state file is a byte copy of today's decisions; verified before restart by diffing the
  effective disabled/paused sets.
- **After deploy, a test disable no longer disables live** — intended. The Settings row
  shows both modes so it is a visible choice.
- **Rollback**: the previous image reads legacy `symbol_registry.json`, frozen at the
  split. Decisions made after the split must be re-applied by hand after a rollback.
- Needs a graceful restart of both bots.
