"""risk_config_shared.json — one value for both modes.

Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md. Signal filters,
preset ranking, Telegram and backtest settings are shared; weights, leverage and loss
limits stay per mode.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

from config import risk_config as rc

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, '_ROOT', tmp_path)
    monkeypatch.setattr(rc, '_active_mode', None)
    monkeypatch.delenv('TRADING_MODE', raising=False)
    return tmp_path


def write(root: Path, name: str, cfg: dict) -> None:
    (root / name).write_text(json.dumps(cfg))


def test_shared_value_applies_to_both_modes(root):
    write(root, 'risk_config_test.json', {'global_min_rr': 2, 'base_leverage': 4})
    write(root, 'risk_config_live.json', {'global_min_rr': 2, 'base_leverage': 2})
    write(root, 'risk_config_shared.json', {'global_min_rr': 3})
    for mode in ('test', 'live'):
        assert rc.load_risk_config(mode=mode)['global_min_rr'] == 3
    # per-mode keys untouched
    assert rc.load_risk_config(mode='test')['base_leverage'] == 4
    assert rc.load_risk_config(mode='live')['base_leverage'] == 2


def test_shared_file_wins_over_a_stale_mode_copy(root):
    write(root, 'risk_config_live.json', {'preset_blocklist': ['old']})
    write(root, 'risk_config_shared.json', {'preset_blocklist': ['new']})
    assert rc.load_risk_config(mode='live')['preset_blocklist'] == ['new']


def test_non_shared_keys_in_the_shared_file_are_ignored(root):
    """Only SHARED_KEYS are read from it, so a stray weight can never leak across modes."""
    write(root, 'risk_config_test.json', {'symbol_weights': {'INJUSDT': 8}})
    write(root, 'risk_config_shared.json', {'symbol_weights': {'INJUSDT': 99}})
    assert rc.load_risk_config(mode='test')['symbol_weights'] == {'INJUSDT': 8}


def test_missing_shared_file_falls_back_to_mode_copies(root):
    """Deploy order and rollback: before the split, the mode files still hold the values."""
    write(root, 'risk_config_test.json', {'global_min_rr': 2})
    assert rc.load_risk_config(mode='test')['global_min_rr'] == 2
    assert rc.load_risk_config(mode='live')['global_min_rr'] == 2


def test_real_money_keys_are_per_mode():
    for key in ('symbol_weights', 'base_leverage', 'max_leverage', 'balance_tiers',
                'max_trade_pct', 'drawdown_hard_stop_pct', 'max_loss_usdt', 'locked_presets',
                'trading_blackout_hours', 'min_profit_factor', 'virtual_only_floor',
                'tats_min_weight', 'scenario', 'symbol_leverage'):
        assert key not in rc.SHARED_KEYS, key


def test_python_and_dashboard_share_one_list():
    ts = (REPO / 'dashboard/app/api/_risk-config.ts').read_text()
    block = ts[ts.index('export const SHARED_KEYS = ['):]
    block = block[:block.index('] as const')]
    ts_keys = set(re.findall(r"'([a-z_]+)'", block))
    assert ts_keys == set(rc.SHARED_KEYS)


# --------------------------------------------------------------------------- #
# split script                                                                #
# --------------------------------------------------------------------------- #

def _split(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'split2', REPO / 'scripts/split_shared_and_registry.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/risk_config.py').write_text((REPO / 'config/risk_config.py').read_text())
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


def test_split_script_seeds_all_four_files(tmp_path, monkeypatch):
    mod = _split(tmp_path, monkeypatch)
    write(tmp_path, 'risk_config_test.json',
          {'global_min_rr': 2, 'telegram': {'token': 'x', 'chat_id': '1'}, 'base_leverage': 4})
    write(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
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


def test_split_script_never_overwrites(tmp_path, monkeypatch):
    mod = _split(tmp_path, monkeypatch)
    write(tmp_path, 'risk_config_test.json', {'global_min_rr': 2})
    write(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
    mod.main()
    write(tmp_path, 'symbol_registry_live.json', {'mode': 'live', 'disabled': {}})
    write(tmp_path, 'risk_config_shared.json', {'global_min_rr': 5})
    mod.main()
    assert json.loads((tmp_path / 'symbol_registry_live.json').read_text())['disabled'] == {}
    assert json.loads((tmp_path / 'risk_config_shared.json').read_text()) == {'global_min_rr': 5}


def test_split_script_dry_run_writes_nothing(tmp_path, monkeypatch):
    mod = _split(tmp_path, monkeypatch)
    monkeypatch.setattr('sys.argv', ['split'])
    write(tmp_path, 'risk_config_test.json', {'global_min_rr': 2})
    write(tmp_path, 'symbol_registry.json', LEGACY_REGISTRY)
    assert mod.main() == 0
    assert sorted(p.name for p in tmp_path.glob('*.json')) == [
        'risk_config_test.json', 'symbol_registry.json']
