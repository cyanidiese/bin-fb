# Telegram Interactive Menu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-way interactive Telegram menu so the bot owner (and read-only viewers) can check status, symbols, trades, and backtest results from Telegram, with a small set of safe write actions.

**Architecture:** A new `TelegramMenu` class polls `getUpdates` every 2 s inside the existing asyncio loop using `asyncio.to_thread(requests.get, ...)` (no new dependencies). Per-session navigation state lives in an in-memory dict. A separate `telegram_views.py` holds pure rendering functions that return `(text, reply_markup)` tuples — easy to test and easy to edit.

**Tech Stack:** Python 3.11, asyncio, existing `requests` library (via `asyncio.to_thread`), Telegram Bot API inline keyboards, pytest.

---

## File map

| File | Action | Responsibility |
|------|--------|---------------|
| `bot/symbol_registry.py` | Modify | Add pause/resume methods and `_paused` dict |
| `bot/telegram_views.py` | Create | Pure `(text, reply_markup)` rendering functions for every screen |
| `bot/telegram_menu.py` | Create | `TelegramMenu`: polling loop, session state, access control, read screens, write actions |
| `main.py` | Modify | Instantiate `TelegramMenu`, add background task, add pause check before order placement |
| `tests/test_symbol_registry_pause.py` | Create | Tests for new pause/resume methods |
| `tests/test_telegram_views.py` | Create | Tests for rendering functions |
| `tests/test_telegram_menu.py` | Create | Tests for access control and action routing |

---

## Task 1: SymbolRegistry pause/resume

**Files:**
- Modify: `bot/symbol_registry.py`
- Create: `tests/test_symbol_registry_pause.py`

### Context

`SymbolRegistry` already has `disable()`/`reenable()` for auto-disabled symbols. "Pause" is manual and temporary — no weight redistribution, no side-effects beyond blocking new orders. The `_paused` dict lives next to `_disabled` in `symbol_registry.json`:

```json
{
  "paused": {
    "SOLUSDT": {"paused_at": "2026-05-17T10:00:00Z"}
  }
}
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_symbol_registry_pause.py
import json
from pathlib import Path
from bot.symbol_registry import SymbolRegistry


def _make_registry(tmp_path, symbols):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": symbols,
        "weights": {s: 1.0 / len(symbols) for s in symbols},
        "disabled": {},
        "status": {s: {"backtest": "none", "pid": None} for s in symbols},
    }))
    return SymbolRegistry(seed_symbols=symbols, registry_path=path)


def test_pause_marks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    assert reg.is_symbol_paused("BTCUSDT")
    assert not reg.is_symbol_paused("ETHUSDT")


def test_resume_unmarks_symbol(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    reg.resume_symbol("BTCUSDT")
    assert not reg.is_symbol_paused("BTCUSDT")


def test_get_paused_symbols_returns_dict(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    reg.pause_symbol("BTCUSDT")
    paused = reg.get_paused_symbols()
    assert "BTCUSDT" in paused
    assert "paused_at" in paused["BTCUSDT"]


def test_pause_persists_across_reload(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "symbols": ["BTCUSDT"],
        "weights": {"BTCUSDT": 1.0},
        "disabled": {},
        "status": {"BTCUSDT": {"backtest": "none", "pid": None}},
    }))
    reg = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
    reg.pause_symbol("BTCUSDT")

    reg2 = SymbolRegistry(seed_symbols=["BTCUSDT"], registry_path=path)
    assert reg2.is_symbol_paused("BTCUSDT")


def test_pause_does_not_affect_weight(tmp_path):
    reg = _make_registry(tmp_path, ["BTCUSDT", "ETHUSDT"])
    w_before = reg.get_weight("BTCUSDT")
    reg.pause_symbol("BTCUSDT")
    assert reg.get_weight("BTCUSDT") == w_before
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -m pytest tests/test_symbol_registry_pause.py -v
```

Expected: 5 failures — `AttributeError: 'SymbolRegistry' object has no attribute 'pause_symbol'`

- [ ] **Step 3: Add pause/resume to SymbolRegistry**

In `bot/symbol_registry.py`, add `_paused: dict[str, dict] = {}` initialisation in `_load`, persist it in `_persist`, and add four public methods. Find the `is_disabled` method (around line 68) and add after it:

```python
    def is_symbol_paused(self, symbol: str) -> bool:
        return symbol in self._paused

    def pause_symbol(self, symbol: str) -> None:
        with self._lock:
            self._paused[symbol] = {
                "paused_at": datetime.now(timezone.utc).isoformat(),
            }
            self._persist()

    def resume_symbol(self, symbol: str) -> None:
        with self._lock:
            self._paused.pop(symbol, None)
            self._persist()

    def get_paused_symbols(self) -> dict:
        return dict(self._paused)
```

In `_load` (around line 136), add after `self._disabled_ranks`:
```python
                self._paused: dict[str, dict] = data.get('paused', {})
```

And in the fallback branch of `_load` (around line 150), add:
```python
        self._paused: dict[str, dict] = {}
```

In `_persist` (around line 156), add `'paused': self._paused` to the `data` dict:
```python
    def _persist(self) -> None:
        data = {
            'symbols': self._symbols,
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'status': self._status,
            'weights': self._weights,
            'disabled': self._disabled,
            'disabled_ranks': self._disabled_ranks,
            'paused': self._paused,
        }
        self._path.write_text(json.dumps(data, indent=2))
```

- [ ] **Step 4: Run tests — expect all 5 pass**

```bash
python -m pytest tests/test_symbol_registry_pause.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bot/symbol_registry.py tests/test_symbol_registry_pause.py
git commit -m "feat: add pause/resume to SymbolRegistry"
```

---

## Task 2: telegram_views.py — pure rendering functions

**Files:**
- Create: `bot/telegram_views.py`
- Create: `tests/test_telegram_views.py`

### Context

Every screen in the Telegram menu is produced by a pure function that takes plain Python data and returns `(text: str, reply_markup: dict)`. `reply_markup` is a Telegram inline keyboard dict:

```python
{"inline_keyboard": [[{"text": "Label", "callback_data": "key"}]]}
```

