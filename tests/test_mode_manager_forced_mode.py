"""A virtual-only instance is not mode-switchable — it IS its configured mode.

current_mode drives every data file path. It normally reads data/bot_mode.json, which
the dashboard can rewrite; without an override the live instance would name its files
after whatever that file says instead of its own configured mode.
"""
import json

from bot.mode_manager import ModeManager


def test_forced_mode_ignores_the_mode_file(tmp_path):
    mode_file = tmp_path / 'bot_mode.json'
    mode_file.write_text(json.dumps({'mode': 'test'}))
    m = ModeManager(mode_path=mode_file, forced_mode='live')
    assert m.current_mode == 'live', 'the mode file must not retarget this instance'


def test_forced_mode_works_when_the_file_is_absent(tmp_path):
    m = ModeManager(mode_path=tmp_path / 'missing.json', forced_mode='live')
    assert m.current_mode == 'live'


def test_without_forced_mode_behaviour_is_unchanged(tmp_path):
    """The testnet bot must keep reading the file exactly as before."""
    mode_file = tmp_path / 'bot_mode.json'
    mode_file.write_text(json.dumps({'mode': 'test'}))
    assert ModeManager(mode_path=mode_file).current_mode == 'test'


def test_absent_file_still_falls_back_to_test(tmp_path):
    assert ModeManager(mode_path=tmp_path / 'missing.json').current_mode == 'test'
