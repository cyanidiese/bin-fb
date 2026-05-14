# Telegram Improvements Design
**Date:** 2026-05-13  
**Status:** Approved

---

## Goal

Extend the Telegram notifier to send real-time trade win/loss alerts in USDT, enforce a configurable per-category rate limit to avoid spam, add an `@bo_pal` mention on emergency messages, and expose multiple test message types from the dashboard Settings page.

---

## Current State

- `bot/notifier.py` — sends Telegram for `warning` and `emergency` levels only; no rate limiting; `send_test()` sends a static "notifier is working" string.
- `bot/order_executor.py` — calculates PnL on every real order close but never calls `notifier` for wins/losses.
- `dashboard/app/api/telegram/test/route.ts` — sends a backtest-highlights summary; no message-type selection.
- `dashboard/app/settings/page.tsx` — has a Telegram section with token/chat-id fields and a single "Send test" button.
- `config/risk_config.py` — has a `telegram` sub-object with `token` and `chat_id`; no interval field.

---

## Architecture

### Rate limiting — per-category in `Notifier`

`Notifier` gains a `_last_sent: dict[str, float]` keyed by category string (`"trade"`, `"system"`). Before sending any non-emergency Telegram message, it checks `time.monotonic() - _last_sent[category] < min_interval_s`. If within the window, the message is silently dropped (still written to system log and alert state as usual — only the Telegram send is skipped).

- `"emergency"` category always bypasses the rate limit.
- `"trade"` — trade close notifications.
- `"system"` — startup, mode switch, stop, balance warning, and all other `notify()` calls.

`min_interval_s` is read once at `Notifier` construction time from the risk config dict passed in, under key `telegram_notify_interval_s`. Default: `120` (2 minutes).

### New method: `notify_trade_close()`

```python
def notify_trade_close(
    self,
    symbol: str,
    side: str,          # "BUY" or "SELL"
    pnl_usdt: float,
    entry_price: float,
    close_price: float,
    preset_name: str,
) -> None
```

- Chooses `win` or `loss` based on `pnl_usdt >= 0`.
- Formats an HTML message (matching existing test-route HTML style):
  ```
  ✅ <b>BTCUSDT BUY — Win</b>
  PnL: <b>+12.34 USDT</b>
  Entry: 68,000.00 → Close: 68,450.00
  Preset: trail_15_from_30_full
  ```
  or for a loss:
  ```
  ❌ <b>BTCUSDT SELL — Loss</b>
  PnL: <b>−5.20 USDT</b>
  Entry: 68,000.00 → Close: 67,890.00
  Preset: trail_15_from_30_full
  ```
- Applies the `"trade"` category rate limit before sending.
- Never raises (same resilience contract as `notify()`).

### Parse mode migration: Markdown → HTML

`_send_telegram()` switches `parse_mode` from `"Markdown"` to `"HTML"`. The dashboard test route already uses HTML. Markdown is fragile (underscores in symbol names like `BTC_USDT` break it). Existing `notify()` calls use `*title*` formatting — these are updated to `<b>title</b>` inside `_send_telegram()`. No caller changes needed since formatting is built inside `_send_telegram`.

### Emergency `@bo_pal` mention

`_send_telegram()` gains a `mention: bool` parameter (default `False`). When `True`, prepends `@bo_pal ` to the message text. `notify()` passes `mention=True` when `level == "emergency"`.

### `send_test(msg_type)` — extended signature

```python
def send_test(self, msg_type: str = "connection") -> tuple[bool, str]
```

Supported `msg_type` values:

| Value | Description | Sample data |
|---|---|---|
| `connection` | Existing basic ping | "Bot notifier is working." |
| `trade_win` | Sample win notification | BTCUSDT BUY +12.34 USDT |
| `trade_loss` | Sample loss notification | ETHUSDT SELL −5.20 USDT |
| `emergency` | Emergency with @bo_pal mention | "Test emergency alert" |
| `balance_warning` | Low balance alert | Balance 42.10 USDT below threshold 50 USDT |

All test sends **bypass the rate limit** (they are explicit user-triggered actions, not bot events).

