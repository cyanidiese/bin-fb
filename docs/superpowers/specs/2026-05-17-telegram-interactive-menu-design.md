# Telegram Interactive Menu Design

**Date:** 2026-05-17
**Status:** Approved

---

## Goal

Add a two-way interactive Telegram menu to the trading bot. Users send `/start` to open a button-driven interface for viewing bot status, symbols, real/virtual trades, and backtest results. The owner can also perform a small set of safe write actions. Additional Telegram accounts can be granted view-only access at runtime.

---

## Architecture

### Overview

A new `TelegramMenu` class lives in `bot/telegram_menu.py`. It runs an async polling loop (calls `getUpdates` every 2 seconds) inside the bot's existing asyncio event loop, started as a background task. The existing `Notifier` class is unchanged — it continues to send push alerts. `TelegramMenu` uses the same token and chat_id from `risk_config`.

**Why polling, not webhook:** The VPS already has a public IP, but polling requires no TLS cert setup, no route changes, and fits naturally into the existing asyncio loop. Latency of 2s is acceptable for a control menu.

**No new library dependencies** — raw `aiohttp` calls to the Telegram Bot API (async equivalent of the existing `requests` usage in `Notifier`).

### State machine

`TelegramMenu` maintains an in-memory dict per chat session:

```python
_sessions: dict[int, dict]   # chat_id → {state, data}
```

Each session has a `state` string (e.g. `"main_menu"`, `"symbols"`, `"virtual_symbol"`) and a `data` dict for any context needed to render the next message (e.g. the selected symbol name). States are reset to `"main_menu"` on `/start`.

### Access control

Three tiers, resolved on every incoming message:

| Role    | Determined by                        | Access                         |
|---------|--------------------------------------|--------------------------------|
| Owner   | `chat_id == risk_config.telegram.chat_id` | Full menu + all write actions  |
| Viewer  | `chat_id` in `data/telegram_viewers.json` | Full read menu, no write buttons |
| Unknown | none of the above                    | Request sent to owner; menu blocked until approved/denied |

Write actions (Pause, Enable, Resume, Confirm Reset, Allow/Deny viewer) are silently absent from messages rendered for viewer-tier sessions.

### Viewer persistence

`data/telegram_viewers.json` — created on first approval, contains:

```json
{
  "viewers": [
    {"chat_id": 111222333, "username": "alice", "added_at": "2026-05-17T10:00:00Z"},
    {"chat_id": 444555666, "username": "bob",   "added_at": "2026-05-17T11:00:00Z"}
  ]
}
```

Read on every access check (file is small; no caching needed). Written atomically via temp-file rename, same pattern as other state files.

Pending access requests are stored in memory only (cleared on bot restart). If the bot restarts before the owner approves/denies, the unknown user simply sends `/start` again.

---

## File layout

| File | Role |
|------|------|
| `bot/telegram_menu.py` | New: `TelegramMenu` class — polling loop, state machine, message rendering |
| `bot/telegram_menu_views.py` | New: pure functions that return `(text, reply_markup)` for each screen |
| `main.py` | Modified: instantiate `TelegramMenu`, start it as a background asyncio task |
| `data/telegram_viewers.json` | Runtime data: approved viewer chat IDs |

`telegram_menu_views.py` is kept separate so the rendering logic (text + button layout) can be read and edited without touching the polling machinery.

---

## Navigation tree

```
/start
├── owner   → Main Menu (full)
├── viewer  → Main Menu (read-only)
└── unknown → "Request sent to owner" + owner notified
               owner: [✅ Allow Viewer] [❌ Deny]

Main Menu
├── [📊 Status]
├── [🔤 Symbols]
├── [📈 Trades]
├── [🧪 Backtest]
└── [⚙️ Controls]   ← owner only

Status
  Mode | Balance | Symbols (active/disabled/paused) | Hard stop | Uptime | Last candle
  [🔙 Menu]

Symbols
  Active:   [SYM] [SYM] …
  Disabled: [SYM] [SYM] …
  Paused:   [SYM] [SYM] …

  → active symbol
      Name | price | best preset
      [⏸ Pause]   [🔙 Symbols]    ← Pause hidden for viewers

  → disabled symbol
      Name | reason | since
      [✅ Enable]  [🔙 Symbols]    ← Enable hidden for viewers

  → paused symbol
      Name | paused since
      [▶️ Resume]  [🔙 Symbols]    ← Resume hidden for viewers

Trades
  [🏦 Real Orders]  [🔮 Virtual Orders]  [🔙 Menu]

  Real Orders
    [📂 Open Position]  [🕐 Recent History]  [🔙 Trades]

    Open Position
      Symbol | side | entry | unrealized PnL%
      (or "No open position")
      [🔙 Real Orders]

    Recent History
      Last 10 closed real orders, one per line:
        ✅/❌  SYMBOL SIDE  ±USDT  date
      [🔙 Real Orders]

  Virtual Orders
    [SYM] [SYM] …   [🔙 Trades]

    → symbol selected:
        Rank 2  preset_name  SIDE  ±pct%  open  (or — no position)
        Rank 3  …
        Rank 4  …
        Rank 5  …
        Rank 6  …
        [📜 Recent Closed]  [🔙 Virtual]

      Recent Closed
        Last 10 closed virtual orders for this symbol:
          ✅/❌  RankN  preset  SIDE  ±USDT
        [🔙 SYMBOL]

Backtest
  [SYM] [SYM] …   [🔙 Menu]

  → symbol selected:
      Top 5 presets by net profit %:
        1  preset_name  +X.XX%  NT  WR%
        …
      [🔙 Backtest]

Controls  (owner only)
  [⛔ Reset Hard Stop]   ← shown only if hard stop active
  [⏸ Paused Symbols]    ← shown only if any symbol paused
  [👥 Manage Viewers]
  [🔙 Menu]

  Reset Hard Stop
    Warning text
    [✅ Confirm Reset]  [❌ Cancel]
    On confirm → calls risk_manager.reset_hard_stop()

  Paused Symbols
    [SYM] [SYM] …  [🔙 Controls]
    → tap symbol:
        Resume SYM?
        [✅ Resume]  [❌ Cancel]

  Manage Viewers
    Current viewers listed with [🚫 Revoke] per entry
    [🔙 Controls]
```

