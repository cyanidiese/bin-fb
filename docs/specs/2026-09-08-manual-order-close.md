# Manual order close from the Trades page

**Date:** 2026-09-08
**Status:** spec — approved for implementation
**Touches real money.** A button on a web page will market-close a live position.

---

## What it does

Four changes to the Trades page open-position rows, plus the backend to support them.

1. The `LIVE` badge becomes **`NOW`**. `LIVE` collides with "live mode" — the badge means
   "this position is open right now", which is unrelated to which market the instance
   trades.
2. Hovering `NOW` shows the full order: entry, TP, SL, quantity, leverage, side, preset,
   scenario, how long it has been open, current price, **and the unrealised result so far**.
3. A small red **×** after `NOW` closes that position immediately — real or virtual.
4. Pressing × asks for confirmation inline, in the style of the Backtest presets table
   (`Close? Yes | No`), plus one line of small text saying what will happen.

Every manual close is logged and recorded distinctly from a strategy exit.

## Why

There is currently no way to exit a position from the dashboard. The only options are the
preset's own rules, the exchange stop, or `close_positions_on_stop` — which closes
*everything*. Two situations today needed a single close and had none: SOLUSDT sat 16
candles with its trail never arming, and a stalled position occupies the symbol's real
slot and (since 2026-09-08) suppresses its preset's virtual pool too.

## Design

### 1. Who is allowed to execute the close

The dashboard cannot call the bot directly — separate containers. The existing channel is
`data/bot_command.json`, written by the dashboard and polled every 2s by
`ModeManager.poll_loop`, with the outcome written to `data/bot_command_result.json`.

**Only the primary instance polls commands** (`main.py`: `if not _virtual_only`), and the
command file is not instance-suffixed. So a close is executable only for the instance
whose mode matches the bot's own.

The API therefore **refuses, with a clear message, when the viewed instance is not the
running one** — rather than writing a command the mirror will never read, or worse, one
the primary would apply to its own position of the same name. The button is disabled in
that case, with the reason in its tooltip.

This is a deliberate limitation, not an oversight: making the mirror poll commands needs
per-instance command files, and the mirror holds no real money.

### 2. Real close

`order_executor.close_order(symbol)` already does everything needed — market close, PnL,
record, Telegram, state reset to IDLE. It hardcodes `result='market_close'`.

Add an optional `reason` parameter defaulting to `'market_close'` so a manual close is
recorded as **`manual_close`**. Existing callers are unaffected.

Distinguishing it matters: `manual_close` is not a strategy outcome and must be excluded
from preset scoring the same way `promoted_to_real` and `max_age` are.

### 3. Virtual close

`_evict(symbol, rank, price, reason)` already closes a rank position with a chosen result
label. Expose `close_open_manually(symbol, rank, price)` which calls it with
`'manual_close'`, so the private method stays private and the public one can validate that
the position exists.

The price comes from the caller (`analyzer.get_current_price()`), matching how
`close_all_open` sources it.

### 4. Live unrealised result

`open_positions_{mode}.json` carries entry, tp, sl, quantity, leverage, side, preset,
scenario and open_time — but no current price, so the dashboard cannot compute a result.

The bot adds `current_price`, `unrealized_pnl_usdt` and `unrealized_pct` (on margin) to
each open-position entry when it writes the snapshot. The bot is the right place: it
already has the live price, and one source of truth avoids the dashboard and the bot
disagreeing about the same position.

The snapshot is written per candle, so the figure is up to one candle old. The tooltip
prints the snapshot time so that is visible rather than implied.

### 5. Logging

Three records per manual close, because each answers a different question:

| where | what it answers |
|---|---|
| `bot.log` at INFO | what happened, in sequence with everything else |
| the order record (`result='manual_close'`) | how this trade ended, for P&L analysis |
| `system_log` | that a human intervened, and when — an audit trail |

`analysis_log` already receives virtual closes via `_evict`.

## Touch points

- `bot/order_executor.py` — `close_order(symbol, reason='market_close')`
- `bot/virtual_order_simulator.py` — `close_open_manually(symbol, rank, price)`
- `bot/mode_manager.py` — handle `close_order` in `poll_loop`
- `main.py` — `on_close_order` callback; enrich the open-position snapshot
- `dashboard/app/api/orders/close/route.ts` — new; writes the command, awaits the result
- `dashboard/app/trades/page.tsx` — NOW badge, tooltip, × button, inline confirm
- tests for each

## Risks

- **It closes real positions.** Mitigated by: inline confirmation, an explanatory line, no
  keyboard shortcut, and a per-row pending state so one click cannot close two positions.
- **Double close.** `close_order` returns None when the symbol is not open, and pops the
  order in a `finally`. The virtual path checks the rank/symbol is present. A second press
  is a no-op, reported as such.
- **A close during an API ban.** The market-close call may be rejected. `close_order`
  already catches, notifies and resets state; the result comes back as a failure and the
  UI says so rather than showing a phantom success.
- **Wrong instance.** Covered in §1 — refused, not silently misapplied.
- **`manual_close` polluting preset statistics.** Excluded from scoring like the other
  bookkeeping results.

## Success criteria

1. The badge reads `NOW`; no `LIVE` remains in the orders table.
2. The tooltip shows every field above plus the unrealised result and the snapshot time.
3. × asks for confirmation; `No` cancels and closes nothing.
4. `Yes` on a real position closes it at market and records `result='manual_close'`.
5. `Yes` on a virtual position closes that rank and records `result='manual_close'`.
6. A close attempted for the non-running instance is refused with a readable reason.
7. Every manual close appears in `bot.log`, the order record, and `system_log`.
8. `manual_close` does not feed `preset_efficiency`.
