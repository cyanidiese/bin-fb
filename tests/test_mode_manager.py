# tests/test_mode_manager.py
import json
import asyncio
from pathlib import Path
import pytest
from bot.mode_manager import ModeManager


def _make_mm(tmp_path: Path) -> ModeManager:
    return ModeManager(
        mode_path=tmp_path / "bot_mode.json",
        command_path=tmp_path / "bot_command.json",
        result_path=tmp_path / "bot_command_result.json",
    )


def test_default_mode_is_test(tmp_path):
    mm = _make_mm(tmp_path)
    assert mm.current_mode == "test"


def test_write_and_read_mode(tmp_path):
    mode_path = tmp_path / "bot_mode.json"
    mm = ModeManager(mode_path=mode_path,
                     command_path=tmp_path / "bot_command.json",
                     result_path=tmp_path / "bot_command_result.json")
    mm._write_mode("live")
    assert json.loads(mode_path.read_text())["mode"] == "live"
    mm2 = ModeManager(mode_path=mode_path,
                      command_path=tmp_path / "bot_command.json",
                      result_path=tmp_path / "bot_command_result.json")
    assert mm2.current_mode == "live"


def test_poll_reads_and_clears_command(tmp_path):
    command_path = tmp_path / "bot_command.json"
    mm = _make_mm(tmp_path)
    command_path.write_text(json.dumps(
        {"id": "abc", "type": "stop_bot", "payload": {}, "issued_at": "2026-01-01T00:00:00Z"}
    ))
    cmd = mm._read_and_clear_command()
    assert cmd is not None
    assert cmd["type"] == "stop_bot"
    assert not command_path.exists()


def test_poll_returns_none_when_no_command(tmp_path):
    mm = _make_mm(tmp_path)
    assert mm._read_and_clear_command() is None


def test_in_place_switch_is_gone(tmp_path):
    """Mode switches happen by closing everything and restarting (main.py
    _primary_mode_watch). The half-complete in-place switch must not come back."""
    mm = _make_mm(tmp_path)
    assert not hasattr(mm, 'switch_mode')


@pytest.mark.asyncio
async def test_stop_bot_calls_close(tmp_path):
    mm = _make_mm(tmp_path)
    close_called = []

    async def fake_close():
        close_called.append(True)

    await mm.stop_bot(close_all=fake_close)
    assert close_called == [True]