---

## Data sources per screen

| Screen | Source file(s) |
|--------|---------------|
| Status | `dashboard/public/risk_state.json`, `data/bot_mode.json`, `symbol_registry.json`, `dashboard/public/results_{SYMBOL}.json` → `generated_at` (most recent across active symbols = last candle time) |
| Symbols | `symbol_registry.json` (disabled + paused dicts), active list from bot state |
| Real → Open | `data/real_orders_{SYMBOL}_{mode}.json` (filter status=open) |
| Real → History | `data/real_orders_{SYMBOL}_{mode}.json` (all symbols, sort by close_time) |
| Virtual → Ranks | `data/virtual_orders_rank{N}_{SYMBOL}_{mode}.json` |
| Backtest | `dashboard/public/backtest_results_{SYMBOL}.json` |
| Hard stop | `dashboard/public/risk_state.json` → `hard_stop_active` field |

---

## Write actions

### Pause symbol
Adds entry to `symbol_registry.json` under a new `paused` dict:
```json
{ "paused": { "SOLUSDT": { "paused_at": "2026-05-17T10:00:00Z" } } }
```
`main.py` checks `symbol_registry.is_symbol_paused(symbol)` before opening any new order for that symbol. No open position is forced closed — pause takes effect on the next candle close.

### Resume symbol
Removes entry from `paused` dict in `symbol_registry.json`.

### Enable symbol
Removes entry from `disabled` dict in `symbol_registry.json`. Mirrors existing `/api/symbols/[symbol]/enable` API endpoint logic.

### Reset hard stop
Calls `risk_manager.reset_hard_stop()` on the live `RiskManager` instance passed into `TelegramMenu` at construction time.

### Approve/deny viewer
Writes to or leaves unchanged `data/telegram_viewers.json`.

---

## `TelegramMenu` construction

```python
TelegramMenu(
    token: str,
    owner_chat_id: int,
    risk_manager: RiskManager,
    symbol_registry: SymbolRegistry,
    project_root: Path,
    get_mode: Callable[[], str],
    get_active_symbols: Callable[[], list[str]],
    get_current_price: Callable[[str], float | None],
)
```

`RiskManager` and `SymbolRegistry` are passed by reference so write actions take effect on the live objects immediately. File reads for data screens happen inline at request time (no caching in menu layer — files are small).

---

## Polling loop

```python
async def run(self) -> None:
    offset = 0
    while True:
        updates = await self._get_updates(offset, timeout=30)
        for update in updates:
            offset = update["update_id"] + 1
            await self._handle_update(update)
        await asyncio.sleep(0)   # yield to event loop between batches
```

Long-polling (`timeout=30`) is used so the bot receives updates within ~1s without busy-waiting. The `asyncio.sleep(0)` after each batch yields control back to the main trading loop.

Error handling: network errors are caught and logged; the loop continues after a short backoff. A single failed update does not stop the menu.

---

## `SymbolRegistry` additions

Two new methods (no new file — added to existing `bot/symbol_registry.py`):

- `pause_symbol(symbol: str) -> None`
- `resume_symbol(symbol: str) -> None`
- `is_symbol_paused(symbol: str) -> bool`
- `get_paused_symbols() -> dict[str, dict]`

And `main.py` adds a `symbol_registry.is_symbol_paused(symbol)` check before calling `order_executor.open_order()`.

---

## Error handling

- All Telegram API calls wrapped in try/except; errors logged, menu continues.
- Stale/missing data files render as "Data unavailable" rather than crashing.
- Unknown `callback_data` values are silently ignored (no state corruption).
- Messages older than 60 seconds (late poll delivery) are ignored.

---

## Out of scope

- No bot command aliases beyond `/start` (the inline keyboard handles all navigation).
- No message editing (each action sends a new message rather than editing the previous one — simpler state management).
- No group chat support — only direct messages to the bot.
- No persistent session state across bot restarts — users re-navigate from `/start`.
