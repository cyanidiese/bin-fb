# Live-Mode Virtual Instance, Running Parallel to Test

**Date:** 2026-09-06
**Status:** spec — awaiting review, no code written
**Related:** TODO.md "Idea 2", memory `project_live_transition`

---

## What it does

Runs a second bot instance against the **live Binance market**, in virtual-only mode,
alongside the existing testnet bot. It places no real orders and holds no credentials.
Its only job is to collect preset statistics on real charts.

Both instances watch the same 15 symbols and read the same config, so the only variable
between them is the market data itself.

## Why

Testnet charts are not live charts. Every preset statistic the bot has accumulated —
9,699 virtual trades on APTUSDT alone, and the `sum(recent_trades[-10:])` ranking that
picks which preset trades real money — was learned on testnet prices. None of it is
known to transfer.

Switching to real money without that evidence means starting preset selection from
scratch, with real money, on a book that is currently losing (−243 USDT over the last
14 days). The purpose of this instance is to have the evidence *before* the switch, not
after.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Symbols | the same 15 as test | one WebSocket, directly comparable, ~4 weight/candle |
| Visibility | dashboard test/live toggle | the comparison is the deliverable; it has to be readable |
| Config | shared `risk_config.json` + `symbol_registry.json` | identical rules on both sides, so a difference means the market |
| Telegram | shared chat, `[LIVE-VIRTUAL]` prefix | one bot per event; must be obvious which is speaking |
| Deployment | second container, same image | isolation |

## Chosen approach: second container, same image

`docker compose` gains a service using the existing image with different environment:
`TRADING_MODE=live`, `VIRTUAL_ONLY=1`, and **no API credentials**.

### Why this and not the alternatives

**Rejected — one process, two pipelines.** A bug in the experimental live pipeline could
take down the bot that is actually trading. `bot/analysis_log.py` also holds a
module-level `_handler` singleton that two pipelines would fight over, interleaving both
modes into one analysis file. The saving (one exchange-info fetch) does not justify
coupling an experiment to production.

**Rejected — periodic batch replay of live klines.** Cheapest option, but the virtual
simulator arms trailing stops on price *ticks* via `check_symbol_price()`, not only on
candle closes. Batch replay would produce systematically different exits from the test
instance, destroying the very comparison this exists to produce.

## Verified before writing this

Measured on the server 2026-09-06, not assumed:

- **A keyless client does exactly what we need.** `Client('', '')` serves
  `futures_klines` and `futures_exchange_info`, and refuses `futures_account` with
  *"API Secret required for private endpoints"* — refused by python-binance itself,
  before any request reaches Binance.
- **The virtual path needs no authenticated call.** `_virtual_lev()` (`main.py:186`)
  computes leverage from the registry override, the efficiency score and the scenario —
  all local. `fetch_leverage_brackets()` (401 without auth) feeds only the real order
  executor. `min_notional` comes from public `exchangeInfo`.
- **API cost is negligible.** ~4 request-weight per candle for 15 symbols against
  production's 2400/min, which currently sits idle at weight 1.
- **Disk is available.** `data/` is 144M (75M of it virtual rank files, bounded by
  `_MAX_CLOSED = 500`); 5.0G free. A parallel instance roughly doubles it to ~290M.

The keyless property is the core safety argument: the live instance is *structurally
incapable* of placing an order, not merely configured not to.

## Path collisions

Most persistence is already mode-suffixed and needs no work: kline cache,
`virtual_orders_rank{N}_{symbol}_{mode}.json`, `preset_efficiency_{mode}.json`,
`decision_log_{mode}.json`, `balance_history_{mode}.json`, `leverage_state_*_{mode}.json`,
`streak_state_{mode}.json`, `open_positions_{mode}.json`, `real_orders_{symbol}_{mode}.json`.

These are **not** suffixed and must be, or the two instances corrupt each other:

| path | current | consequence today |
|---|---|---|
| `dashboard/public/results_{symbol}.json` | `exporter.py:96` takes `mode` and ignores it | instances overwrite each other's chart data |
| `logs/bot.log` | `main.py:92` | interleaved, unreadable |
| `logs/analysis.jsonl` | `main.py:306` | both modes in one analysis file |
| `data/system_log.json` | `main.py:126` | mixed notification history |
| `dashboard/public/alert_state.json` | `main.py:127` | mixed alerts |
| `dashboard/public/symbols.json` | `exporter.py:125` | last writer wins (benign, both write the same list) |

