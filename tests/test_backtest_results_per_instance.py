"""The mirror must never overwrite the primary's backtest results.

RiskManager._compute_perf_score() reads backtest_results_{symbol}.json and derives two
things the trading bot risks real money on:

  intra_score    -> leverage             (risk_manager.py:417)
  raw_profit_pct -> capital allocation   ("a symbol with +22 % profit gets
                                          proportionally more capital")

main.py runs the obligatory startup backtest ungated, passing --mode current_mode, so a
mirror on the live market would rewrite that file and silently resize the testnet bot's
real orders. These tests pin the primary's filename in BOTH modes — the regression to
avoid is that going live hands the trading bot the mirror's numbers.
"""
import os
from pathlib import Path

import backtest
from bot.instance_paths import backtest_results_name, instance_path
from bot.risk_manager import RiskManager

BASE = Path('/x')


# --------------------------------------------------------------------------- #
# instance_path — the shared primitive                                        #
# --------------------------------------------------------------------------- #

def test_primary_keeps_the_historical_name_in_both_modes():
    for mode in ('test', 'live'):
        assert instance_path(BASE, 'x.json', mode, mirror=False) == BASE / 'x.json', mode


def test_mirror_is_suffixed_with_its_own_market():
    assert instance_path(BASE, 'x.json', 'live', mirror=True) == BASE / 'x_live.json'
    assert instance_path(BASE, 'x.json', 'test', mirror=True) == BASE / 'x_test.json'


def test_extensionless_name_still_gets_a_suffix():
    assert instance_path(BASE, 'notes', 'live', mirror=True) == BASE / 'notes_live'


def test_dotted_stem_suffixes_before_the_last_extension():
    assert instance_path(BASE, 'a.b.json', 'live', mirror=True) == BASE / 'a.b_live.json'


# --------------------------------------------------------------------------- #
# backtest_results_name                                                       #
# --------------------------------------------------------------------------- #

def test_results_name_primary_is_unchanged_in_both_modes():
    for mode in ('test', 'live'):
        assert backtest_results_name('INJUSDT', mode, mirror=False) == \
            'backtest_results_INJUSDT.json', mode


def test_results_name_mirror_carries_its_market():
    assert backtest_results_name('INJUSDT', 'live', mirror=True) == \
        'backtest_results_INJUSDT_live.json'
    assert backtest_results_name('INJUSDT', 'test', mirror=True) == \
        'backtest_results_INJUSDT_test.json'


# --------------------------------------------------------------------------- #
# RiskManager — the reader whose output sizes real orders                     #
# --------------------------------------------------------------------------- #

def test_risk_manager_reads_the_primary_file_by_default(tmp_path):
    """Default construction must be byte-identical to today."""
    rm = RiskManager(mode='test', backtest_results_dir=tmp_path)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT.json'


def test_a_live_primary_still_reads_the_unsuffixed_file(tmp_path):
    """The regression to avoid: going live must not make the trading bot read the
    mirror's file."""
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT.json'


def test_risk_manager_in_a_mirror_reads_its_own_file(tmp_path):
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=True)
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT_live.json'


def test_risk_manager_path_follows_a_mode_switch(tmp_path):
    """reset_for_mode_switch reassigns self._mode; the path must track it."""
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=True)
    rm._mode = 'test'
    assert rm._backtest_path('INJUSDT') == tmp_path / 'backtest_results_INJUSDT_test.json'


def test_perf_score_reads_through_the_accessor(tmp_path):
    """A mirror must not silently fall back to the primary's file if the accessor is
    bypassed somewhere in _compute_perf_score."""
    (tmp_path / 'backtest_results_INJUSDT.json').write_text(
        '{"presets": {"p": {"total_trades": 999, "total_profit_pct": 99.0, '
        '"profit_factor": 9.0}}}')
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=True)
    score, pf, raw = rm._compute_perf_score('INJUSDT')
    assert raw is None, "the mirror read the primary file"


# --------------------------------------------------------------------------- #
# backtest.py — the writer                                                    #
# --------------------------------------------------------------------------- #

def test_backtest_writes_the_primary_name_by_default(monkeypatch):
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    monkeypatch.setenv('TRADING_MODE', 'test')
    assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT.json'


def test_backtest_writes_the_primary_name_in_live_mode(monkeypatch):
    """A genuinely live primary keeps the name the dashboard and RiskManager read."""
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    monkeypatch.setenv('TRADING_MODE', 'live')
    assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT.json'


def test_backtest_writes_the_mirror_name_when_virtual_only(monkeypatch):
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'live')
    assert backtest._dashboard_path('INJUSDT').name == \
        'backtest_results_INJUSDT_live.json'


def test_backtest_mirror_in_test_mode(monkeypatch):
    monkeypatch.setenv('VIRTUAL_ONLY', 'true')
    monkeypatch.setenv('TRADING_MODE', 'test')
    assert backtest._dashboard_path('INJUSDT').name == \
        'backtest_results_INJUSDT_test.json'


def test_backtest_treats_legacy_testnet_as_test(monkeypatch):
    """load_settings() accepts TRADING_MODE=testnet as a deprecated alias; the filename
    must agree with it rather than inventing a third name."""
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'testnet')
    assert backtest._dashboard_path('INJUSDT').name == \
        'backtest_results_INJUSDT_test.json'


def test_backtest_reads_env_at_call_time_not_import_time(monkeypatch):
    """--mode sets os.environ before settings load; the path must see that value."""
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'test')
    first = backtest._dashboard_path('INJUSDT').name
    monkeypatch.setenv('TRADING_MODE', 'live')
    second = backtest._dashboard_path('INJUSDT').name
    assert first != second, 'the path was frozen at import'


# --------------------------------------------------------------------------- #
# main.py wiring                                                              #
# --------------------------------------------------------------------------- #

def test_main_seeds_the_tracker_from_its_own_instance_file():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'backtest_results_name(' in src, \
        'seed_from_backtest must use the instance-aware name'
    assert 'f"backtest_results_{sym}.json"' not in src, \
        'an unsuffixed literal remains — the mirror would seed from the primary'


def test_main_passes_mirror_to_risk_manager():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    ctor = src[src.index('risk_manager = RiskManager('):]
    # slice to the closing paren at statement indentation — the argument list now
    # contains nested calls, so the first ')' is not the end of the constructor
    ctor = ctor[:ctor.index('\n    )')]
    assert 'mirror=' in ctor, 'RiskManager must know which instance it serves'
