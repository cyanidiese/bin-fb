"""Risk config, per-mode config and trading-mode handling.

Sections (one class per former test file):
- TestRiskConfig              — load/save/defaults of risk_config.json
- TestPerModeRiskConfig       — risk_config_test.json / risk_config_live.json
- TestSharedSettings          — risk_config_shared.json (one value for both modes)
- TestLockedPresetsPerMode    — locked presets keyed by trading mode
- TestBacktestResultsPerMode  — backtest results belong to a market, not an instance
- TestPerInstancePaths        — per-instance files never interleave between the two bots
- TestModeManager             — ModeManager basics
- TestModeSwitch              — coordinated mode switch (close out, prove flat, restart)
- TestRecalcScriptRanges      — preset Profit% store covers every Trades date shortcut
"""
import asyncio
import importlib.util
import inspect
import json
import re
from pathlib import Path

import pytest

import backtest
import main
from bot.exporter import _results_path
from bot.instance_paths import backtest_results_name, backtest_results_path, instance_path
from bot.mode_manager import (ModeManager, instance_mode, opposite_mode, read_mode_file,
                              read_primary_running_mode)
from bot.mode_switch import close_out
from bot.risk_manager import RiskManager
from config import risk_config as rc
from config.risk_config import load_risk_config, locked_presets_for, save_risk_config
from main import _instance_path, _log_paths
from tests.factories import ROOT, src

