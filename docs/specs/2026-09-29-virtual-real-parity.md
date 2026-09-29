# Virtual/real parity: persistent virtual positions, no rank-1 rank_change, real max-age knob

Date: 2026-09-29. Status: approved by the user in chat, being implemented.

## Why

Over 30 days (test mode, pre-entry-candle bug trades excluded), real orders averaged
−1.76 % of margin per trade (n=118). The same presets' independent virtual trades
(ranks ≥ 2) averaged +0.30 % (n=1259). A third of the virtual sample never reached SL/TP.
Those trades were cut by simulator housekeeping and recorded at whatever the price was:

- `closed_early`: `on_stop_bot()` calls `VirtualOrderSimulator.close_all_open()` on every
  restart. 1,235 records since Sep 20, 544 on Sep 28 alone.
- `rank_change`: rank 1 evicts its position when the best preset changes. Ranks ≥ 2
  stopped doing this after Sep 7; 60,266 old records remain.
- `max_age`: virtual positions older than `virtual_max_age_candles` (96) are evicted.
  Real positions have no such limit.

Cut trades average about 0 %. They skew to wide stops: 72 % of virtual trades with SL > 3 %
were cut. Real orders ride those trades to the full loss. So virtual hides the losses
rather than avoiding them.

## What changes (user decisions, 2026-09-29)

1. **Virtual positions survive restarts.**
   - `VirtualOrderSimulator.save_open_state(path)` writes every open rank position (record
     plus `FakeOrder.get_state()`) with `saved_at_ms`, atomically.
   - It runs on graceful stop, replacing `close_all_open()` there, and after every candle
     close, so a crash loses at most one candle.
   - `restore_open_state(path, get_klines, symbols)` runs at startup, after the analyzers
     hold their klines. It re-registers each position, then replays every closed candle
     with close time > `saved_at_ms` (and not before the position opened) through
     `FakeOrder.check(high, low, idx, open, close)`. A position whose SL/TP/trail was hit
     while the bot was down closes at that candle's price and time, with its natural
     result, through the same close path as ticks.
   - Positions of symbols no longer in the roster are dropped with a warning. Nothing is
     recorded for them; there is no result to record.
   - The mode switch keeps `close_all_open()`: it is an explicit "close everything" and
     the user chose that behaviour on 2026-09-26.
   - File: `data/virtual_open_state_{mode}.json` (per mode, so the mirror has its own).
2. **Rank 1 no longer evicts on a best-preset change.** The open position runs to its
   own exit. While it is open, rank 1 records `r1:slot_held_by_other_preset` and does not
   open the new best preset. Promotion eviction (`promoted_to_real`) happens only when
   rank 1 is free to open.
3. **Real max-age knob, off by default.** New risk-config key `real_max_age_candles`,
   default 0 = off.
   - When > 0, the candle-close handler closes a real position older than N × 15 min at
     market via `order_executor.close_order(symbol, reason='max_age')`.
   - `virtual_max_age_candles` stays 96. Same rule, one setting per side.
   - The user turns the real one on by config when there is data. The 24h replay was
     +74 USDT on only 9 trades (one trade = +112); 12h was −91 on 22.

## Rejected

- **`rank_change` for real orders.** It would close real trades on ranking noise. It
  never fires for locked symbols (16 of the traded ones). The virtual version was
  switched off on Sep 7 for diluting every score.
- **Real max-age on by default.** Not enough data (see 3).
- **Keeping virtual positions only in memory with a longer graceful timeout.** Crashes
  and container recreation would still lose them.

## Touch points

- `bot/virtual_order_simulator.py`:
  - `save_open_state`, `restore_open_state`;
  - a shared `_close_position` used by `check_prices` and the replay;
  - rank-1 branch in `on_candle_close`.
- `main.py`:
  - `on_stop_bot` (save instead of close);
  - startup (restore after analyzers are built, before the first tick);
  - candle-close handler (save state; real max-age check);
  - a helper that feeds virtual closes to `virtual_tracker` with the existing
    rank-1/bookkeeping filter, shared by the tick path and the restore.
- `config/risk_config.py`: default `real_max_age_candles: 0`.
- Tests: `tests/test_virtual_persistence.py` (new) plus the existing simulator tests.

## Risks

- **Double-counting on restore.** A position closed by replay must not also stay open.
  The shared close path pops it first.
- **Candle gaps while down.** If the kline cache lacks the downtime candles (a REST ban
  on startup), replay sees fewer candles. The position is then checked from the first
  live tick onward, as real positions are today.
- **Rank balances.** `sync_real_balance_on_start()` still resets rank balances to the
  real balance at each start. A restored position's PnL lands on the reset balance. This
  is unchanged behaviour, recorded as a known difference.
