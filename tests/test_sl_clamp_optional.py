"""Clamping an over-wide stop is opt-in, per symbol, and OFF by default.

Rejecting the signal is a two-month-old, data-backed behaviour. Switching every symbol
to clamping in one deploy would change what the bot trades without a controlled
comparison, so the flag ships disabled and is turned on per symbol via
`risk_config.per_symbol_settings.<SYMBOL>.sl_clamp_enabled`, alongside the `max_sl_pct`
that becomes the clamp target.
"""
import dataclasses
from pathlib import Path

import pytest

from config.settings import Settings, clamp_sl_to_max, load_settings

ROOT = Path(__file__).resolve().parents[1]


def test_the_flag_exists_on_settings():
    """It must live on Settings, because per_symbol_settings is applied with
    dataclasses.replace and silently drops keys that are not fields."""
    assert 'sl_clamp_enabled' in {f.name for f in dataclasses.fields(Settings)}


def test_it_defaults_to_off(monkeypatch):
    monkeypatch.delenv('SL_CLAMP_ENABLED', raising=False)
    monkeypatch.setenv('TRADING_MODE', 'test')
    monkeypatch.setenv('TESTNET_API_KEY', 'k')
    monkeypatch.setenv('TESTNET_API_SECRET', 's')
    monkeypatch.setenv('SYMBOL', 'INJUSDT')
    assert load_settings().sl_clamp_enabled is False, \
        'shipping this on would change what the bot trades without a comparison'


def test_the_env_var_can_turn_it_on(monkeypatch):
    for v in ('1', 'true', 'yes', 'TRUE'):
        monkeypatch.setenv('SL_CLAMP_ENABLED', v)
        monkeypatch.setenv('TRADING_MODE', 'test')
        monkeypatch.setenv('TESTNET_API_KEY', 'k')
        monkeypatch.setenv('TESTNET_API_SECRET', 's')
        monkeypatch.setenv('SYMBOL', 'INJUSDT')
        assert load_settings().sl_clamp_enabled is True, v


def test_it_is_settable_per_symbol_via_dataclasses_replace(monkeypatch):
    """The exact mechanism risk_config uses: per_symbol_settings -> replace().

    Built through load_settings() rather than a synthetic Settings, so this cannot pass
    against a hand-made object that does not match the real one.
    """
    monkeypatch.delenv('SL_CLAMP_ENABLED', raising=False)
    monkeypatch.setenv('TRADING_MODE', 'test')
    monkeypatch.setenv('TESTNET_API_KEY', 'k')
    monkeypatch.setenv('TESTNET_API_SECRET', 's')
    monkeypatch.setenv('SYMBOL', 'INJUSDT')
    base = load_settings()
    assert base.sl_clamp_enabled is False

    # exactly what main.py does with per_symbol_settings
    overrides = {'sl_clamp_enabled': True, 'max_sl_pct': 12.0}
    valid = {f.name for f in dataclasses.fields(Settings)}
    tuned = dataclasses.replace(base, **{k: v for k, v in overrides.items() if k in valid})

    assert tuned.sl_clamp_enabled is True, 'per-symbol enable did not take'
    assert tuned.max_sl_pct == 12.0, 'per-symbol percent did not take'
    assert base.sl_clamp_enabled is False, 'replace must not mutate the original'


# --------------------------------------------------------------------------- #
# All three engines must branch on the flag, and reject when it is off        #
# --------------------------------------------------------------------------- #

ENGINES = ('main.py', 'bot/virtual_order_simulator.py', 'bot/backtester.py')


@pytest.mark.parametrize('path', ENGINES)
def test_every_engine_branches_on_the_flag(path):
    src = (ROOT / path).read_text()
    assert 'sl_clamp_enabled' in src, f'{path} does not consult the flag'


@pytest.mark.parametrize('path', ENGINES)
def test_every_engine_still_has_a_reject_path(path):
    """With the flag off, behaviour must be exactly what ships today."""
    src = (ROOT / path).read_text()
    assert 'clamp_sl_to_max' in src, f'{path} lost the clamp'
    if path == 'main.py':
        assert "decision='skip_max_sl_pct'" in src, 'main.py lost the reject path'
    else:
        assert 'max_sl_pct' in src, f'{path} lost the reject path'


def test_the_helper_itself_is_unconditional():
    """clamp_sl_to_max stays a pure function — the flag is the caller's decision, so
    the helper can be reasoned about and tested on its own."""
    import inspect
    src = inspect.getsource(clamp_sl_to_max)
    assert 'sl_clamp_enabled' not in src


def test_clamping_is_still_correct_when_enabled():
    sl, pct, clamped = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
    assert (round(sl, 6), pct, clamped) == (88.0, 12.0, True)