MAIN_SRC = src('main.py')
BASE = Path('/x')


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Point config.risk_config at an empty tmp dir with no active mode."""
    monkeypatch.setattr(rc, '_ROOT', tmp_path)
    monkeypatch.setattr(rc, '_active_mode', None)
    monkeypatch.delenv('TRADING_MODE', raising=False)
    return tmp_path


# =========================================================================== #
# risk_config.json basics                                                     #
# =========================================================================== #

class TestRiskConfig:
    def test_load_creates_file_when_missing(self, tmp_path):
        p = tmp_path / "risk_config.json"
        cfg = load_risk_config(p)
        assert p.exists()
        assert cfg["base_leverage"] == 2
        assert cfg["min_profit_factor"] == 1.2
        assert len(cfg["balance_tiers"]) == 3

    def test_load_merges_missing_keys(self, tmp_path):
        p = tmp_path / "risk_config.json"
        p.write_text(json.dumps({"base_leverage": 5}))
        cfg = load_risk_config(p)
        # New key from defaults appears
        assert "min_profit_factor" in cfg
        # Existing key preserved
        assert cfg["base_leverage"] == 5

    def test_save_and_reload(self, tmp_path):
        p = tmp_path / "risk_config.json"
        cfg = load_risk_config(p)
        cfg["base_leverage"] = 7
        save_risk_config(cfg, p)
        cfg2 = load_risk_config(p)
        assert cfg2["base_leverage"] == 7

    def test_corrupt_file_returns_defaults(self, tmp_path):
        p = tmp_path / "risk_config.json"
        p.write_text("not json{{{")
        cfg = load_risk_config(p)
        assert cfg["base_leverage"] == 2

    def test_new_defaults_present(self, tmp_path):
        path = tmp_path / "risk_config.json"
        cfg = load_risk_config(path)
        assert "telegram" in cfg
        assert cfg["telegram"] == {"token": "", "chat_id": ""}
        assert cfg["min_balance_pct"] == 15.0
        assert cfg["consecutive_failure_threshold"] == 3
        assert cfg["test_starting_balance_usdt"] == 10000.0
        assert cfg["max_leverage"] == 20
        assert cfg["price_stale_threshold_s"] == 15

    def test_existing_file_missing_new_keys_gets_defaults(self, tmp_path):
        path = tmp_path / "risk_config.json"
        # Write file without new keys
        path.write_text('{"drawdown_warning_pct": 10.0}')
        cfg = load_risk_config(path)
        assert cfg["drawdown_warning_pct"] == 10.0
        assert cfg["telegram"] == {"token": "", "chat_id": ""}
        assert cfg["consecutive_failure_threshold"] == 3


# =========================================================================== #
# Per-mode risk config                                                        #
# =========================================================================== #

def _write_mode_cfg(root: Path, mode: str, cfg: dict) -> None:
    (root / f'risk_config_{mode}.json').write_text(json.dumps(cfg))


class TestPerModeRiskConfig:
    """Per-mode risk config: risk_config_test.json / risk_config_live.json.

    Spec: docs/specs/2026-09-26-per-mode-risk-config.md. A weight edited in one mode must not
    move in the other; live falls back to test key by key; locks never leak across modes.
    """

    def test_each_mode_reads_its_own_file(self, root):
        _write_mode_cfg(root, 'test', {'symbol_weights': {'INJUSDT': 9}})
        _write_mode_cfg(root, 'live', {'symbol_weights': {'INJUSDT': 8}})
        assert rc.load_risk_config(mode='test')['symbol_weights'] == {'INJUSDT': 9}
        assert rc.load_risk_config(mode='live')['symbol_weights'] == {'INJUSDT': 8}

    def test_saving_one_mode_leaves_the_other_untouched(self, root):
        _write_mode_cfg(root, 'test', {'symbol_weights': {'INJUSDT': 8}})
        _write_mode_cfg(root, 'live', {'symbol_weights': {'INJUSDT': 8}})
        cfg = rc.load_risk_config(mode='test')
        cfg['symbol_weights']['INJUSDT'] = 9
        rc.save_risk_config(cfg, mode='test')
        assert rc.load_risk_config(mode='test')['symbol_weights']['INJUSDT'] == 9
        assert rc.load_risk_config(mode='live')['symbol_weights']['INJUSDT'] == 8

    def test_live_falls_back_to_test_key_by_key(self, root):
        _write_mode_cfg(root, 'test', {'max_leverage': 7, 'scenario': 'tats'})
        _write_mode_cfg(root, 'live', {'max_leverage': 3})
        live = rc.load_risk_config(mode='live')
        assert live['max_leverage'] == 3          # live's own value wins
        assert live['scenario'] == 'tats'         # missing in live → test's
        assert 'scenario' not in json.loads((root / 'risk_config_live.json').read_text())

    def test_missing_live_file_reads_test(self, root):
        _write_mode_cfg(root, 'test', {'max_leverage': 7})
        assert rc.load_risk_config(mode='live')['max_leverage'] == 7
        assert not (root / 'risk_config_live.json').exists()   # never created on read

    def test_test_never_falls_back_to_live(self, root):
        _write_mode_cfg(root, 'live', {'max_leverage': 3})
        assert rc.load_risk_config(mode='test')['max_leverage'] == rc.DEFAULT_CONFIG['max_leverage']

    def test_test_locks_never_leak_into_live(self, root):
        _write_mode_cfg(root, 'test', {'locked_presets': {'test': {'SOLUSDT': 'r5_arm25'}}})
        _write_mode_cfg(root, 'live', {})   # no locked_presets key → falls back to test's dict
        live = rc.load_risk_config(mode='live')
        assert rc.locked_presets_for(live, 'live') == {}
        assert rc.locked_presets_for(rc.load_risk_config(mode='test'), 'test') == {'SOLUSDT': 'r5_arm25'}

    def test_active_mode_drives_the_default_path(self, root, monkeypatch):
        _write_mode_cfg(root, 'test', {'max_leverage': 7})
        _write_mode_cfg(root, 'live', {'max_leverage': 3})
        assert rc.load_risk_config()['max_leverage'] == 7          # default: test
        monkeypatch.setenv('TRADING_MODE', 'live')
        assert rc.load_risk_config()['max_leverage'] == 3          # env fallback
        rc.set_active_mode('test')
        assert rc.load_risk_config()['max_leverage'] == 7          # explicit wins over env
        with pytest.raises(ValueError):
            rc.set_active_mode('paper')

    def test_explicit_per_mode_path_keeps_fallback(self, root):
        _write_mode_cfg(root, 'test', {'scenario': 'tats'})
        _write_mode_cfg(root, 'live', {'max_leverage': 3})
        live = rc.load_risk_config(root / 'risk_config_live.json')
        assert live['scenario'] == 'tats' and live['max_leverage'] == 3

    def test_split_script_seeds_both_files(self, tmp_path, monkeypatch):
        spec = importlib.util.spec_from_file_location(
            'split', ROOT / 'scripts/split_risk_config.py')
        split = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(split)
        monkeypatch.setattr(split, 'ROOT', tmp_path)
        monkeypatch.setattr(split, 'LEGACY', tmp_path / 'risk_config.json')
        (tmp_path / 'risk_config.json').write_text(json.dumps({
            'symbol_weights': {'INJUSDT': 9},
            'locked_presets': {'test': {'SOLUSDT': 'a'}, 'live': {'INJUSDT': 'b'}},
        }))
        monkeypatch.setattr('sys.argv', ['split', '--apply'])
        assert split.main() == 0
        test = json.loads((tmp_path / 'risk_config_test.json').read_text())
        live = json.loads((tmp_path / 'risk_config_live.json').read_text())
        assert test['symbol_weights'] == live['symbol_weights'] == {'INJUSDT': 9}
        assert test['locked_presets'] == {'test': {'SOLUSDT': 'a'}}
        assert live['locked_presets'] == {'live': {'INJUSDT': 'b'}}
        # never overwrites
        (tmp_path / 'risk_config_live.json').write_text(json.dumps({'symbol_weights': {'X': 1}}))
        split.main()
        assert json.loads((tmp_path / 'risk_config_live.json').read_text()) == {'symbol_weights': {'X': 1}}


# =========================================================================== #
# Shared settings                                                             #
# =========================================================================== #

def _write_cfg_file(root: Path, name: str, cfg: dict) -> None:
    (root / name).write_text(json.dumps(cfg))


def _load_shared_split(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'split2', ROOT / 'scripts/split_shared_and_registry.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/risk_config.py').write_text(src('config/risk_config.py'))
    monkeypatch.setattr(mod, 'ROOT', tmp_path)
    monkeypatch.setattr('sys.argv', ['split', '--apply'])
    return mod


LEGACY_REGISTRY = {
    'symbols': ['INJUSDT', 'SOLUSDT', 'WLDUSDT'],
    'status': {'INJUSDT': {'backtest': 'complete', 'pid': None}},
    'weights': {'INJUSDT': 1, 'SOLUSDT': 1, 'WLDUSDT': 0},
    'disabled': {'WLDUSDT': {'reason': 'losses', 'disabled_at': '2026-06-02'}},
    'disabled_ranks': {}, 'paused': {},
}


class TestSharedSettings:
    """risk_config_shared.json — one value for both modes.

    Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md. Signal filters,
    preset ranking, Telegram and backtest settings are shared; weights, leverage and loss
    limits stay per mode.
    """

    def test_shared_value_applies_to_both_modes(self, root):
        _write_cfg_file(root, 'risk_config_test.json', {'global_min_rr': 2, 'base_leverage': 4})
        _write_cfg_file(root, 'risk_config_live.json', {'global_min_rr': 2, 'base_leverage': 2})
        _write_cfg_file(root, 'risk_config_shared.json', {'global_min_rr': 3})
        for mode in ('test', 'live'):
            assert rc.load_risk_config(mode=mode)['global_min_rr'] == 3
        # per-mode keys untouched
        assert rc.load_risk_config(mode='test')['base_leverage'] == 4
        assert rc.load_risk_config(mode='live')['base_leverage'] == 2

    def test_shared_file_wins_over_a_stale_mode_copy(self, root):
        _write_cfg_file(root, 'risk_config_live.json', {'preset_blocklist': ['old']})
        _write_cfg_file(root, 'risk_config_shared.json', {'preset_blocklist': ['new']})
        assert rc.load_risk_config(mode='live')['preset_blocklist'] == ['new']

    def test_non_shared_keys_in_the_shared_file_are_ignored(self, root):
        """Only SHARED_KEYS are read from it, so a stray weight can never leak across modes."""
        _write_cfg_file(root, 'risk_config_test.json', {'symbol_weights': {'INJUSDT': 8}})
        _write_cfg_file(root, 'risk_config_shared.json', {'symbol_weights': {'INJUSDT': 99}})
        assert rc.load_risk_config(mode='test')['symbol_weights'] == {'INJUSDT': 8}

    def test_missing_shared_file_falls_back_to_mode_copies(self, root):
        """Deploy order and rollback: before the split, the mode files still hold the values."""
        _write_cfg_file(root, 'risk_config_test.json', {'global_min_rr': 2})
        assert rc.load_risk_config(mode='test')['global_min_rr'] == 2
        assert rc.load_risk_config(mode='live')['global_min_rr'] == 2

    def test_real_money_keys_are_per_mode(self):
        for key in ('symbol_weights', 'base_leverage', 'max_leverage', 'balance_tiers',
                    'max_trade_pct', 'drawdown_hard_stop_pct', 'max_loss_usdt', 'locked_presets',
                    'trading_blackout_hours', 'min_profit_factor', 'virtual_only_floor',
                    'tats_min_weight', 'scenario', 'symbol_leverage'):
            assert key not in rc.SHARED_KEYS, key

    def test_python_and_dashboard_share_one_list(self):
        ts = src('dashboard/app/api/_risk-config.ts')
        block = ts[ts.index('export const SHARED_KEYS = ['):]
        block = block[:block.index('] as const')]
        ts_keys = set(re.findall(r"'([a-z_]+)'", block))
        assert ts_keys == set(rc.SHARED_KEYS)

    # ----------------------------------------------------------------------- #
    # split script                                                            #
    # ----------------------------------------------------------------------- #

    def test_split_script_seeds_all_four_files(self, tmp_path, monkeypatch):
        mod = _load_shared_split(tmp_path, monkeypatch)
        _write_cfg_file(tmp_path, 'risk_config_test.json',
                        {'global_min_rr': 2, 'telegram': {'token': 'x', 'chat_id': '1'},
                         'base_leverage': 4})
        _write_cfg_file(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
        assert mod.main() == 0

        shared = json.loads((tmp_path / 'risk_config_shared.json').read_text())
        assert shared == {'global_min_rr': 2, 'telegram': {'token': 'x', 'chat_id': '1'}}
        roster = json.loads((tmp_path / 'symbol_registry_shared.json').read_text())
        assert roster['symbols'] == LEGACY_REGISTRY['symbols']
        assert roster['status'] == LEGACY_REGISTRY['status']
        test = json.loads((tmp_path / 'symbol_registry_test.json').read_text())
        live = json.loads((tmp_path / 'symbol_registry_live.json').read_text())
        assert test['disabled'] == live['disabled'] == LEGACY_REGISTRY['disabled']
        assert (test['mode'], live['mode']) == ('test', 'live')
        assert 'symbols' not in test

    def test_split_script_never_overwrites(self, tmp_path, monkeypatch):
        mod = _load_shared_split(tmp_path, monkeypatch)
        _write_cfg_file(tmp_path, 'risk_config_test.json', {'global_min_rr': 2})
        _write_cfg_file(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
        mod.main()
        _write_cfg_file(tmp_path, 'symbol_registry_live.json', {'mode': 'live', 'disabled': {}})
        _write_cfg_file(tmp_path, 'risk_config_shared.json', {'global_min_rr': 5})
        mod.main()
        assert json.loads((tmp_path / 'symbol_registry_live.json').read_text())['disabled'] == {}
        assert json.loads((tmp_path / 'risk_config_shared.json').read_text()) == {'global_min_rr': 5}

    def test_split_script_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        mod = _load_shared_split(tmp_path, monkeypatch)
        monkeypatch.setattr('sys.argv', ['split'])
        _write_cfg_file(tmp_path, 'risk_config_test.json', {'global_min_rr': 2})
        _write_cfg_file(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
        assert mod.main() == 0
        assert sorted(p.name for p in tmp_path.glob('*.json')) == [
            'risk_config_test.json', 'symbol_registry.json']


# =========================================================================== #
# Locked presets per mode                                                     #
# =========================================================================== #

FLAT = {'EIGENUSDT': 'r5_sl_filter', 'TIAUSDT': 'l2_trend_buy'}
NESTED = {'test': {'TIAUSDT': 'l2_trend_buy'}, 'live': {'INJUSDT': 'r5_tight_rr3'}}


class TestLockedPresetsPerMode:
    """Locked presets are per trading mode.

    `locked_presets` was a flat {symbol: preset} dict shared by both instances. The mirror
    mounts the same risk_config.json read-only, so the presets locked for testnet were also
    being forced on the live market — where the ranking may well differ, which is the whole
    reason the mirror exists.

    Shape is now {"test": {...}, "live": {...}}. A legacy flat dict is read as the TEST set
    so existing config keeps working unchanged, and live starts empty.
    """

    class TestNested:
        def test_test_mode_reads_the_test_set(self):
            assert locked_presets_for({'locked_presets': NESTED}, 'test') == NESTED['test']

        def test_live_mode_reads_the_live_set(self):
            assert locked_presets_for({'locked_presets': NESTED}, 'live') == NESTED['live']

        def test_the_two_sets_are_independent(self):
            t = locked_presets_for({'locked_presets': NESTED}, 'test')
            l = locked_presets_for({'locked_presets': NESTED}, 'live')
            assert 'TIAUSDT' in t and 'TIAUSDT' not in l

    class TestLegacyFlat:
        """Existing server config is a flat dict and must keep working."""

        def test_a_flat_dict_is_the_test_set(self):
            assert locked_presets_for({'locked_presets': FLAT}, 'test') == FLAT

        def test_a_flat_dict_gives_live_nothing(self):
            assert locked_presets_for({'locked_presets': FLAT}, 'live') == {}, \
                'testnet locks must not be forced on the live market'

    class TestEdges:
        def test_a_missing_key_is_empty(self):
            assert locked_presets_for({}, 'test') == {}
            assert locked_presets_for({}, 'live') == {}

        def test_a_missing_mode_is_empty(self):
            assert locked_presets_for({'locked_presets': {'test': FLAT}}, 'live') == {}

        def test_a_malformed_value_is_empty_not_an_exception(self):
            """This runs on the candle path; a bad config must not raise."""
            for bad in (None, [], 'nonsense', 42):
                assert locked_presets_for({'locked_presets': bad}, 'test') == {}

        def test_a_nested_non_dict_mode_is_empty(self):
            assert locked_presets_for({'locked_presets': {'test': 'oops'}}, 'test') == {}

        def test_the_returned_dict_is_a_copy(self):
            cfg = {'locked_presets': dict(NESTED)}
            got = locked_presets_for(cfg, 'test')
            got['NEWSYM'] = 'x'
            assert 'NEWSYM' not in cfg['locked_presets']['test'], 'mutated the config'

    def test_main_passes_the_mode_everywhere(self):
        assert 'locked_presets_for' in MAIN_SRC, 'main.py still reads locked_presets directly'
        assert 'risk_cfg.get("locked_presets"' not in MAIN_SRC, \
            'a raw read remains — that instance would use the wrong mode'


# =========================================================================== #
# Backtest results per mode                                                   #
# =========================================================================== #

class TestBacktestResultsPerMode:
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

    # ----------------------------------------------------------------------- #
    # instance_path — still the primitive for display files (risk_state, charts)
    # ----------------------------------------------------------------------- #

    def test_primary_keeps_the_historical_name_in_both_modes(self):
        for mode in ('test', 'live'):
            assert instance_path(BASE, 'x.json', mode, mirror=False) == BASE / 'x.json', mode

    def test_mirror_is_suffixed_with_its_own_market(self):
        assert instance_path(BASE, 'x.json', 'live', mirror=True) == BASE / 'x_live.json'
        assert instance_path(BASE, 'x.json', 'test', mirror=True) == BASE / 'x_test.json'

    def test_extensionless_name_still_gets_a_suffix(self):
        assert instance_path(BASE, 'notes', 'live', mirror=True) == BASE / 'notes_live'

    def test_dotted_stem_suffixes_before_the_last_extension(self):
        assert instance_path(BASE, 'a.b.json', 'live', mirror=True) == BASE / 'a.b_live.json'

    # ----------------------------------------------------------------------- #
    # backtest results: named by mode                                         #
    # ----------------------------------------------------------------------- #

    def test_results_name_is_the_market(self):
        assert backtest_results_name('INJUSDT', 'test') == 'backtest_results_INJUSDT_test.json'
        assert backtest_results_name('INJUSDT', 'live') == 'backtest_results_INJUSDT_live.json'

    def test_test_falls_back_to_the_legacy_file(self, tmp_path):
        (tmp_path / 'backtest_results_INJUSDT.json').write_text('{}')
        assert backtest_results_path(tmp_path, 'INJUSDT', 'test').name == 'backtest_results_INJUSDT.json'
        (tmp_path / 'backtest_results_INJUSDT_test.json').write_text('{}')
        assert backtest_results_path(tmp_path, 'INJUSDT', 'test').name == 'backtest_results_INJUSDT_test.json'

    def test_live_never_falls_back_to_the_testnet_file(self, tmp_path):
        """The legacy file was always the testnet primary's; reading it for live would size
        live orders from the testnet market."""
        (tmp_path / 'backtest_results_INJUSDT.json').write_text('{}')
        assert backtest_results_path(tmp_path, 'INJUSDT', 'live').name == 'backtest_results_INJUSDT_live.json'

    # ----------------------------------------------------------------------- #
    # RiskManager — the reader whose output sizes real orders                 #
    # ----------------------------------------------------------------------- #

    def test_risk_manager_reads_its_market_file(self, tmp_path):
        for mirror in (False, True):
            assert RiskManager(mode='test', backtest_results_dir=tmp_path, mirror=mirror) \
                ._backtest_path('INJUSDT').name == 'backtest_results_INJUSDT_test.json'
            assert RiskManager(mode='live', backtest_results_dir=tmp_path, mirror=mirror) \
                ._backtest_path('INJUSDT').name == 'backtest_results_INJUSDT_live.json'

    def test_a_live_primary_ignores_the_testnet_results(self, tmp_path):
        (tmp_path / 'backtest_results_INJUSDT.json').write_text(json.dumps(
            {"presets": {"p": {"total_trades": 999, "total_profit_pct": 99.0, "profit_factor": 9.0}}}))
        rm = RiskManager(mode='live', backtest_results_dir=tmp_path)
        score, pf, raw = rm._compute_perf_score('INJUSDT')
        assert raw is None, "a live primary read the testnet backtest"

    # ----------------------------------------------------------------------- #
    # backtest.py — the writer                                                #
    # ----------------------------------------------------------------------- #

    def test_backtest_writes_the_market_file_from_any_instance(self, monkeypatch):
        for virtual in ('0', '1'):
            monkeypatch.setenv('VIRTUAL_ONLY', virtual)
            for mode in ('test', 'live'):
                monkeypatch.setenv('TRADING_MODE', mode)
                assert backtest._dashboard_path('INJUSDT').name == \
                    f'backtest_results_INJUSDT_{mode}.json'

    def test_backtest_treats_legacy_testnet_as_test(self, monkeypatch):
        monkeypatch.setenv('TRADING_MODE', 'testnet')
        assert backtest._dashboard_path('INJUSDT').name == 'backtest_results_INJUSDT_test.json'

    def test_chart_export_goes_to_the_instance_running_that_mode(self, monkeypatch, tmp_path):
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

    def test_backtest_does_not_need_api_keys(self, monkeypatch):
        """A live-mode backtest must work before live keys exist."""
        from config.settings import load_settings
        monkeypatch.setenv('TRADING_MODE', 'live')
        monkeypatch.delenv('API_KEY', raising=False)
        monkeypatch.delenv('API_SECRET', raising=False)
        monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
        s = load_settings('INJUSDT', require_keys=False)
        assert s.trading_mode == 'live' and s.api_key == ''

    def test_backtest_stores_production_klines_in_the_production_cache(self):
        text = src('backtest.py')
        assert "feed.set_cache_suffix('live')" in text
        assert "_{settings.timeframe}_live.json'" in text

    def test_feed_refuses_production_klines_into_the_testnet_cache(self):
        from bot.data_feed import DataFeed
        feed = DataFeed.__new__(DataFeed)
        feed._klines_source = 'production'
        feed._mode_suffix = 'live'
        with pytest.raises(ValueError):
            feed.set_cache_suffix('test')

    # ----------------------------------------------------------------------- #
    # main.py wiring                                                          #
    # ----------------------------------------------------------------------- #

    def test_main_seeds_the_tracker_from_its_market_file(self):
        assert 'backtest_results_path(' in MAIN_SRC
        assert 'f"backtest_results_{sym}.json"' not in MAIN_SRC


# =========================================================================== #
# Per-instance paths                                                          #
# =========================================================================== #

NAMES = ('risk_state.json', 'alert_state.json', 'system_log.json',
         'analysis.jsonl', 'bot.log', 'trades.log')


class TestPerInstancePaths:
    """Per-instance files must not interleave between the two bots.

    The suffix is keyed on which instance writes, not on the mode: the primary owns the
    unsuffixed name in either mode, so every existing reader is untouched whichever mode the
    bot is switched to. See bot/instance_paths.py for why that distinction matters.

    risk_state.json is the one with teeth — the mirror has balance 0, so sharing the file
    would blank the trading bot's risk page.
    """

    # ----------------------------------------------------------------------- #
    # The primitive, re-exported so main.py has one name for it               #
    # ----------------------------------------------------------------------- #

    def test_primary_keeps_the_historical_name_in_test_mode(self):
        for n in NAMES:
            assert _instance_path(BASE, n, 'test', mirror=False) == BASE / n, n

    def test_primary_keeps_the_historical_name_in_live_mode(self):
        """The regression this rule exists to prevent: going live must not hand the mirror
        the files the dashboard reads."""
        for n in NAMES:
            assert _instance_path(BASE, n, 'live', mirror=False) == BASE / n, n

    def test_mirror_is_always_suffixed(self):
        assert _instance_path(BASE, 'risk_state.json', 'live', mirror=True) == \
            BASE / 'risk_state_live.json'
        assert _instance_path(BASE, 'risk_state.json', 'test', mirror=True) == \
            BASE / 'risk_state_test.json'
        assert _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True) == \
            BASE / 'analysis_live.jsonl'
        assert _instance_path(BASE, 'bot.log', 'test', mirror=True) == BASE / 'bot_test.log'

    def test_mirror_suffix_tracks_its_own_market(self):
        """After a flip the mirror must start a new file, not append live-market data to a
        test-market one."""
        assert _instance_path(BASE, 'analysis.jsonl', 'live', mirror=True) != \
            _instance_path(BASE, 'analysis.jsonl', 'test', mirror=True)

    # ----------------------------------------------------------------------- #
    # Log paths, resolved before Settings or ModeManager exist                #
    # ----------------------------------------------------------------------- #

    def test_primary_log_paths_are_the_historical_ones(self, monkeypatch):
        monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
        bot_log, trades_log = _log_paths()
        assert bot_log == Path('logs') / 'bot.log'
        assert trades_log == Path('logs') / 'trades.log'

    def test_mirror_log_paths_carry_its_market(self, monkeypatch):
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        bot_log, trades_log = _log_paths()
        expected = opposite_mode(read_mode_file())
        assert bot_log == Path('logs') / f'bot_{expected}.log'
        assert trades_log == Path('logs') / f'trades_{expected}.log'

    def test_two_rotating_handlers_never_share_a_file(self, monkeypatch):
        """Two RotatingFileHandlers on one file collide during rollover, even though the
        mirror logs no trades."""
        monkeypatch.delenv('VIRTUAL_ONLY', raising=False)
        primary = _log_paths()
        monkeypatch.setenv('VIRTUAL_ONLY', '1')
        mirror = _log_paths()
        assert set(primary).isdisjoint(set(mirror))

    # ----------------------------------------------------------------------- #
    # results_{symbol}.json                                                    #
    # ----------------------------------------------------------------------- #

    def test_results_primary_writes_only_the_name_everything_reads(self):
        """Verified readers: telegram_menu.py:257/324, exporter.py, page.tsx:62,
        trades/page.tsx:204, api/symbols/[symbol]/route.ts:24 — all unsuffixed."""
        for mode in ('test', 'live'):
            assert _results_path('INJUSDT', mode, mirror=False) == \
                Path('dashboard/public/results_INJUSDT.json'), mode

    def test_results_mirror_never_writes_the_unsuffixed_name(self):
        for mode in ('test', 'live'):
            got = _results_path('INJUSDT', mode, mirror=True)
            assert got == Path(f'dashboard/public/results_INJUSDT_{mode}.json')
            assert got != Path('dashboard/public/results_INJUSDT.json')

    def test_export_accepts_mirror_and_defaults_to_the_primary(self):
        from bot.exporter import export
        sig = inspect.signature(export)
        assert 'mirror' in sig.parameters, 'export must know which instance calls it'
        assert sig.parameters['mirror'].default is False, \
            'defaulting to True would retarget the trading bot'

    # ----------------------------------------------------------------------- #
    # Wiring: no unsuffixed literal may survive for a category-B file         #
    # ----------------------------------------------------------------------- #

    def test_no_category_b_path_is_built_without_instance_path(self):
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

    def test_risk_manager_gets_an_explicit_state_path(self):
        ctor = MAIN_SRC[MAIN_SRC.index('risk_manager = RiskManager('):]
        ctor = ctor[:ctor.index('\n    )')]
        assert 'state_path=' in ctor, \
            'risk_state.json defaults to the shared path — the mirror would blank it'

    def test_both_export_call_sites_pass_mirror(self):
        count = MAIN_SRC.count('mirror=_virtual_only')
        assert count >= 2, f'expected both export sites to pass mirror, found {count}'

    def test_the_instance_mode_agrees_with_mode_manager(self):
        """_instance_mode names the notifier's files and is computed before ModeManager
        exists; if the two ever disagreed, one instance would split its output across two
        suffixes."""
        assert '_instance_mode' in MAIN_SRC
        assert 'assert _instance_mode == mode_manager.current_mode' in MAIN_SRC or \
            '_instance_mode != mode_manager.current_mode' in MAIN_SRC, \
            'the agreement between the two mode resolutions must be checked, not assumed'


# =========================================================================== #
# ModeManager                                                                 #
# =========================================================================== #

def _make_mm(tmp_path: Path) -> ModeManager:
    return ModeManager(
        mode_path=tmp_path / "bot_mode.json",
        command_path=tmp_path / "bot_command.json",
        result_path=tmp_path / "bot_command_result.json",
    )


class TestModeManager:
    def test_default_mode_is_test(self, tmp_path):
        mm = _make_mm(tmp_path)
        assert mm.current_mode == "test"

    def test_write_and_read_mode(self, tmp_path):
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

    def test_poll_reads_and_clears_command(self, tmp_path):
        command_path = tmp_path / "bot_command.json"
        mm = _make_mm(tmp_path)
        command_path.write_text(json.dumps(
            {"id": "abc", "type": "stop_bot", "payload": {}, "issued_at": "2026-01-01T00:00:00Z"}
        ))
        cmd = mm._read_and_clear_command()
        assert cmd is not None
        assert cmd["type"] == "stop_bot"
        assert not command_path.exists()

    def test_poll_returns_none_when_no_command(self, tmp_path):
        mm = _make_mm(tmp_path)
        assert mm._read_and_clear_command() is None

    def test_in_place_switch_is_gone(self, tmp_path):
        """Mode switches happen by closing everything and restarting (main.py
        _primary_mode_watch). The half-complete in-place switch must not come back."""
        mm = _make_mm(tmp_path)
        assert not hasattr(mm, 'switch_mode')

    @pytest.mark.asyncio
    async def test_stop_bot_calls_close(self, tmp_path):
        mm = _make_mm(tmp_path)
        close_called = []

        async def fake_close():
            close_called.append(True)

        await mm.stop_bot(close_all=fake_close)
        assert close_called == [True]


# =========================================================================== #
# Coordinated mode switch                                                     #
# =========================================================================== #

class Exchange:
    """Fake: positions on the exchange; close_real clears only what the bot tracks."""

    def __init__(self, tracked, untracked=(), check_fails=False, stuck=()):
        self.tracked = set(tracked)
        self.untracked = set(untracked)
        self.stuck = set(stuck)            # never close
        self.check_fails = check_fails
        self.calls = []

    async def close_virtual(self):
        self.calls.append('virtual')

    async def close_real(self):
        self.calls.append('real')
        self.tracked -= (self.tracked - self.stuck)

    async def open_on_exchange(self):
        self.calls.append('check')
        if self.check_fails:
            return None
        return sorted(self.tracked | self.untracked)

    async def close_untracked(self):
        self.calls.append('untracked')
        self.untracked -= (self.untracked - self.stuck)

    async def sleep(self, _s):
        self.calls.append('sleep')


def _run_close_out(ex, keys=True):
    return asyncio.run(close_out(
        'live', keys_present=lambda m: keys, close_virtual=ex.close_virtual,
        close_real=ex.close_real, open_on_exchange=ex.open_on_exchange,
        close_untracked=ex.close_untracked, sleep=ex.sleep))


def _switch_mm(tmp_path, mirror=False):
    return ModeManager(mode_path=tmp_path / 'bot_mode.json',
                       command_path=tmp_path / 'cmd.json', result_path=tmp_path / 'res.json',
                       mirror=mirror, primary_path=tmp_path / 'primary_mode.json')


def _write_mode_file(p: Path, mode):
    p.write_text(json.dumps({'mode': mode}))


class TestModeSwitch:
    """Coordinated mode switch: close everything, prove flat, restart; the mirror follows the
    primary's RUNNING mode. Spec: docs/specs/2026-09-26-mode-switch-restart-and-per-mode-backtests.md
    """

    # ----------------------------------------------------------------------- #
    # close_out                                                               #
    # ----------------------------------------------------------------------- #

    def test_refuses_without_keys_and_closes_nothing(self):
        ex = Exchange(tracked={'SOLUSDT'})
        r = _run_close_out(ex, keys=False)
        assert r.outcome == 'refused'
        assert ex.calls == [] and ex.tracked == {'SOLUSDT'}

    def test_flat_after_closing_everything(self):
        ex = Exchange(tracked={'SOLUSDT', 'INJUSDT'})
        r = _run_close_out(ex)
        assert r.outcome == 'flat'
        assert ex.calls[:3] == ['virtual', 'real', 'check']

    def test_untracked_leftovers_are_closed_before_declaring_flat(self):
        """close_all_orders_at_market forgets an order even when its close failed."""
        ex = Exchange(tracked={'SOLUSDT'}, untracked={'INJUSDT'})
        r = _run_close_out(ex)
        assert r.outcome == 'flat'
        assert 'untracked' in ex.calls

    def test_postponed_when_a_position_will_not_close(self):
        ex = Exchange(tracked={'SOLUSDT'}, stuck={'SOLUSDT'})
        r = _run_close_out(ex)
        assert r.outcome == 'postponed' and r.still_open == ['SOLUSDT']
        assert ex.calls.count('real') == 3

    def test_postponed_when_the_exchange_cannot_be_asked(self):
        """No proof of flat → never exit (a rate-limit ban answers None)."""
        ex = Exchange(tracked=set(), check_fails=True)
        r = _run_close_out(ex)
        assert r.outcome == 'postponed' and r.still_open is None

    def test_a_failing_market_close_does_not_crash_the_sequence(self):
        ex = Exchange(tracked={'SOLUSDT'})

        async def boom():
            raise RuntimeError('-1003 banned')
        r = asyncio.run(close_out(
            'live', keys_present=lambda m: True, close_virtual=ex.close_virtual,
            close_real=boom, open_on_exchange=ex.open_on_exchange,
            close_untracked=ex.close_untracked, sleep=ex.sleep))
        assert r.outcome in ('flat', 'postponed')     # SOL untracked-closed or still reported

    # ----------------------------------------------------------------------- #
    # ModeManager: requested vs running                                       #
    # ----------------------------------------------------------------------- #

    def test_primary_sees_a_requested_change(self, tmp_path):
        _write_mode_file(tmp_path / 'bot_mode.json', 'test')
        m = _switch_mm(tmp_path)
        assert m.requested_mode_change() is None
        _write_mode_file(tmp_path / 'bot_mode.json', 'live')
        assert m.requested_mode_change() == 'live'

    def test_primary_ignores_torn_or_unknown_requests(self, tmp_path):
        _write_mode_file(tmp_path / 'bot_mode.json', 'test')
        m = _switch_mm(tmp_path)
        (tmp_path / 'bot_mode.json').write_text('{"mode": "li')
        assert m.requested_mode_change() is None
        _write_mode_file(tmp_path / 'bot_mode.json', 'paper')
        assert m.requested_mode_change() is None

    def test_mirror_follows_the_running_primary_not_the_request(self, tmp_path):
        _write_mode_file(tmp_path / 'bot_mode.json', 'test')
        _write_mode_file(tmp_path / 'primary_mode.json', 'test')
        mirror = _switch_mm(tmp_path, mirror=True)
        assert mirror.current_mode == 'live'
        _write_mode_file(tmp_path / 'bot_mode.json', 'live')      # button pressed, primary still closing
        assert mirror.mirror_target_changed() is False     # used to restart into 'test' here
        _write_mode_file(tmp_path / 'primary_mode.json', 'live')  # primary restarted in live
        assert mirror.mirror_target_changed() is True

    def test_mirror_falls_back_to_bot_mode_before_any_primary_wrote_it(self, tmp_path):
        _write_mode_file(tmp_path / 'bot_mode.json', 'test')
        mirror = _switch_mm(tmp_path, mirror=True)
        assert mirror.current_mode == 'live'
        _write_mode_file(tmp_path / 'bot_mode.json', 'live')
        assert mirror.mirror_target_changed() is True

    def test_primary_records_its_running_mode_and_mirror_never_writes(self, tmp_path):
        _write_mode_file(tmp_path / 'bot_mode.json', 'live')
        _switch_mm(tmp_path).write_primary_mode('t0')
        assert json.loads((tmp_path / 'primary_mode.json').read_text())['mode'] == 'live'
        _write_mode_file(tmp_path / 'primary_mode.json', 'test')
        _switch_mm(tmp_path, mirror=True).write_primary_mode('t1')
        assert json.loads((tmp_path / 'primary_mode.json').read_text())['mode'] == 'test'

    def test_instance_mode(self, tmp_path):
        bm, pm = tmp_path / 'bot_mode.json', tmp_path / 'primary_mode.json'
        _write_mode_file(bm, 'live')
        _write_mode_file(pm, 'test')
        assert instance_mode(False, pm, bm) == 'live'      # primary: the request
        assert instance_mode(True, pm, bm) == 'live'       # mirror: opposite of running 'test'
        assert read_primary_running_mode(pm, bm) == 'test'

    # ----------------------------------------------------------------------- #
    # main.py wiring                                                          #
    # ----------------------------------------------------------------------- #

    def test_pending_switch_stops_new_real_orders(self):
        block = MAIN_SRC[MAIN_SRC.index('_placement_symbols = [] if _virtual_only'):][:400]
        assert 'if _switch_pending[0] is not None:' in block
        assert '_placement_symbols = []' in block.split('if _switch_pending[0] is not None:')[1]

    def test_only_the_primary_runs_the_switch_watch(self):
        block = MAIN_SRC[MAIN_SRC.index('_switch_task = None'):][:400]
        assert 'if _virtual_only:' in block and '_primary_mode_watch()' in block.split('else:')[1]

    def test_exit_happens_only_after_a_flat_close_out(self):
        watch = MAIN_SRC[MAIN_SRC.index('async def _primary_mode_watch'):]
        watch = watch[:watch.index('sys.exit(0)')]
        assert "result.outcome == 'refused'" in watch and "result.outcome == 'postponed'" in watch
        # both non-flat outcomes loop back before the exit
        assert watch.count('continue') >= 4

    def test_primary_writes_its_running_mode_at_startup(self):
        assert 'mode_manager.write_primary_mode(started_at)' in MAIN_SRC

    def test_in_place_switch_removed(self):
        assert 'async def on_switch_mode' not in MAIN_SRC
        assert 'on_switch_mode=' not in MAIN_SRC

    def test_rate_limit_state_is_keyed_by_mode(self):
        assert 'f"rate_limit_state_{current_mode}.json"' in MAIN_SRC


# =========================================================================== #
# Recalc script ranges                                                        #
# =========================================================================== #

STORE = src('dashboard/app/api/trades/_preset-profit-store.ts')
SCRIPT = src('scripts/recalc_symbol_scores.sh')
PAGE_KEYS = re.findall(r"\{ key: '([^']+)'", src('dashboard/lib/tradesDateRange.ts'))


class TestRecalcScriptRanges:
    """The preset Profit% store covers every date shortcut the Trades page offers.

    The store derives its windows from RANGE_PRESETS itself, so a new shortcut is covered
    automatically — this guards that nobody reintroduces a hand-kept list that can drift
    (the recalc script used to hold one), and that "today"/"all" stay special-cased by
    their `days` value rather than by name.
    """

    def test_store_windows_come_from_range_presets(self):
        assert 'for (const p of RANGE_PRESETS)' in STORE
        assert 'p.days === null ? null : p.days === 0 ? midnightS' in STORE

    def test_no_hand_kept_shortcut_list_in_store_or_script(self):
        for key in PAGE_KEYS:
            if key in ('today', 'all'):
                continue
            assert f"'{key}'" not in STORE and f'"{key}"' not in SCRIPT, key

    def test_page_still_offers_two_weeks(self):
        assert '14d' in PAGE_KEYS


class TestRiskConfigView:
    """risk_config_view(): a cached, read-only view for hot paths (2026-09-29 profiling:
    load_risk_config() was called tens of thousands of times per candle batch)."""

    @pytest.fixture
    def cfgdir(self, tmp_path, monkeypatch):
        import config.risk_config as rc
        monkeypatch.setattr(rc, '_ROOT', tmp_path)
        monkeypatch.setattr(rc, '_active_mode', 'test')
        rc._VIEW_CACHE.clear()
        (tmp_path / 'risk_config_test.json').write_text(json.dumps({'max_trade_pct': 7}))
        yield tmp_path
        rc._VIEW_CACHE.clear()

    def test_it_matches_load_risk_config(self, cfgdir):
        import config.risk_config as rc
        assert dict(rc.risk_config_view()) == rc.load_risk_config()

    def test_it_is_read_only(self, cfgdir):
        import config.risk_config as rc
        with pytest.raises(TypeError):
            rc.risk_config_view()['max_trade_pct'] = 1

    def test_it_is_not_rebuilt_while_the_files_are_unchanged(self, cfgdir, monkeypatch):
        import config.risk_config as rc
        rc.risk_config_view()
        calls = []
        real = rc.load_risk_config
        monkeypatch.setattr(rc, 'load_risk_config', lambda *a, **k: calls.append(1) or real(*a, **k))
        for _ in range(50):
            rc.risk_config_view()
        assert calls == []

    def test_an_edit_is_picked_up(self, cfgdir):
        import os
        import config.risk_config as rc
        assert rc.risk_config_view()['max_trade_pct'] == 7
        f = cfgdir / 'risk_config_test.json'
        f.write_text(json.dumps({'max_trade_pct': 12}))
        os.utime(f, ns=(1, f.stat().st_mtime_ns + 1_000_000))   # a distinct mtime, as any real edit has
        assert rc.risk_config_view()['max_trade_pct'] == 12
