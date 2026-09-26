"""Per-mode risk config: risk_config_test.json / risk_config_live.json.

Spec: docs/specs/2026-09-26-per-mode-risk-config.md. A weight edited in one mode must not
move in the other; live falls back to test key by key; locks never leak across modes.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from config import risk_config as rc


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, '_ROOT', tmp_path)
    monkeypatch.setattr(rc, '_active_mode', None)
    monkeypatch.delenv('TRADING_MODE', raising=False)
    return tmp_path


def write(root: Path, mode: str, cfg: dict) -> None:
    (root / f'risk_config_{mode}.json').write_text(json.dumps(cfg))


def test_each_mode_reads_its_own_file(root):
    write(root, 'test', {'symbol_weights': {'INJUSDT': 9}})
    write(root, 'live', {'symbol_weights': {'INJUSDT': 8}})
    assert rc.load_risk_config(mode='test')['symbol_weights'] == {'INJUSDT': 9}
    assert rc.load_risk_config(mode='live')['symbol_weights'] == {'INJUSDT': 8}


def test_saving_one_mode_leaves_the_other_untouched(root):
    write(root, 'test', {'symbol_weights': {'INJUSDT': 8}})
    write(root, 'live', {'symbol_weights': {'INJUSDT': 8}})
    cfg = rc.load_risk_config(mode='test')
    cfg['symbol_weights']['INJUSDT'] = 9
    rc.save_risk_config(cfg, mode='test')
    assert rc.load_risk_config(mode='test')['symbol_weights']['INJUSDT'] == 9
    assert rc.load_risk_config(mode='live')['symbol_weights']['INJUSDT'] == 8


def test_live_falls_back_to_test_key_by_key(root):
    write(root, 'test', {'max_leverage': 7, 'scenario': 'tats'})
    write(root, 'live', {'max_leverage': 3})
    live = rc.load_risk_config(mode='live')
    assert live['max_leverage'] == 3          # live's own value wins
    assert live['scenario'] == 'tats'         # missing in live → test's
    assert 'scenario' not in json.loads((root / 'risk_config_live.json').read_text())


def test_missing_live_file_reads_test(root):
    write(root, 'test', {'max_leverage': 7})
    assert rc.load_risk_config(mode='live')['max_leverage'] == 7
    assert not (root / 'risk_config_live.json').exists()   # never created on read


def test_test_never_falls_back_to_live(root):
    write(root, 'live', {'max_leverage': 3})
    assert rc.load_risk_config(mode='test')['max_leverage'] == rc.DEFAULT_CONFIG['max_leverage']


def test_test_locks_never_leak_into_live(root):
    write(root, 'test', {'locked_presets': {'test': {'SOLUSDT': 'r5_arm25'}}})
    write(root, 'live', {})   # no locked_presets key → falls back to test's dict
    live = rc.load_risk_config(mode='live')
    assert rc.locked_presets_for(live, 'live') == {}
    assert rc.locked_presets_for(rc.load_risk_config(mode='test'), 'test') == {'SOLUSDT': 'r5_arm25'}


def test_active_mode_drives_the_default_path(root, monkeypatch):
    write(root, 'test', {'max_leverage': 7})
    write(root, 'live', {'max_leverage': 3})
    assert rc.load_risk_config()['max_leverage'] == 7          # default: test
    monkeypatch.setenv('TRADING_MODE', 'live')
    assert rc.load_risk_config()['max_leverage'] == 3          # env fallback
    rc.set_active_mode('test')
    assert rc.load_risk_config()['max_leverage'] == 7          # explicit wins over env
    with pytest.raises(ValueError):
        rc.set_active_mode('paper')


def test_explicit_per_mode_path_keeps_fallback(root):
    write(root, 'test', {'scenario': 'tats'})
    write(root, 'live', {'max_leverage': 3})
    live = rc.load_risk_config(root / 'risk_config_live.json')
    assert live['scenario'] == 'tats' and live['max_leverage'] == 3


def test_split_script_seeds_both_files(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'split', Path(__file__).resolve().parents[1] / 'scripts/split_risk_config.py')
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