## Touch points

**New**
- `docker-compose.yml` — second service, same image, `TRADING_MODE=live`,
  `VIRTUAL_ONLY=1`, no credentials
- `config/settings.py` — `virtual_only: bool` from `VIRTUAL_ONLY`

**Modified**
- `main.py` — when `virtual_only`: skip `fetch_leverage_brackets()`, skip
  `_get_fresh_balance()` and the placement loop, skip `OrderExecutor` order paths.
  Mode-suffix `bot.log`, `analysis.jsonl`, `system_log.json`, `alert_state.json`.
- `bot/exporter.py` — `results_{symbol}_{mode}.json`; `mode` is already a parameter
- `bot/notifier.py` — optional instance label, prefixed to titles
- `dashboard/` — API routes read a mode parameter; a test/live toggle in the UI

**Explicitly unchanged**
- `bot/virtual_order_simulator.py`, `bot/virtual_tracker.py`, `bot/analyzer.py`,
  `bot/fake_order.py` — the simulation must stay byte-identical between modes or the
  comparison is meaningless. If a change is needed here, that is a signal the design is
  wrong.

## Risks

- **The dashboard is used daily.** The mode toggle touches the exporter and API routes.
  Mitigation: `mode` defaults to `test` everywhere, so omitting it preserves today's
  behaviour exactly.
- **Config changes affect both instances.** Accepted deliberately — identical rules are
  the point. Mitigation: record config changes with dates so a comparison spanning one
  is not read naively.
- **`VIRTUAL_ONLY` is a new branch in the trading path.** Touching `main.py`'s placement
  flow risks the live-money path. Mitigation: the flag only ever *skips* work, never
  changes what a real order does; tests assert `virtual_only=False` behaviour is
  unchanged.
- **Startup cost.** The live instance fetches 1500 klines × 15 symbols on first run
  (~150 weight, one-off) and builds a fresh kline cache.
- **Two containers on one small VPS.** 15G disk at 65%, and the bot process is light.
  Worth watching RAM after the first week.

## Out of scope

- Enabling real orders in live mode. That is a later, separate decision requiring its
  own spec, and needs credentials this instance deliberately does not have.
- The `real_orders_enabled` global/per-symbol switch brainstormed earlier. Not needed:
  a keyless virtual-only instance already cannot trade, and `disabled` already gives
  per-symbol "no real, keep virtual".
- Widening beyond 15 symbols. Revisit once the comparison proves useful.

## Success criteria

1. The live instance runs for a week with no real order placed and no credential present.
2. The test instance is unaffected: same trades it would have made otherwise.
3. For each preset, live-mode and test-mode results are readable side by side.
4. The comparison answers: **do the presets currently selected on testnet also rank well
   on real charts?** A "no" is a valuable answer — it would mean the current preset
   rankings should not be trusted when going live.

---

## Amendment 2026-09-07: the secondary is a mirror, not a pinned live instance

Confirmed with the owner. Supersedes the "Decisions" row on deployment and the
"handover, later" assumption that the secondary is stopped when the primary goes live.

### The shape

The secondary always runs **the mode the primary is not running**.

| dashboard mode | primary | secondary |
|---|---|---|
| test | test — real + virtual, testnet REST/WS | live — virtual only, production REST/WS |
| live | live — real + virtual, production REST/WS | test — virtual only, testnet REST/WS |

Two independent controls, deliberately:

- **Bot mode** (Settings → Trading Mode) decides who trades. Takes effect **on restart**.
- **Data view** (Trades page) decides whose files the dashboard reads. **Instant**, client-side,
  never touches bot state.

### Why restart, and why that is not a compromise

The primary already only changes mode on restart. `POST /api/mode`
(`dashboard/app/api/mode/route.ts:39`) writes `bot_mode.json` directly and returns
`via: 'direct'`; nothing in the dashboard ever writes a `switch_mode` command, so
`ModeManager.switch_mode()` and `main.on_switch_mode()` are unreachable from the UI.
`current_mode` is captured once at `main.py:158` and only `_write_mode()` mutates it.

