"""Backtest results belong to a MARKET, not to an instance.

RiskManager._compute_perf_score() reads a symbol's backtest results and derives what the
trading bot risks real money on — leverage (intra_score) and cross-symbol capital
allocation (raw_profit_pct) — and the virtual tracker seeds presets from the same file.

It used to be keyed by instance ("whoever is primary"): after a mode switch the primary
sized orders from the previous market's backtest, the dashboard could only refresh the
primary's copy (the mirror's 15 files were 19 days old, 7 symbols had none), and a
dashboard backtest of the other mode overwrote the trading bot's file.
Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md
"""
import json
from pathlib import Path

import backtest
from bot.instance_paths import backtest_results_name, backtest_results_path, instance_path
from bot.risk_manager import RiskManager

BASE = Path('/x')


# --------------------------------------------------------------------------- #
# instance_path — still the primitive for display files (risk_state, charts)  #
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
# backtest results: named by mode                                             #
# --------------------------------------------------------------------------- #

def test_results_name_is_the_market():
    assert backtest_results_name('INJUSDT', 'test') == 'backtest_results_INJUSDT_test.json'
    assert backtest_results_name('INJUSDT', 'live') == 'backtest_results_INJUSDT_live.json'


def test_test_falls_back_to_the_legacy_file(tmp_path):
    (tmp_path / 'backtest_results_INJUSDT.json').write_text('{}')
    assert backtest_results_path(tmp_path, 'INJUSDT', 'test').name == 'backtest_results_INJUSDT.json'
    (tmp_path / 'backtest_results_INJUSDT_test.json').write_text('{}')
    assert backtest_results_path(tmp_path, 'INJUSDT', 'test').name == 'backtest_results_INJUSDT_test.json'


def test_live_never_falls_back_to_the_testnet_file(tmp_path):
    """The legacy file was always the testnet primary's; reading it for live would size
    live orders from the testnet market."""
    (tmp_path / 'backtest_results_INJUSDT.json').write_text('{}')
    assert backtest_results_path(tmp_path, 'INJUSDT', 'live').name == 'backtest_results_INJUSDT_live.json'


# --------------------------------------------------------------------------- #
# RiskManager — the reader whose output sizes real orders                     #
# --------------------------------------------------------------------------- #

def test_risk_manager_reads_its_market_file(tmp_path):
    for mirror in (False, True):
        assert RiskManager(mode='test', backtest_results_dir=tmp_path, mirror=mirror) \
            ._backtest_path('INJUSDT').name == 'backtest_results_INJUSDT_test.json'
        assert RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=mirror) \
            ._backtest_path('INJUSDT').name == 'backtest_results_INJUSDT_live.json'


def test_a_live_primary_ignores_the_testnet_results(tmp_path):
    (tmp_path / 'backtest_results_INJUSDT.json').write_text(json.dumps(
        {"presets": {"p": {"total_trades": 999, "total_profit_pct": 99.0, "profit_factor": 9.0}}}))
    rm = RiskManager(mode='live', backtest_results_dir=tmp_path)
    score, pf, raw = rm._compute_perf_score('INJUSDT')
    assert raw is None, "a live primary read the testnet backtest"


# --------------------------------------------------------------------------- #
# backtest.py — the writer                                                    #
# --------------------------------------------------------------------------- #

def test_backtest_writes_the_market_file_from_any_instance(monkeypatch):
    for virtual in ('0', '1'):
        monkeypatch.setenv('VIRTUAL_ONLY', virtual)
        for mode in ('test', 'live'):
            monkeypatch.setenv('TRADING_MODE', mode)
            assert backtest._dashboard_path('INJUSDT').name == \
                f'backtest_results_INJUSDT_{mode}.json'


def test_backtest_treats_legacy_testnet_as_test(monkeypatch):
    monkeypatch.setenv('TRADING_MODE', 'testnet')
    assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT_test.json'


def test_chart_export_goes_to_the_instance_running_that_mode(monkeypatch, tmp_path):
    """A dashboard backtest of the other mode must not overwrite the trading bot's chart."""
    import bot.mode_manager as mm
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    monkeypatch.setattr(backtest, 'read_primary_running_mode', lambda: 'test')
    monkeypatch.setenv('TRADING_MODE', 'test')
    assert backtest._is_mirror() is False
    monkeypatch.setenv('TRADING_MODE', 'live')
    assert backtest._is_mirror() is True
    monkeypatch.setenv('VIRTUAL_ONLY', '1')
    monkeypatch.setenv('TRADING_MODE', 'test')
    assert backtest._is_mirror() is True


def test_backtest_does_not_need_api_keys(monkeypatch):
    """A live-mode backtest must work before live keys exist."""
    from config.settings import load_settings
    monkeypatch.setenv('TRADING_MODE', 'live')
    monkeypatch.delenv('API_KEY', raising=False)
    monkeypatch.delenv('API_SECRET', raising=False)
    monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
    s = load_settings('INJUSDT', require_keys=False)
    assert s.trading_mode == 'live' and s.api_key == ''


def test_backtest_stores_production_klines_in_the_production_cache():
    src = (Path(__file__).resolve().parents[1] / 'backtest.py').read_text()
    assert "feed.set_cache_suffix('live')" in src
    assert "_{settings.timeframe}_live.json'" in src


def test_feed_refuses_production_klines_into_the_testnet_cache():
    import pytest
    from bot.data_feed import DataFeed
    feed = DataFeed.__new__(DataFeed)
    feed._klines_source = 'production'
    feed._mode_suffix = 'live'
    with pytest.raises(ValueError):
        feed.set_cache_suffix('test')


# --------------------------------------------------------------------------- #
# main.py wiring                                                              #
# --------------------------------------------------------------------------- #

def test_main_seeds_the_tracker_from_its_market_file():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'backtest_results_path(' in src
    assert 'f"backtest_results_{sym}.json"' not in src