Callback data keys used throughout:
- `"menu"` → main menu
- `"status"` → status screen
- `"symbols"` → symbols list
- `"sym:{SYM}"` → symbol detail
- `"do_enable:{SYM}"` → execute enable (single-click, no confirm)
- `"confirm_pause:{SYM}"` → confirm-pause dialog
- `"do_pause:{SYM}"` → execute pause
- `"do_resume:{SYM}"` → execute resume (single-click, from paused list)
- `"trades"` → trades menu
- `"real_orders"` → real orders submenu
- `"real_open"` → real open position
- `"real_history"` → real history
- `"virtual"` → virtual symbol list
- `"vsym:{SYM}"` → virtual positions for symbol
- `"vhist:{SYM}"` → virtual history for symbol
- `"backtest"` → backtest symbol list
- `"btsym:{SYM}"` → backtest for symbol
- `"controls"` → controls screen
- `"confirm_reset"` → confirm-reset dialog
- `"do_reset"` → execute reset
- `"paused_syms"` → paused symbols (for resume)
- `"viewers"` → manage viewers
- `"revoke:{CHAT_ID}"` → revoke viewer
- `"allow:{CHAT_ID}"` → approve pending viewer
- `"deny:{CHAT_ID}"` → deny pending viewer

- [ ] **Step 1: Write failing tests**

```python
# tests/test_telegram_views.py
import pytest
from bot.telegram_views import (
    render_main_menu,
    render_status,
    render_symbols,
    render_symbol_active,
    render_symbol_disabled,
    render_symbol_paused,
    render_confirm_pause,
    render_trades_menu,
    render_real_open,
    render_real_history,
    render_virtual_symbols,
    render_virtual_symbol,
    render_virtual_history,
    render_backtest_symbols,
    render_backtest_symbol,
    render_controls,
    render_confirm_reset,
    render_paused_for_resume,
    render_manage_viewers,
    render_access_request,
)


def _buttons(reply_markup):
    """Flatten all button callback_data values into a list."""
    return [
        btn["callback_data"]
        for row in reply_markup["inline_keyboard"]
        for btn in row
    ]


def test_main_menu_owner_has_controls():
    text, rm = render_main_menu(is_owner=True)
    assert "controls" in _buttons(rm)
    assert "status" in _buttons(rm)


def test_main_menu_viewer_no_controls():
    text, rm = render_main_menu(is_owner=False)
    assert "controls" not in _buttons(rm)
    assert "status" in _buttons(rm)


def test_status_shows_hard_stop():
    text, rm = render_status(
        mode="live", balance=1234.56, hard_stop_active=True,
        hard_stop_since="2026-05-15T14:45:00Z",
        n_active=12, n_disabled=2, n_paused=1,
        uptime_str="3d 14h",
        last_candle_sym="BTCUSDT", last_candle_ago="2m ago",
    )
    assert "⛔" in text
    assert "1,234.56" in text


def test_status_clear_hard_stop():
    text, rm = render_status(
        mode="test", balance=500.0, hard_stop_active=False,
        hard_stop_since=None,
        n_active=5, n_disabled=0, n_paused=0,
        uptime_str="1h", last_candle_sym=None, last_candle_ago=None,
    )
    assert "✅" in text


def test_symbols_owner_has_enable_button():
    text, rm = render_symbol_disabled(
        symbol="DOGEUSDT", reason="5 consecutive failures",
        disabled_at="2026-05-15T14:45:00Z", is_owner=True,
    )
    assert "do_enable:DOGEUSDT" in _buttons(rm)


def test_symbols_viewer_no_enable_button():
    text, rm = render_symbol_disabled(
        symbol="DOGEUSDT", reason="5 consecutive failures",
        disabled_at="2026-05-15T14:45:00Z", is_owner=False,
    )
    assert "do_enable:DOGEUSDT" not in _buttons(rm)


def test_symbol_active_owner_has_pause():
    text, rm = render_symbol_active(
        symbol="BTCUSDT", price=104200.0,
        best_preset="r5_arm15_cooldown", is_owner=True,
    )
    assert "confirm_pause:BTCUSDT" in _buttons(rm)


def test_confirm_pause_has_do_pause_and_cancel():
    text, rm = render_confirm_pause("BTCUSDT")
    cbs = _buttons(rm)
    assert "do_pause:BTCUSDT" in cbs
    assert "sym:BTCUSDT" in cbs  # cancel goes back to symbol detail


def test_controls_shows_reset_only_when_active():
    text_active, rm_active = render_controls(
        hard_stop_active=True, paused_symbols=[]
    )
    assert "confirm_reset" in _buttons(rm_active)

    text_clear, rm_clear = render_controls(
        hard_stop_active=False, paused_symbols=[]
    )
    assert "confirm_reset" not in _buttons(rm_clear)


def test_controls_shows_paused_syms_only_when_present():
    _, rm_with = render_controls(hard_stop_active=False, paused_symbols=["SOLUSDT"])
    assert "paused_syms" in _buttons(rm_with)

    _, rm_empty = render_controls(hard_stop_active=False, paused_symbols=[])
    assert "paused_syms" not in _buttons(rm_empty)


def test_virtual_symbol_renders_all_ranks():
    ranks = [
        {"rank": 2, "preset_name": "r5_arm15_cooldown", "side": "BUY", "pnl_pct": 0.8, "status": "open"},
        {"rank": 3, "preset_name": "trail_15_full", "side": None, "pnl_pct": None, "status": "none"},
    ]
    text, rm = render_virtual_symbol("BTCUSDT", ranks)
    assert "Rank 2" in text
    assert "Rank 3" in text
    assert "vhist:BTCUSDT" in _buttons(rm)


def test_backtest_symbol_shows_top5():
    top5 = [
        {"name": "r5_arm15_cooldown", "profit_pct": 4.35, "n_trades": 21, "win_rate": 0.571},
        {"name": "r6_arm15_maxp3", "profit_pct": 3.94, "n_trades": 18, "win_rate": 0.611},
    ]
    text, _ = render_backtest_symbol("BTCUSDT", top5)
    assert "r5_arm15_cooldown" in text
    assert "+4.35%" in text


def test_access_request_has_allow_deny():
    text, rm = render_access_request("alice", 111222333)
    cbs = _buttons(rm)
    assert "allow:111222333" in cbs
    assert "deny:111222333" in cbs
```