### `notify()` — balance warning

The existing `notify()` method is unchanged in signature. Callers in `bot/risk_manager.py` already call `notifier.notify("warning", ...)` when balance is low. This will now route through the `"system"` category rate limit. No code change needed in risk_manager.

---

## Data flow

```
order_executor._close_order()
  → calls notify_trade_close(symbol, side, pnl_usdt, entry, close, preset)
      → rate-limit check ("trade" category)
      → _send_telegram(level, title, body, mention=False)

notify(level="emergency", ...)
  → _send_telegram(..., mention=True)

send_test(msg_type)
  → bypass rate limit
  → _send_telegram(...)
```

---

## Config changes

### `config/risk_config.py`

Add to `DEFAULT_CONFIG`:

```python
"telegram_notify_interval_s": 120,
```

The existing `telegram` sub-object (`token`, `chat_id`) is unchanged.

### `Notifier.__init__()` gains one new parameter

```python
min_interval_s: float = 120.0,
```

Callers (`main.py`) pass `risk_cfg.get("telegram_notify_interval_s", 120)`.

---

## Files changed

| File | Change |
|---|---|
| `config/risk_config.py` | Add `telegram_notify_interval_s: 120` to `DEFAULT_CONFIG` |
| `bot/notifier.py` | Per-category rate limit; `notify_trade_close()`; `@bo_pal` on emergency; extended `send_test(msg_type)` |
| `bot/order_executor.py` | Call `self._notifier.notify_trade_close(...)` in `_record_real_order_close()` |
| `main.py` | Pass `min_interval_s=risk_cfg.get("telegram_notify_interval_s", 120)` to `Notifier` |
| `dashboard/app/api/risk/route.ts` | Expose `telegram_notify_interval_s` in GET response and accept in POST save |
| `dashboard/app/settings/page.tsx` | Add interval selector (30s / 2m / 5m / 10m) to Telegram section |
| `dashboard/app/api/telegram/test/route.ts` | Accept `type` body param; dispatch to appropriate sample message |
| `tests/test_notifier.py` | Add tests for rate limit, trade close message, emergency mention, send_test types |

---

## Error handling

- Rate limit drop is silent at the Telegram level; system log still records the event.
- `notify_trade_close()` wraps `_send_telegram()` in try/except, logs failure to system log — same pattern as `notify()`.
- `send_test()` returns `(False, error_string)` on any failure; dashboard surfaces the error string.
- Unknown `msg_type` in `send_test()` returns `(False, "Unknown message type: {msg_type}")`.

---

## Dashboard — Settings page Telegram section

Current layout: token field, chat-id field, "Send test" button.

New layout:
- Token field (unchanged)
- Chat ID field (unchanged)
- **Notify interval** — segmented selector: `30s` / `2 min` / `5 min` / `10 min` (maps to `30`, `120`, `300`, `600`)
- **Test message type** — dropdown: Connection / Trade Win / Trade Loss / Emergency / Balance Warning
- **Send test** button (unchanged, now sends the selected type)
- Save button saves interval alongside token/chat-id via existing POST /api/risk

---

## Testing

New tests in `tests/test_notifier.py`:

1. `test_rate_limit_drops_second_trade_message` — two `notify_trade_close()` calls within interval; only one Telegram send.
2. `test_rate_limit_allows_after_interval` — second call after interval passes; both sends fire.
3. `test_emergency_bypasses_rate_limit` — two emergency `notify()` calls; both sends fire regardless of interval.
4. `test_trade_close_win_format` — assert message contains "Win", "+", symbol, preset.
5. `test_trade_close_loss_format` — assert message contains "Loss", "−", symbol.
6. `test_emergency_includes_mention` — assert "@bo_pal" in sent text.
7. `test_send_test_unknown_type` — returns `(False, "Unknown message type: ...")`.
8. `test_send_test_bypasses_rate_limit` — send_test fires even if interval not elapsed.

---

## Out of scope

- Daily P&L summary (requires a scheduler; deferred).
- Telegram bot commands (two-way communication; out of scope).
- Per-symbol rate limits (one global trade category is sufficient).
