"""Per-instance files must not interleave between the two bots.

The suffix is keyed on which instance writes, not on the mode: the primary owns the
unsuffixed name in either mode, so every existing reader is untouched whichever mode the
bot is switched to. See bot/instance_paths.py for why that distinction matters.

risk_state.json is the one with teeth — the mirror has balance 0, so sharing the file
would blank the trading bot's risk page.
"""
import os
from pathlib import Path

import main
from bot.exporter import _results_path
from bot.mode_manager import opposite_mode, read_mode_file
from main import _instance_path, _log_paths

MAIN_SRC = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
BASE = Path('/x')
NAMES = ('risk_state.json', 'alert_state.json', 'system_log.json',
         'analysis.jsonl', 'bot.log', 'trades.log')


# --------------------------------------------------------------------------- #
# The primitive, re-exported so main.py has one name for it                   #
# --------------------------------------------------------------------------- #

def test_primary_keeps_the_historical_name_in_test_mode():
    for n in NAMES:
        assert _instance_path(BASE, n, 'test', mirror=False) == BASE / n, n


def test_primary_keeps_the_historical_name_in_live_mode():
    """The regression this rule exists to prevent: going live must not hand the mirror
    the files the dashboard reads."""
    for n in NAMES:
        assert _instance_path(BASE, n, 'live', mirror=False) == BASE / n, n


def test_mirror_is_always_suffixed():
    assert _instance_path(BASE, 'risk_state.json', 'live', mirror=True) == \
        BASE / 'risk_state_live.json'
    assert _instance_path(BASE, 'risk_state.json', 'test', mirror=True) == \
        BASE / 'risk_state_test.json'
    assert _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True) == \
        BASE / 'analysis_live.jsonl'
    assert _instance_path(BASE, 'bot.log', 'test', mirror=True) == BASE / 'bot_test.log'


def test_mirror_suffix_tracks_its_own_market():
    """After a flip the mirror must start a new file, not append live-market data to a
    test-market one."""
    assert _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True) != \
        _instance_path(BASE, 'analysis.jsonl', 'test', mirror=True)


# --------------------------------------------------------------------------- #
# Log paths, resolved before Settings or ModeManager exist                    #
# --------------------------------------------------------------------------- #

def test_primary_log_paths_are_the_historical_ones(monkeypatch):
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    bot_log, trades_log = _log_paths()
    assert bot_log == Path('logs') / 'bot.log'
    assert trades_log == Path('logs') / 'trades.log'


def test_mirror_log_paths_carry_its_market(monkeypatch):
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    bot_log, trades_log = _log_paths()
    expected = opposite_mode(read_mode_file())
    assert bot_log == Path('logs') / f'bot_{expected}.log'
    assert trades_log == Path('logs') / f'trades_{expected}.log'


def test_two_rotating_handlers_never_share_a_file(monkeypatch):
    """Two RotatingFileHandlers on one file collide during rollover, even though the
    mirror logs no trades."""
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    primary = _log_paths()
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    mirror = _log_paths()
    assert set(primary).isdisjoint(set(mirror))


# --------------------------------------------------------------------------- #
# results_{symbol}.json                                                        #
# --------------------------------------------------------------------------- #

def test_results_primary_writes_only_the_name_everything_reads():
    """Verified readers: telegram_menu.py:257/324, exporter.py, page.tsx:62,
    trades/page.tsx:204, api/symbols/[symbol]/route.ts:24 — all unsuffixed."""
    for mode in ('test', 'live'):
        assert _results_path('INJUSDT', mode, mirror=False) == \
            Path('dashboard/public/results_INJUSDT.json'), mode


def test_results_mirror_never_writes_the_unsuffixed_name():
    for mode in ('test', 'live'):
        got = _results_path('INJUSDT', mode, mirror=True)
        assert got == Path(f'dashboard/public/results_INJUSDT_{mode}.json')
        assert got != Path('dashboard/public/results_INJUSDT.json')


def test_export_accepts_mirror_and_defaults_to_the_primary():
    import inspect
    from bot.exporter import export
    sig = inspect.signature(export)
    assert 'mirror' in sig.parameters, 'export must know which instance calls it'
    assert sig.parameters['mirror'].default is False, \
        'defaulting to True would retarget the trading bot'


# --------------------------------------------------------------------------- #
# Wiring: no unsuffixed literal may survive for a category-B file             #
# --------------------------------------------------------------------------- #

def test_no_category_b_path_is_built_without_instance_path():
    """The bug shape is joining the name straight onto a directory.

    Passing the name TO _instance_path is correct and must stay allowed, so this checks
    for the direct-join and bare-string forms rather than for the name appearing at all.
    """
    bad = []
    for name in ('bot.log', 'trades.log', 'analysis.jsonl',
                 'system_log.json', 'alert_state.json', 'risk_state.json'):
        for form in (f"/ '{name}'", f'/ "{name}"',
                     f"'logs/{name}'", f'"logs/{name}"'):
            if form in MAIN_SRC:
                bad.append(form)
    assert not bad, f"category-B paths built without _instance_path: {bad}"


def test_risk_manager_gets_an_explicit_state_path():
    ctor = MAIN_SRC[MAIN_SRC.index('risk_manager = RiskManager('):]
    ctor = ctor[:ctor.index('\n    )')]
    assert 'state_path=' in ctor, \
        'risk_state.json defaults to the shared path — the mirror would blank it'


def test_both_export_call_sites_pass_mirror():
    count = MAIN_SRC.count('mirror=_virtual_only')
    assert count >= 2, f'expected both export sites to pass mirror, found {count}'


def test_the_instance_mode_agrees_with_mode_manager():
    """_instance_mode names the notifier's files and is computed before ModeManager
    exists; if the two ever disagreed, one instance would split its output across two
    suffixes."""
    assert '_instance_mode' in MAIN_SRC
    assert 'assert _instance_mode == mode_manager.current_mode' in MAIN_SRC or \
        '_instance_mode != mode_manager.current_mode' in MAIN_SRC, \
        'the agreement between the two mode resolutions must be checked, not assumed'
