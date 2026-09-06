"""Single-owner resources must have exactly one owner: the trading bot.

bot_pid.json drives the dashboard's Stop button, and mode_manager DELETES a command
after reading it (mode_manager.py:73). If the virtual instance owns the PID or eats
the command, pressing Stop reports success while the trading bot keeps running. That
specific failure is what these tests prevent.

Guarded inside the writers rather than at each call site: _write_bot_state is called
from startup, a 10-second heartbeat loop and shutdown, and missing one is the whole
failure mode.
"""
import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _body(fn: str, chars: int = 500) -> str:
    src = MAIN.read_text()
    return src[src.index(f'def {fn}'):][:chars]


def test_pid_write_short_circuits():
    """Whoever owns bot_pid.json is who the dashboard Stop button kills."""
    b = _body('_write_pid')
    assert 'VIRTUAL_ONLY' in b or 'virtual_only' in b
    assert 'return' in b


def test_bot_state_write_short_circuits():
    """The 'is the bot alive' indicator must reflect the trading bot, and a
    virtual-only instance would otherwise overwrite it every 10 seconds."""
    b = _body('_write_bot_state')
    assert 'VIRTUAL_ONLY' in b or 'virtual_only' in b
    assert 'return' in b


def test_command_polling_is_guarded():
    """mode_manager deletes the command file after reading it — consume-once. A Stop
    taken by the virtual instance leaves the trading bot running."""
    src = MAIN.read_text()
    m = re.search(r'poll_loop\(', src)
    assert m, 'poll_loop call not found'
    window = src[max(0, m.start() - 700):m.start()]
    assert 'virtual_only' in window.lower(), 'command polling must be guarded'


def test_the_flag_is_actually_set_from_settings():
    """A module-level flag that nothing assigns would silently guard nothing."""
    src = MAIN.read_text()
    assert re.search(r'_VIRTUAL_ONLY\s*=\s*.*virtual_only', src), \
        'the module flag must be assigned from Settings.virtual_only'


def test_trading_bot_still_writes_both():
    """The guards must be conditional, not a blanket disable."""
    for fn in ('_write_pid', '_write_bot_state'):
        b = _body(fn, 1200)   # wide enough to reach the write past the guard
        assert 'tmp.replace' in b, f'{fn} must still write for the trading bot'
        assert b.index('if _VIRTUAL_ONLY') < b.index('tmp.replace'), \
            f'{fn}: the guard must precede the write, not follow it'