- [ ] **Step 2: Run tests — confirm all fail**

```bash
python -m pytest tests/test_telegram_views.py -v
```

Expected: all fail with `ModuleNotFoundError: No module named 'bot.telegram_views'`

- [ ] **Step 3: Create bot/telegram_views.py**

```python
# bot/telegram_views.py
"""Pure rendering functions: each returns (html_text, reply_markup_dict).

No I/O, no side-effects — designed to be trivially testable.
"""
from __future__ import annotations

import html


# ── Helpers ────────────────────────────────────────────────────────────────


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _kb(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": rows}


def _back(data: str) -> dict:
    return _btn("🔙 Back", data)


def _sym_rows(symbols: list[str], prefix: str, cols: int = 3) -> list[list[dict]]:
    """Build rows of symbol buttons with at most `cols` per row."""
    btns = [_btn(s, f"{prefix}:{s}") for s in symbols]
    return [btns[i:i + cols] for i in range(0, len(btns), cols)]


# ── Main menu ──────────────────────────────────────────────────────────────


def render_main_menu(is_owner: bool) -> tuple[str, dict]:
    rows = [
        [_btn("📊 Status", "status"), _btn("🔤 Symbols", "symbols")],
        [_btn("📈 Trades", "trades"), _btn("🧪 Backtest", "backtest")],
    ]
    if is_owner:
        rows.append([_btn("⚙️ Controls", "controls")])
    return "🤖 <b>Bot Menu</b>\nSelect an action:", _kb(rows)


# ── Status ─────────────────────────────────────────────────────────────────


def render_status(
    mode: str,
    balance: float,
    hard_stop_active: bool,
    hard_stop_since: str | None,
    n_active: int,
    n_disabled: int,
    n_paused: int,
    uptime_str: str,
    last_candle_sym: str | None,
    last_candle_ago: str | None,
) -> tuple[str, dict]:
    hs = f"⛔ ACTIVE since {hard_stop_since}" if hard_stop_active else "✅ clear"
    lc = f"{last_candle_sym} {last_candle_ago}" if last_candle_sym else "—"
    lines = [
        "📊 <b>Bot Status</b>",
        f"Mode: <b>{html.escape(mode)}</b>  |  Balance: <b>{balance:,.2f} USDT</b>",
        f"Symbols: {n_active} active, {n_disabled} disabled, {n_paused} paused",
        f"Hard stop: {hs}",
        f"Uptime: {uptime_str}  |  Last candle: {lc}",
    ]
    return "\n".join(lines), _kb([[_back("menu")]])


# ── Symbols ────────────────────────────────────────────────────────────────


def render_symbols(
    active: list[str],
    disabled: dict[str, dict],
    paused: dict[str, dict],
) -> tuple[str, dict]:
    rows: list[list[dict]] = []
    if active:
        rows.append([_btn(f"✅ {s}", f"sym:{s}") for s in active[:4]])
        if len(active) > 4:
            rows.append([_btn(f"✅ {s}", f"sym:{s}") for s in active[4:8]])
    if disabled:
        rows.append([_btn(f"🚫 {s}", f"sym:{s}") for s in list(disabled)[:4]])
    if paused:
        rows.append([_btn(f"⏸ {s}", f"sym:{s}") for s in list(paused)[:4]])
    rows.append([_back("menu")])
    parts = [f"🔤 <b>Symbols</b>"]
    if active:
        parts.append(f"Active ({len(active)}): {', '.join(active)}")
    if disabled:
        parts.append(f"Disabled ({len(disabled)}): {', '.join(disabled)}")
    if paused:
        parts.append(f"Paused ({len(paused)}): {', '.join(paused)}")
    return "\n".join(parts), _kb(rows)


def render_symbol_active(
    symbol: str, price: float, best_preset: str, is_owner: bool
) -> tuple[str, dict]:
    text = (
        f"✅ <b>{html.escape(symbol)}</b>\n"
        f"Price: {price:,.2f}  |  Status: active\n"
        f"Best preset: {html.escape(best_preset)}"
    )
    rows: list[list[dict]] = []
    if is_owner:
        rows.append([_btn("⏸ Pause", f"confirm_pause:{symbol}")])
    rows.append([_back("symbols")])
    return text, _kb(rows)


def render_symbol_disabled(
    symbol: str, reason: str, disabled_at: str, is_owner: bool
) -> tuple[str, dict]:
    text = (
        f"🚫 <b>{html.escape(symbol)}</b> — disabled\n"
        f"Reason: {html.escape(reason)}\n"
        f"Since: {disabled_at[:16].replace('T', ' ')}"
    )
    rows: list[list[dict]] = []
    if is_owner:
        rows.append([_btn("✅ Enable", f"do_enable:{symbol}")])
    rows.append([_back("symbols")])
    return text, _kb(rows)


def render_symbol_paused(
    symbol: str, paused_at: str, is_owner: bool
) -> tuple[str, dict]:
    text = (
        f"⏸ <b>{html.escape(symbol)}</b> — paused\n"
        f"Since: {paused_at[:16].replace('T', ' ')}"
    )
    rows: list[list[dict]] = []
    if is_owner:
        rows.append([_btn("▶️ Resume", f"do_resume:{symbol}")])
    rows.append([_back("symbols")])
    return text, _kb(rows)


def render_confirm_pause(symbol: str) -> tuple[str, dict]:
    text = (
        f"⚠️ Pause <b>{html.escape(symbol)}</b>?\n"
        "Trading will stop on the next candle close.\n"
        "Open positions are not affected."
    )
    rows = [
        [_btn("✅ Confirm Pause", f"do_pause:{symbol}"), _btn("❌ Cancel", f"sym:{symbol}")],
    ]
    return text, _kb(rows)


# ── Trades ─────────────────────────────────────────────────────────────────


def render_trades_menu() -> tuple[str, dict]:
    rows = [
        [_btn("🏦 Real Orders", "real_orders"), _btn("🔮 Virtual Orders", "virtual")],
        [_back("menu")],
    ]
    return "📈 <b>Trades</b>", _kb(rows)


def render_real_orders_menu() -> tuple[str, dict]:
    rows = [
        [_btn("📂 Open Position", "real_open"), _btn("🕐 Recent History", "real_history")],
        [_back("trades")],
    ]
    return "🏦 <b>Real Orders</b>", _kb(rows)


def render_real_open(orders: list[dict]) -> tuple[str, dict]:
    if not orders:
        body = "No open positions."
    else:
        lines = []
        for o in orders:
            sign = "+" if o.get("side") == "BUY" else ""
            lines.append(
                f"<b>{html.escape(o['symbol'])}</b> {o['side']}  "
                f"@ {o['entry_price']:,.2f}  |  {html.escape(o.get('preset_name', ''))}"
            )
        body = "\n".join(lines)
    return f"📂 <b>Open Positions</b>\n{body}", _kb([[_back("real_orders")]])


def render_real_history(orders: list[dict]) -> tuple[str, dict]:
    if not orders:
        body = "No closed orders yet."
    else:
        lines = []
        for o in orders:
            emoji = "✅" if o.get("pnl_usdt", 0) >= 0 else "❌"
            sign = "+" if o.get("pnl_usdt", 0) >= 0 else ""
            ts = str(o.get("close_time", ""))[:16].replace("T", " ")
            lines.append(
                f"{emoji} <b>{html.escape(o['symbol'])}</b> {o['side']}  "
                f"{sign}{o.get('pnl_usdt', 0):.2f} USDT  {ts}"
            )
        body = "\n".join(lines)
    return f"🕐 <b>Recent Real Orders</b>\n{body}", _kb([[_back("real_orders")]])


# ── Virtual orders ─────────────────────────────────────────────────────────


def render_virtual_symbols(symbols: list[str]) -> tuple[str, dict]:
    rows = _sym_rows(symbols, "vsym")
    rows.append([_back("trades")])
    return "🔮 <b>Virtual Orders — select symbol</b>", _kb(rows)


def render_virtual_symbol(symbol: str, ranks: list[dict]) -> tuple[str, dict]:
    lines = [f"🔮 <b>{html.escape(symbol)}</b> virtual positions"]
    for r in ranks:
        if r["status"] == "open":
            sign = "+" if (r.get("pnl_pct") or 0) >= 0 else ""
            pct = f"{sign}{r['pnl_pct']:.2f}%" if r.get("pnl_pct") is not None else ""
            lines.append(
                f"Rank {r['rank']}  {html.escape(r['preset_name'])}  "
                f"{r['side']} {pct}  open"
            )
        else:
            lines.append(f"Rank {r['rank']}  {html.escape(r['preset_name'])}  — no position")
    rows = [
        [_btn("📜 Recent Closed", f"vhist:{symbol}"), _back("virtual")],
    ]
    return "\n".join(lines), _kb(rows)


def render_virtual_history(symbol: str, orders: list[dict]) -> tuple[str, dict]:
    if not orders:
        body = "No closed virtual orders."
    else:
        lines = []
        for o in orders:
            emoji = "✅" if (o.get("pnl_usdt") or 0) >= 0 else "❌"
            sign = "+" if (o.get("pnl_usdt") or 0) >= 0 else ""
            pnl = f"{sign}{o.get('pnl_usdt', 0):.2f} USDT" if o.get("pnl_usdt") is not None else "—"
            lines.append(
                f"{emoji} Rank{o['rank']}  {html.escape(o['preset_name'])}  "
                f"{o['side']}  {pnl}"
            )
        body = "\n".join(lines)
    return f"📜 <b>{html.escape(symbol)}</b> recent virtual closes\n{body}", _kb([[_back(f"vsym:{symbol}")]])


# ── Backtest ───────────────────────────────────────────────────────────────


def render_backtest_symbols(symbols: list[str]) -> tuple[str, dict]:
    rows = _sym_rows(symbols, "btsym")
    rows.append([_back("menu")])
    return "🧪 <b>Backtest — select symbol</b>", _kb(rows)


def render_backtest_symbol(symbol: str, top5: list[dict]) -> tuple[str, dict]:
    lines = [f"🧪 <b>{html.escape(symbol)}</b> — top presets"]
    for i, p in enumerate(top5, 1):
        sign = "+" if p["profit_pct"] >= 0 else ""
        lines.append(
            f"{i}. {html.escape(p['name'])}  "
            f"{sign}{p['profit_pct']:.2f}%  "
            f"{p['n_trades']}T  {p['win_rate']:.0%}"
        )
    return "\n".join(lines), _kb([[_back("backtest")]])


# ── Controls ───────────────────────────────────────────────────────────────


def render_controls(
    hard_stop_active: bool, paused_symbols: list[str]
) -> tuple[str, dict]:
    rows: list[list[dict]] = []
    if hard_stop_active:
        rows.append([_btn("⛔ Reset Hard Stop", "confirm_reset")])
    if paused_symbols:
        rows.append([_btn(f"⏸ Paused Symbols ({len(paused_symbols)})", "paused_syms")])
    rows.append([_btn("👥 Manage Viewers", "viewers"), _back("menu")])
    text = "⚙️ <b>Controls</b>"
    if hard_stop_active:
        text += "\n⛔ Hard stop is ACTIVE"
    return text, _kb(rows)


def render_confirm_reset() -> tuple[str, dict]:
    text = (
        "⚠️ <b>Reset the drawdown guard?</b>\n"
        "This re-enables all trade placement immediately."
    )
    rows = [[_btn("✅ Confirm Reset", "do_reset"), _btn("❌ Cancel", "controls")]]
    return text, _kb(rows)


def render_paused_for_resume(paused: list[str]) -> tuple[str, dict]:
    rows = _sym_rows(paused, "do_resume")
    rows.append([_back("controls")])
    return "⏸ <b>Paused symbols — tap to resume</b>", _kb(rows)


# ── Viewers ────────────────────────────────────────────────────────────────


def render_manage_viewers(viewers: list[dict]) -> tuple[str, dict]:
    if not viewers:
        body = "No viewers registered."
        rows: list[list[dict]] = []
    else:
        lines = []
        rows = []
        for v in viewers:
            uname = html.escape(v.get("username") or str(v["chat_id"]))
            lines.append(f"@{uname} (ID: {v['chat_id']})")
            rows.append([_btn(f"🚫 Revoke @{uname}", f"revoke:{v['chat_id']}")])
        body = "\n".join(lines)
    rows.append([_back("controls")])
    return f"👥 <b>Viewers</b>\n{body}", _kb(rows)


def render_access_request(username: str, chat_id: int) -> tuple[str, dict]:
    uname = html.escape(username or str(chat_id))
    text = f"🔔 New access request\n@{uname} (ID: {chat_id})"
    rows = [[_btn("✅ Allow Viewer", f"allow:{chat_id}"), _btn("❌ Deny", f"deny:{chat_id}")]]
    return text, _kb(rows)
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
python -m pytest tests/test_telegram_views.py -v
```

Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add bot/telegram_views.py tests/test_telegram_views.py
git commit -m "feat: add telegram_views pure rendering functions"
```

---

## Task 3: TelegramMenu core — polling, sessions, access control, read screens

**Files:**
- Create: `bot/telegram_menu.py`
- Create: `tests/test_telegram_menu.py`

### Context

`TelegramMenu` uses `asyncio.to_thread(requests.get/post, ...)` — no new dependencies. Per-session state is `_sessions: dict[int, str]` mapping `chat_id → current_state_key`. `_pending: dict[int, str]` maps `chat_id → username` for unresolved access requests. Viewers are persisted to `data/telegram_viewers.json`.

The polling loop calls `getUpdates` with `timeout=30` (long-polling). The `requests` call must use `timeout=35` (must exceed the Telegram timeout).

Data reading methods (`_screen_*`) read files directly — no caching. Files are small (< 100 KB), so disk I/O is negligible at the ~2 s poll rate.

- [ ] **Step 1: Write failing access-control tests**

```python
# tests/test_telegram_menu.py
import json
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from bot.telegram_menu import TelegramMenu


