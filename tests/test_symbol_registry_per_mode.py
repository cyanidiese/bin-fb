"""Per-mode symbol registry: one shared roster, per-mode decisions.

Spec: docs/specs/2026-09-26-shared-settings-and-per-mode-registry.md. Disabling a symbol
in test must not disable it in live; the symbol list is the same for both.
"""
import json
from pathlib import Path

import pytest

from bot.symbol_registry import SymbolRegistry

LEGACY = {
    'symbols': ['INJUSDT', 'SOLUSDT', 'WLDUSDT'],
    'status': {'INJUSDT': {'backtest': 'complete', 'pid': None}},
    'weights': {'INJUSDT': 1.0, 'SOLUSDT': 1.0, 'WLDUSDT': 0.0},
    'disabled': {'WLDUSDT': {'reason': 'losses', 'disabled_at': '2026-06-02'}},
    'disabled_ranks': {}, 'paused': {}, 'leverage_overrides': {},
}


def read(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text())


def reg(root: Path, mode: str, **kw) -> SymbolRegistry:
    return SymbolRegistry(seed_symbols=['BTCUSDT'], mode=mode, root=root, **kw)


def test_first_start_seeds_from_the_legacy_file(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    r = reg(tmp_path, 'test')
    assert r.get_symbols() == LEGACY['symbols']
    assert r.is_disabled('WLDUSDT')
    assert read(tmp_path, 'symbol_registry_shared.json')['symbols'] == LEGACY['symbols']
    assert read(tmp_path, 'symbol_registry_test.json')['disabled'] == LEGACY['disabled']
    assert read(tmp_path, 'symbol_registry.json') == LEGACY          # legacy untouched


def test_disabling_in_test_leaves_live_enabled(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    test = reg(tmp_path, 'test')
    live = reg(tmp_path, 'live')
    test.disable('SOLUSDT', 'manual')
    live.reload_from_disk()
    assert test.is_disabled('SOLUSDT')
    assert not live.is_disabled('SOLUSDT')
    assert 'SOLUSDT' not in read(tmp_path, 'symbol_registry_live.json').get('disabled', {})


def test_live_without_its_own_file_starts_from_test(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    reg(tmp_path, 'test').pause_symbol('INJUSDT')
    live = reg(tmp_path, 'live', read_only=True)       # the mirror
    assert live.is_symbol_paused('INJUSDT')
    assert live.is_disabled('WLDUSDT')
    assert not (tmp_path / 'symbol_registry_live.json').exists()   # :ro — never created


def test_the_roster_is_shared(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    test = reg(tmp_path, 'test')
    live = reg(tmp_path, 'live')
    roster = read(tmp_path, 'symbol_registry_shared.json')
    roster['symbols'].append('DOGEUSDT')                 # the dashboard adds a symbol
    (tmp_path / 'symbol_registry_shared.json').write_text(json.dumps(roster))
    assert test.reload_from_disk() == (['DOGEUSDT'], [])
    assert live.reload_from_disk() == (['DOGEUSDT'], [])


def test_decisions_never_rewrite_the_roster(tmp_path):
    """The dashboard owns `status` (backtest runs); a disable used to rewrite it from the
    bot's stale in-memory copy."""
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    r = reg(tmp_path, 'test')
    roster = read(tmp_path, 'symbol_registry_shared.json')
    roster['status']['SOLUSDT'] = {'backtest': 'running', 'pid': 42}
    (tmp_path / 'symbol_registry_shared.json').write_text(json.dumps(roster))
    r.disable('SOLUSDT', 'manual')
    r.set_weight('INJUSDT', 2.0)
    r.pause_symbol('INJUSDT')
    assert read(tmp_path, 'symbol_registry_shared.json') == roster


def test_mirror_writes_nothing(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    reg(tmp_path, 'test')
    reg(tmp_path, 'live')
    before = {p.name: p.read_text() for p in tmp_path.glob('*.json')}
    mirror = reg(tmp_path, 'live', read_only=True)
    mirror.disable('INJUSDT', 'x')
    mirror.pause_symbol('SOLUSDT')
    mirror.add_symbol('DOGEUSDT')
    assert {p.name: p.read_text() for p in tmp_path.glob('*.json')} == before


def test_unreadable_state_file_is_not_overwritten(tmp_path):
    (tmp_path / 'symbol_registry.json').write_text(json.dumps(LEGACY))
    (tmp_path / 'symbol_registry_test.json').write_text('{"disabled": {tru')   # torn write
    r = reg(tmp_path, 'test')
    assert r.is_disabled('WLDUSDT')                      # ran from the legacy fallback
    assert (tmp_path / 'symbol_registry_test.json').read_text() == '{"disabled": {tru'


def test_no_files_at_all_seeds_from_env(tmp_path):
    r = reg(tmp_path, 'test')
    assert r.get_symbols() == ['BTCUSDT']
    assert read(tmp_path, 'symbol_registry_shared.json')['symbols'] == ['BTCUSDT']


def test_mode_and_registry_path_are_exclusive(tmp_path):
    with pytest.raises(ValueError):
        SymbolRegistry(['X'], registry_path=tmp_path / 'r.json', mode='test')
    with pytest.raises(ValueError):
        SymbolRegistry(['X'], mode='paper', root=tmp_path)


def test_main_builds_the_registry_for_its_mode():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    ctor = src[src.index('symbol_registry = SymbolRegistry('):]
    ctor = ctor[:ctor.index('\n    )')]
    assert 'mode=_cfg_mode' in ctor
    assert 'read_only=_virtual_only' in ctor
