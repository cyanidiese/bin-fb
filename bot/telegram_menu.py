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
        self._sessions: dict[int, str] = {}
        self._pending: dict[int, str] = {}

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
        action_prefix = data.split(":")[0]
        if action_prefix in _WRITE_CALLBACKS and role != "owner":
            return

        is_owner = role == "owner"

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
            "text": text[:4096],
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