def _make_menu(tmp_path, owner_id=111):
    risk_manager = MagicMock()
    risk_manager.snapshot.return_value = {
        "hard_stop_active": False,
        "balance": 1000.0,
        "mode": "test",
    }
    symbol_registry = MagicMock()
    symbol_registry.get_symbols.return_value = ["BTCUSDT"]
    symbol_registry.get_disabled.return_value = {}
    symbol_registry.get_paused_symbols.return_value = {}

    return TelegramMenu(
        token="fake_token",
        owner_chat_id=owner_id,
        risk_manager=risk_manager,
        symbol_registry=symbol_registry,
        project_root=tmp_path,
        get_mode=lambda: "test",
        get_active_symbols=lambda: ["BTCUSDT"],
        get_open_orders=lambda: {},
        rank_max=6,
    )


def test_owner_role(tmp_path):
    menu = _make_menu(tmp_path, owner_id=111)
    assert menu._resolve_role(111) == "owner"


def test_viewer_role(tmp_path):
    viewers_path = tmp_path / "data" / "telegram_viewers.json"
    viewers_path.parent.mkdir(parents=True)
    viewers_path.write_text(json.dumps({
        "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
    }))
    menu = _make_menu(tmp_path, owner_id=111)
    assert menu._resolve_role(222) == "viewer"