So "mirror by restart" is the model the primary already follows. The secondary adopts it:
resolve `opposite(bot_mode.json)` at startup, watch the file, and **exit cleanly** when the
opposite no longer matches. `restart: unless-stopped` restarts it into the new mode with
no partially-reinitialised state. Reusing `on_switch_mode()` in-process was rejected: it
closes orders, refetches balance and rebuilds every mode-scoped object, so a partial
failure would leave one instance writing to a mix of both suffixes.

`bot_mode.json` is written atomically (tmp + rename) by both writers, so the watcher
cannot read a torn file.

### Suffix rule — corrected

The original constraint said suffixes apply **only off test mode**. Under the mirror that
is wrong: with the primary in live mode, a mode-keyed rule hands the *secondary* the
canonical unsuffixed names — including `dashboard/public/risk_state.json`, which the
secondary would write with balance 0 and blank the trading bot's risk page.

**Corrected rule: category-B files are suffixed when `virtual_only`, not when mode != test.**
The primary always owns the unsuffixed name; the secondary always writes
`<name>_{its mode}`. Consequences:

- Every existing dashboard reader keeps working unchanged, in either mode.
- The secondary's chart/log/state files never mix two markets, because its suffix tracks
  the market it is actually reading.
- The handover is unaffected: the preset statistics that matter
  (`preset_efficiency_{mode}.json`, `virtual_orders_rank{N}_{symbol}_{mode}.json`,
  `virtual_orders_{mode}.json`) are already keyed on `current_mode`, so a future live
  primary reads exactly the files the live secondary filled.

### Suffix vocabulary: `_live`, not `_public`

Not a preference. A live primary derives every path from `current_mode == "live"`. Naming
the secondary's output `_public` would make the accumulated preset knowledge invisible at
the moment it is needed. The suffix must equal the mode string.

### Two latent bugs found while verifying this

Both are dormant today (`TRADING_MODE=test` and `bot_mode.json` absent → both resolve to
test) and both fire the first time the mode actually changes.

1. **The mode-switch dialog promises what the code does not do.**
   `TradingMode.tsx:62` warns "All open orders will be closed at market price" and
   "Real orders will be placed with real money". Neither happens — the route writes a
   file. The running bot keeps trading in its old mode with its positions open.
2. **Two independent sources of truth for mode.** `current_mode` comes from
   `bot_mode.json`; `DataFeed` is built from `settings.trading_mode` (env `TRADING_MODE`,
   `main.py:320`). If they disagree after a restart, the bot writes `_live`-suffixed files
   while talking to testnet.

Both must be fixed before the primary is ever run in live mode. Tracked as Task 1c.

---

## Amendment 2026-09-07 (b): rename over a bind-mounted file fails with EBUSY

Found while verifying the registry change, not by reading code. The two config files are
bind-mounted as **single files**:

```
./risk_config.json:/app/risk_config.json
./symbol_registry.json:/app/symbol_registry.json
```

Inside the container each of those paths is itself a mount point, so renaming over it
fails. Measured on the server:

```
rename over bind-mounted file: FAILS -> OSError [Errno 16] Device or resource busy
```

This matters twice:

1. **It would have broken the trading bot.** Making `SymbolRegistry._persist()` atomic
   with tmp+rename — the obvious fix for the truncated-read problem — fails with EBUSY
   inside the container. Combined with tolerating a failed write, every pause, disable
   and weight change would have **silently stopped persisting**. The textbook fix was
   the wrong fix for this deployment.

2. **`risk_config._atomic_write()` has always had the same bug.** It is tmp+rename, and
   the only in-container caller is `weight_rebalancer`, which is disabled
   (`weight_rebalancer.enabled = false`) — so it has never fired. It would have started
   raising the day rebalancing was switched on. The config edits that do land come from
   the host via `/bfb-config`, where the path is an ordinary file and rename works.

`config/safe_write.write_json()` now serves both: prefer tmp+rename, fall back to an
in-place write on `OSError`, and still raise for a genuine failure. Verified on the
server against both real files — each falls back to in-place with content preserved and
no `.tmp` left behind.

The fallback is not atomic, which is why the reader side matters: `SymbolRegistry._load()`
tolerates a torn read and deliberately does **not** overwrite a file it could not parse.
Before this change it answered a parse failure by reseeding from the `SYMBOL` env var and
writing — so a transient truncated read would have discarded 15 weights and 7 disabled
entries that the next restart could have recovered.

**Generalisation worth remembering:** on this deployment, any new atomic write to a
bind-mounted single file is broken by construction. Route it through
`config.safe_write.write_json()`.
