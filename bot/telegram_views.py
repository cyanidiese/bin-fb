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
    hs_since = html.escape(hard_stop_since or "")
    hs = f"⛔ ACTIVE since {hs_since}" if hard_stop_active else "✅ clear"
    lc_sym = html.escape(last_candle_sym or "")
    lc_ago = html.escape(last_candle_ago or "")
    lc = f"{lc_sym} {lc_ago}" if last_candle_sym else "—"
    lines = [
        "📊 <b>Bot Status</b>",
        f"Mode: <b>{html.escape(mode)}</b>  |  Balance: <b>{balance:,.2f} USDT</b>",
        f"Symbols: {n_active} active, {n_disabled} disabled, {n_paused} paused",
        f"Hard stop: {hs}",
        f"Uptime: {html.escape(uptime_str)}  |  Last candle: {lc}",
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
    parts = ["🔤 <b>Symbols</b>"]
    if active:
        parts.append(f"Active ({len(active)}): {', '.join(html.escape(s) for s in active)}")
    if disabled:
        parts.append(f"Disabled ({len(disabled)}): {', '.join(html.escape(s) for s in disabled)}")
    if paused:
        parts.append(f"Paused ({len(paused)}): {', '.join(html.escape(s) for s in paused)}")
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
            lines.append(
                f"<b>{html.escape(o['symbol'])}</b> {html.escape(str(o.get('side', '')))}  "
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
                f"{emoji} <b>{html.escape(o['symbol'])}</b> {html.escape(str(o.get('side', '')))}  "
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
                f"{html.escape(str(r.get('side') or ''))} {pct}  open"
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
                f"{html.escape(str(o.get('side', '')))}  {pnl}"
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