def test_unknown_role(tmp_path):
    menu = _make_menu(tmp_path, owner_id=111)
    assert menu._resolve_role(999) == "unknown"


def test_approve_viewer_persists(tmp_path):
    menu = _make_menu(tmp_path, owner_id=111)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    menu._pending[333] = "bob"
    menu._approve_viewer(333, "bob")
    assert menu._resolve_role(333) == "viewer"


def test_revoke_viewer(tmp_path):
    viewers_path = tmp_path / "data" / "telegram_viewers.json"
    viewers_path.parent.mkdir(parents=True)
    viewers_path.write_text(json.dumps({
        "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
    }))
    menu = _make_menu(tmp_path, owner_id=111)
    menu._revoke_viewer(222)
    assert menu._resolve_role(222) == "unknown"


def test_viewer_cannot_call_write_action(tmp_path):
    viewers_path = tmp_path / "data" / "telegram_viewers.json"
    viewers_path.parent.mkdir(parents=True)
    viewers_path.write_text(json.dumps({
        "viewers": [{"chat_id": 222, "username": "alice", "added_at": "2026-01-01T00:00:00Z"}]
    }))
    menu = _make_menu(tmp_path, owner_id=111)
    # Viewer trying do_reset should be silently ignored (no exception)
    result = asyncio.get_event_loop().run_until_complete(
        menu._dispatch_callback(222, "viewer", "dummy_qid", "do_reset")
    )
    menu.risk_manager.reset_hard_stop.assert_not_called()
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
python -m pytest tests/test_telegram_menu.py -v
```

Expected: all fail with `ModuleNotFoundError: No module named 'bot.telegram_menu'`

- [ ] **Step 3: Create bot/telegram_menu.py**

```python
# bot/telegram_menu.py
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TYPE_CHECKING

import requests

from bot.telegram_views import (
    render_main_menu, render_status, render_symbols,
    render_symbol_active, render_symbol_disabled, render_symbol_paused,
    render_confirm_pause, render_trades_menu, render_real_orders_menu,
    render_real_open, render_real_history, render_virtual_symbols,
    render_virtual_symbol, render_virtual_history, render_backtest_symbols,
    render_backtest_symbol, render_controls, render_confirm_reset,
    render_paused_for_resume, render_manage_viewers, render_access_request,
)

if TYPE_CHECKING:
    from bot.risk_manager import RiskManager
    from bot.symbol_registry import SymbolRegistry
    from bot.order_executor import OpenOrder

logger = logging.getLogger(__name__)

_WRITE_CALLBACKS = frozenset({
    "do_enable", "do_pause", "do_resume", "do_reset",
    "allow", "deny", "revoke",
})


