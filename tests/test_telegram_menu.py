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
    asyncio.get_event_loop().run_until_complete(
        menu._dispatch_callback(222, 0, "viewer", "dummy_qid", "do_reset")
    )
    menu.risk_manager.reset_hard_stop.assert_not_called()