- **Real max-age.** Order execution code, but inert at the default 0.

## Known differences not fixed here (reported to the user)

- Virtual positions get no candle high/low check (ticks only), so the
  `max_losing_candles` rule never fires for virtual.
- The exchange SL algo order is placed once and never trailed. During a rate-limit ban,
  a software exit can strand on the wider exchange SL.
- A locked or traded preset has no independent virtual twin while it trades.
- Real-only gates: loss streak, zone SL, profit factor, blackout, zero weight.
- Dashboard Profit% sums margin % including truncated records.
- `virtual_tracker` scoring still counts `closed_early` / `rank_change` records.

---

# Part 2 — full parity pass (user request 2026-09-29)

Rule from the user: where the real flow can adopt the virtual behaviour, change real; where
it cannot (the exchange forces it, or the real behaviour is the faithful one), make virtual
inherit real. Rank 1 keeps its special role: it records the signals real refused.

## Data behind the direction of the gates

Rank-1 virtual trades on signals real refused (Aug 21 – Sep 29):

| Refusal | Trades | Avg per trade |
|---|---|---|
| loss streak | 8 | −1.92 % |
| profit factor | 4 | −4.79 % |
| blackout | 3 | −2.60 % |
| duplicate SL | 3 | −2.26 % |
| zero weight | 81 | −0.92 % |

The gates did not cost profit, so real keeps them and virtual inherits them.

## Changes

| # | Detail | Before | After | Side changed |
|---|---|---|---|---|
| V1 | Candle high/low check | real every candle close, virtual ticks only (a wick between ticks is missed; `max_losing_candles` never fires for virtual) | `VirtualOrderSimulator.check_candle()` every candle close. It skips positions opened after the candle closed (like `_opened_after`) and advances a per-position `candles_seen` counter. The restart replay uses the same counter | virtual inherits |
| V2 | Leverage ceiling | virtual 125, real exchange bracket | virtual uses `order_executor.get_bracket_max(sym)` for the override and scenario ceiling | virtual inherits |
| V3 | Sizing | virtual `max(alloc, min_notional)·lev/entry`, no rounding, no balance check | Same steps as real: margin = min_notional/lev; leverage bump when the pool cannot fund it (skip `insufficient_balance` if impossible); qty = margin_used·lev·1.02/entry, floored to lot step, bumped one step to min notional, notional cap floored to step, maxQty | virtual inherits |
| V4 | Gates on ranks ≥ 2 | real only | Blackout hours, plus the loss-streak / global-pause / zone-SL cooldowns keyed per symbol:preset:side (virtual has one position per preset where real has one per symbol). They use the preset's own settings, fire from the preset's own virtual closes, and the state is persisted in the virtual open-state file. Rank 1 is exempt. Profit-factor and drawdown hard stop stay real-only: they are account-level, and a rank pool mixes every symbol | virtual inherits |
| V5 | Rank 1 when a real order opens | evicted as `real_order_took_over` whatever its preset | Evicted only when it holds the same preset as the real position (the existing all-rank rule). Otherwise it runs to its own exit | virtual |
| R1 | Duplicate-SL clock | real counts from the signal's candle, virtual from the SL hit | Real counts from the SL-hit candle | real adopts virtual |
| R2 | Candle counter after restart | real resets to 0 | Saved with the restart state and restored | real |
| R3 | Exit slippage | not measurable (real records only the fill) | Real closes also record `exit_trigger_price` (the software decision price), so virtual exit slippage can later be modelled on data | real (enabler) |
| R4 | Exchange SL | placed once, never moved | See below | real adopts virtual |

**R4 detail — exchange SL follows the software stop.**
- `exchange_sl_follow_trail`, per mode, default on.
- At each candle close, when the software stop (`FakeOrder.protective_stop()`: trail, else partial, else early-loss exit, else SL) is tighter than the current exchange stop by at least `exchange_sl_min_move_pct` (0.1 % of price):
  - place a new STOP_MARKET `exchange_sl_buffer_pct` (0.1 %) beyond the software stop, so the software normally exits first and labels the result;
  - then cancel the old stop, so the position is never unprotected.
- Skipped while the rate-limit guard is active, and when the new stop would trigger at once. Both legs are reduceOnly, so a race cannot open a position.

## Not changed (with reason)

- **Zero weight, capital competition, disabled/blocklisted symbols and presets.** Virtual keeps simulating these on purpose; the user asked on 2026-09-28 that unweighted and disabled symbols keep producing virtual orders.
- **Profit-factor / hard stop.** Account-level; see V4.
- **Exit fill slippage in virtual.** Waits for R3 data rather than guessing a number.
- **Rank balances synced to the real balance at each start.** This already mirrors the real account.
