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