class TelegramMenu:
    def __init__(
        self,
        token: str,
        owner_chat_id: int,
        risk_manager: "RiskManager",
        symbol_registry: "SymbolRegistry",
        project_root: Path,
        get_mode: Callable[[], str],
        get_active_symbols: Callable[[], list[str]],
        get_open_orders: Callable[[], "dict[str, OpenOrder]"],
        rank_max: int = 6,
    ) -> None:
        self._token = token
        self._owner_id = owner_chat_id
        self.risk_manager = risk_manager
        self._registry = symbol_registry
        self._root = project_root
        self._get_mode = get_mode
        self._get_active_symbols = get_active_symbols
        self._get_open_orders = get_open_orders
        self._rank_max = rank_max

        self._viewers_path = project_root / "data" / "telegram_viewers.json"
        self._sessions: dict[int, str] = {}   # chat_id → state key (unused beyond /start flow)
        self._pending: dict[int, str] = {}    # chat_id → username (pending access requests)

    # ── Polling loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        if not self._token:
            logger.info("TelegramMenu: no token configured, menu disabled")
            return
        offset = 0
        while True:
            try:
                updates = await asyncio.to_thread(self._fetch_updates, offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"TelegramMenu poll error: {exc}")
                await asyncio.sleep(5)

    def _fetch_updates(self, offset: int) -> list[dict]:
        resp = requests.get(
            f"https://api.telegram.org/bot{self._token}/getUpdates",
            params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=35,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", [])

    # ── Update dispatch ─────────────────────────────────────────────────────

    async def _handle_update(self, update: dict) -> None:
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            username = msg.get("from", {}).get("username", "")
            text = msg.get("text", "")
            await self._handle_message(chat_id, username, text)
        elif "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            username = cq.get("from", {}).get("username", "")
            qid = cq["id"]
            data = cq.get("data", "")
            await asyncio.to_thread(self._answer_callback, qid)
            role = self._resolve_role(chat_id)
            await self._dispatch_callback(chat_id, role, qid, data)

    async def _handle_message(self, chat_id: int, username: str, text: str) -> None:
        role = self._resolve_role(chat_id)
        if role == "unknown":
            await self._send(chat_id, "🔒 Access request sent to the bot owner.")
            self._pending[chat_id] = username
            req_text, req_kb = render_access_request(username, chat_id)
            await self._send(self._owner_id, req_text, req_kb)
            return
        t, kb = render_main_menu(is_owner=(role == "owner"))
        await self._send(chat_id, t, kb)

    # ── Callback dispatch ───────────────────────────────────────────────────

    async def _dispatch_callback(
        self, chat_id: int, role: str, qid: str, data: str
    ) -> None:
        # Block write actions for non-owners
        action_prefix = data.split(":")[0]
        if action_prefix in _WRITE_CALLBACKS and role != "owner":
            return

        is_owner = role == "owner"

        # ── Navigation ──────────────────────────────────────────────────────
        if data == "menu":
            t, kb = render_main_menu(is_owner)
        elif data == "status":
            t, kb = await asyncio.to_thread(self._screen_status)
        elif data == "symbols":
            t, kb = self._screen_symbols()
        elif data.startswith("sym:"):
            symbol = data[4:]
            t, kb = self._screen_symbol_detail(symbol, is_owner)
        elif data.startswith("confirm_pause:"):
            t, kb = render_confirm_pause(data[14:])
        elif data == "trades":
            t, kb = render_trades_menu()
        elif data == "real_orders":
            t, kb = render_real_orders_menu()
        elif data == "real_open":
            t, kb = self._screen_real_open()
        elif data == "real_history":
            t, kb = await asyncio.to_thread(self._screen_real_history)
        elif data == "virtual":
            syms = self._get_active_symbols()
            t, kb = render_virtual_symbols(syms)
        elif data.startswith("vsym:"):
            t, kb = await asyncio.to_thread(self._screen_virtual_symbol, data[5:])
        elif data.startswith("vhist:"):
            t, kb = await asyncio.to_thread(self._screen_virtual_history, data[6:])
        elif data == "backtest":
            syms = self._get_active_symbols()
            t, kb = render_backtest_symbols(syms)
        elif data.startswith("btsym:"):
            t, kb = await asyncio.to_thread(self._screen_backtest_symbol, data[6:])
        elif data == "controls":
            t, kb = self._screen_controls()
        elif data == "confirm_reset":
            t, kb = render_confirm_reset()
        elif data == "paused_syms":
            paused = list(self._registry.get_paused_symbols().keys())
            t, kb = render_paused_for_resume(paused)
        elif data == "viewers":
            t, kb = render_manage_viewers(self._load_viewers())

        # ── Write actions ────────────────────────────────────────────────────
        elif data.startswith("do_enable:"):
            symbol = data[10:]
            self._registry.reenable(symbol)
            t, kb = self._screen_symbols()
        elif data.startswith("do_pause:"):
            symbol = data[9:]
            self._registry.pause_symbol(symbol)
            t, kb = self._screen_symbols()
        elif data.startswith("do_resume:"):
            symbol = data[10:]
            self._registry.resume_symbol(symbol)
            t, kb = self._screen_symbols()
        elif data == "do_reset":
            self.risk_manager.reset_hard_stop()
            t, kb = self._screen_controls()
        elif data.startswith("allow:"):
            requester_id = int(data[6:])
            uname = self._pending.pop(requester_id, "")
            self._approve_viewer(requester_id, uname)
            await self._send(requester_id, "✅ Access granted. You have view-only access.")
            viewer_menu, viewer_kb = render_main_menu(is_owner=False)
            await self._send(requester_id, viewer_menu, viewer_kb)
            t, kb = render_manage_viewers(self._load_viewers())
        elif data.startswith("deny:"):
            self._pending.pop(int(data[5:]), None)
            t, kb = render_main_menu(is_owner=True)
        elif data.startswith("revoke:"):
            self._revoke_viewer(int(data[7:]))
            t, kb = render_manage_viewers(self._load_viewers())
        else:
            return

        await self._send(chat_id, t, kb)

    # ── Screen data builders ─────────────────────────────────────────────────

    def _screen_status(self) -> tuple[str, dict]:
        snap = self.risk_manager.snapshot()
        hs_active = snap.get("hard_stop_active", False)
        hs_since = snap.get("hard_stop_triggered_at")
        balance = snap.get("balance", 0.0)
        mode = self._get_mode()

        active = self._get_active_symbols()
        disabled = self._registry.get_disabled()
        paused = self._registry.get_paused_symbols()

        # Uptime from bot_state.json
        uptime_str = "—"
        bot_state_path = self._root / "dashboard" / "public" / "bot_state.json"
        if bot_state_path.exists():
            try:
                bs = json.loads(bot_state_path.read_text())
                started = bs.get("started_at", "")
                if started:
                    delta = datetime.now(timezone.utc) - datetime.fromisoformat(started)
                    h = int(delta.total_seconds() // 3600)
                    d, h = divmod(h, 24)
                    uptime_str = f"{d}d {h}h" if d else f"{h}h"
            except Exception:
                pass

        # Last candle: most recent results_{sym}.json generated_at
        last_sym, last_ago = None, None
        latest_ts: float = 0.0
        results_dir = self._root / "dashboard" / "public"
        for sym in active:
            rpath = results_dir / f"results_{sym}.json"
            if rpath.exists():
                try:
                    d = json.loads(rpath.read_text())
                    gen = d.get("generated_at", "")
                    if gen:
                        ts = datetime.fromisoformat(gen).timestamp()
                        if ts > latest_ts:
                            latest_ts = ts
                            last_sym = sym
                except Exception:
                    pass
        if last_sym and latest_ts:
            ago_s = int(time.time() - latest_ts)
            last_ago = f"{ago_s // 60}m ago" if ago_s >= 60 else f"{ago_s}s ago"

        return render_status(
            mode=mode, balance=balance,
            hard_stop_active=hs_active, hard_stop_since=hs_since,
            n_active=len(active), n_disabled=len(disabled), n_paused=len(paused),
            uptime_str=uptime_str, last_candle_sym=last_sym, last_candle_ago=last_ago,
        )

    def _screen_symbols(self) -> tuple[str, dict]:
        active = self._get_active_symbols()
        disabled = self._registry.get_disabled()
        paused = self._registry.get_paused_symbols()
        return render_symbols(active, disabled, paused)

    def _screen_symbol_detail(self, symbol: str, is_owner: bool) -> tuple[str, dict]:
        disabled = self._registry.get_disabled()
        paused = self._registry.get_paused_symbols()
        if symbol in disabled:
            info = disabled[symbol]
            return render_symbol_disabled(symbol, info.get("reason", ""), info.get("disabled_at", ""), is_owner)
        if symbol in paused:
            info = paused[symbol]
            return render_symbol_paused(symbol, info.get("paused_at", ""), is_owner)
        # Active symbol — try to get current price from results file
        price = 0.0
        best_preset = "—"
        rpath = self._root / "dashboard" / "public" / f"results_{symbol}.json"
        if rpath.exists():
            try:
                d = json.loads(rpath.read_text())
                price = float(d.get("current_price", 0.0))
            except Exception:
                pass
        eff_path = self._root / "data" / f"preset_efficiency_{self._get_mode()}.json"
        if eff_path.exists():
            try:
                eff = json.loads(eff_path.read_text())
                sym_eff = eff.get(symbol, {})
                if sym_eff:
                    best_preset = max(sym_eff, key=lambda k: sym_eff[k].get("total_winning_usdt", 0))
            except Exception:
                pass
        return render_symbol_active(symbol, price, best_preset, is_owner)

    def _screen_real_open(self) -> tuple[str, dict]:
        open_orders = self._get_open_orders()
        orders = [
            {
                "symbol": o.symbol,
                "side": o.side,
                "entry_price": o.entry_price,
                "preset_name": o.preset_name,
                "open_time": o.open_time or "",
            }
            for o in open_orders.values()
        ]
        return render_real_open(orders)

    def _screen_real_history(self) -> tuple[str, dict]:
        mode = self._get_mode()
        orders: list[dict] = []
        for sym in self._get_active_symbols():
            path = self._root / "data" / f"real_orders_{sym}_{mode}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    if isinstance(data, list):
                        orders.extend({"symbol": sym, **o} for o in data)
                except Exception:
                    pass
        orders.sort(key=lambda o: o.get("close_time", ""), reverse=True)
        return render_real_history(orders[:10])

    def _screen_virtual_symbol(self, symbol: str) -> tuple[str, dict]:
        mode = self._get_mode()
        ranks = []
        for rank in range(2, self._rank_max + 1):
            path = self._root / "data" / f"virtual_orders_rank{rank}_{symbol}_{mode}.json"
            preset_name = "—"
            side = None
            pnl_pct = None
            status = "none"
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    if isinstance(data, list) and data:
                        open_o = next((o for o in data if o.get("status") == "open"), None)
                        if open_o:
                            preset_name = open_o.get("preset_name", "—")
                            side = open_o.get("side")
                            pnl_pct = open_o.get("unrealized_pct")
                            status = "open"
                        else:
                            last = data[-1]
                            preset_name = last.get("preset_name", "—")
                except Exception:
                    pass
            ranks.append({"rank": rank, "preset_name": preset_name, "side": side,
                          "pnl_pct": pnl_pct, "status": status})
        return render_virtual_symbol(symbol, ranks)

    def _screen_virtual_history(self, symbol: str) -> tuple[str, dict]:
        mode = self._get_mode()
        orders: list[dict] = []
        for rank in range(2, self._rank_max + 1):
            path = self._root / "data" / f"virtual_orders_rank{rank}_{symbol}_{mode}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    if isinstance(data, list):
                        for o in data:
                            if o.get("status") == "closed":
                                orders.append({"rank": rank, **o})
                except Exception:
                    pass
        orders.sort(key=lambda o: o.get("close_time", ""), reverse=True)
        return render_virtual_history(symbol, orders[:10])

    def _screen_backtest_symbol(self, symbol: str) -> tuple[str, dict]:
        path = self._root / "dashboard" / "public" / f"backtest_results_{symbol}.json"
        top5: list[dict] = []
        if path.exists():
            try:
                data = json.loads(path.read_text())
                presets = data.get("presets", {})
                ranked = sorted(
                    presets.items(),
                    key=lambda kv: kv[1].get("total_profit_pct", 0),
                    reverse=True,
                )
                for name, p in ranked[:5]:
                    top5.append({
                        "name": name,
                        "profit_pct": p.get("total_profit_pct", 0.0),
                        "n_trades": p.get("total_trades", 0),
                        "win_rate": p.get("win_rate", 0.0),
                    })
            except Exception:
                pass
        return render_backtest_symbol(symbol, top5)

    def _screen_controls(self) -> tuple[str, dict]:
        snap = self.risk_manager.snapshot()
        hs_active = snap.get("hard_stop_active", False)
        paused = list(self._registry.get_paused_symbols().keys())
        return render_controls(hs_active, paused)

    # ── Viewer persistence ──────────────────────────────────────────────────

    def _load_viewers(self) -> list[dict]:
        if self._viewers_path.exists():
            try:
                return json.loads(self._viewers_path.read_text()).get("viewers", [])
            except Exception:
                pass
        return []

    def _save_viewers(self, viewers: list[dict]) -> None:
        self._viewers_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._viewers_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"viewers": viewers}, indent=2))
        tmp.replace(self._viewers_path)

    def _resolve_role(self, chat_id: int) -> str:
        if chat_id == self._owner_id:
            return "owner"
        viewers = self._load_viewers()
        if any(v["chat_id"] == chat_id for v in viewers):
            return "viewer"
        return "unknown"

    def _approve_viewer(self, chat_id: int, username: str) -> None:
        viewers = self._load_viewers()
        if not any(v["chat_id"] == chat_id for v in viewers):
            viewers.append({
                "chat_id": chat_id,
                "username": username,
                "added_at": datetime.now(timezone.utc).isoformat(),
            })
            self._save_viewers(viewers)

    def _revoke_viewer(self, chat_id: int) -> None:
        viewers = [v for v in self._load_viewers() if v["chat_id"] != chat_id]
        self._save_viewers(viewers)

    # ── Telegram API ────────────────────────────────────────────────────────

    async def _send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict = {
            "chat_id": chat_id,
            "text": text[:4096],  # Telegram message limit
            "parse_mode": "HTML",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await asyncio.to_thread(
                requests.post,
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json=payload,
                timeout=10,
            )
        except Exception as exc:
            logger.warning(f"TelegramMenu send failed to {chat_id}: {exc}")

    def _answer_callback(self, callback_query_id: str) -> None:
        try:
            requests.post(
                f"https://api.telegram.org/bot{self._token}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id},
                timeout=5,
            )
        except Exception:
            pass
```

- [ ] **Step 4: Run tests — expect all pass**

```bash
python -m pytest tests/test_telegram_menu.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add bot/telegram_menu.py tests/test_telegram_menu.py
git commit -m "feat: add TelegramMenu with polling, access control, and read/write screens"
```

---

## Task 4: main.py integration

**Files:**
- Modify: `main.py`

### Context

Three changes to `main.py`:

1. Import `TelegramMenu`.
2. Instantiate it after `notifier` and `risk_manager` are constructed (around line 125), passing live references.
3. Add `asyncio.create_task(telegram_menu.run())` in the Task Setup block (around line 628) and cancel it in the `finally` block.
4. Add `symbol_registry.is_symbol_paused(sym)` check in the candidate-building loop (around line 489) so paused symbols are skipped.

- [ ] **Step 1: Add import**

Find the existing import block around line 20 in `main.py`. Add after `from bot.notifier import Notifier`:

```python
from bot.telegram_menu import TelegramMenu
```

- [ ] **Step 2: Instantiate TelegramMenu**

Find the block that constructs `risk_manager` (around line 122). After it, add:

```python
    _tg_cfg = risk_cfg.get("telegram", {})
    _tg_token = _tg_cfg.get("token", "")
    _tg_owner_id = int(_tg_cfg.get("chat_id", "0") or "0")
    telegram_menu = TelegramMenu(
        token=_tg_token,
        owner_chat_id=_tg_owner_id,
        risk_manager=risk_manager,
        symbol_registry=symbol_registry,
        project_root=_PROJECT_ROOT,
        get_mode=lambda: mode_manager.current_mode,
        get_active_symbols=symbol_registry.get_symbols,
        get_open_orders=order_executor.get_open_orders,
        rank_max=int(risk_cfg.get("virtual_rank_max", 6)),
    )
```

**Important:** This block must be placed *after* `order_executor` is constructed (around line 139) because it references `order_executor.get_open_orders`. Place it after line 193 (the end of the `VirtualOrderSimulator` construction block).

- [ ] **Step 3: Add background task**

Find the Task Setup block (around line 626-641). Add `_menu_task` alongside the existing tasks:

```python
    _menu_task = asyncio.create_task(telegram_menu.run())
```

In the `finally` block (around line 651), add `_menu_task` to the cancel and await lists:

```python
    finally:
        for t in [_poll_task, _hb_task, _watchdog_task, _menu_task]:
            t.cancel()
        for t in [_poll_task, _hb_task, _watchdog_task, _menu_task]:
            try:
                await t
            except asyncio.CancelledError:
                pass
```

- [ ] **Step 4: Add pause check to order candidate loop**

Find the candidate-building loop (around line 488):

```python
        for sym in symbol_registry.get_symbols():
            if symbol_registry.is_disabled(sym):
                continue
```

Add the pause check immediately after the `is_disabled` check:

```python
        for sym in symbol_registry.get_symbols():
            if symbol_registry.is_disabled(sym):
                continue
            if symbol_registry.is_symbol_paused(sym):
                continue
```

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: all existing tests pass plus the new ones from Tasks 1–3.

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat: integrate TelegramMenu into bot runtime"
```

---

## Task 5: End-to-end smoke test

**Files:**
- No new files — manual validation

### Context

With the bot NOT running, verify the menu works by sending a `/start` to the bot from Telegram. This requires the `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` to be configured in `risk_config.json`.

- [ ] **Step 1: Verify imports don't break startup**

```bash
cd /Users/bohdanpaliichuk/Documents/Projects/My/bin-furures-bot
python -c "from bot.telegram_menu import TelegramMenu; print('OK')"
python -c "from bot.telegram_views import render_main_menu; print(render_main_menu(True)[0])"
```

Expected output:
```
OK
🤖 <b>Bot Menu</b>
Select an action:
```

- [ ] **Step 2: Run complete test suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests pass, no new failures.

- [ ] **Step 3: Final commit — update FEATURES.md and CLAUDE_NOTES.md**

```bash
# Run the librarian agent via Claude Code: /librarian
# Then commit the updated docs
git add FEATURES.md CLAUDE_NOTES.md TODO.md
git commit -m "docs: update FEATURES, NOTES, TODO for Telegram interactive menu"
```
