"""The registry must survive being read by two processes at once.

Three separate failure modes, all reachable today:

1. `_persist()` used write_text() directly, unlike risk_config's _atomic_write(). With
   two processes on the file a reader can catch it mid-write and get truncated JSON.
2. `_load()` answers a parse failure by reseeding from the SYMBOL env var and calling
   `_persist()` — a WRITE. So a transient read glitch would overwrite the real registry,
   discarding weights, disabled, paused and leverage_overrides.
3. On the mirror's read-only config mount that same write raises PermissionError inside
   __init__, killing the instance at startup. The :ro safety measure turned a transient
   glitch into a hard crash.
"""
import json
from pathlib import Path

import pytest

from bot.symbol_registry import SymbolRegistry

FULL = {
    'symbols': ['INJUSDT', 'SOLUSDT'],
    'status': {'INJUSDT': {'backtest': 'ok', 'pid': None}},
    'weights': {'INJUSDT': 2.0, 'SOLUSDT': 1.0},
    'disabled': {'SOLUSDT': {'reason': 'losses'}},
    'disabled_ranks': {'INJUSDT': [3]},
    'paused': {'INJUSDT': {'until': 'later'}},
    'leverage_overrides': {'INJUSDT': 7},
}


# --------------------------------------------------------------------------- #
# 1. atomic write                                                             #
# --------------------------------------------------------------------------- #

def test_persist_goes_through_a_temp_file(tmp_path, monkeypatch):
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

    replaced = []
    real = Path.replace

    def spy(self, target):
        # the temp file must already hold complete JSON before it becomes the real one
        json.loads(self.read_text())
        replaced.append((self.name, Path(target).name))
        return real(self, target)

    monkeypatch.setattr(Path, 'replace', spy)
    r.pause_symbol('INJUSDT')
    assert replaced, 'persist did not use tmp+replace'
    assert replaced[-1][1] == 'symbol_registry.json'


def test_no_temp_file_is_left_behind(tmp_path):
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)
    r.pause_symbol('INJUSDT')
    assert list(tmp_path.glob('*.tmp')) == []


def test_reader_only_ever_sees_valid_json(tmp_path):
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT', 'SOLUSDT'], registry_path=path)
    for i in range(20):
        r.pause_symbol('INJUSDT')
        json.loads(path.read_text())
        r.resume_symbol('INJUSDT')
        json.loads(path.read_text())


# --------------------------------------------------------------------------- #
# 2. a corrupt file must not be destroyed                                     #
# --------------------------------------------------------------------------- #

def test_corrupt_file_is_not_overwritten(tmp_path):
    """Reseeding over an unreadable file loses weights, pauses and leverage
    overrides that a later restart could have recovered."""
    path = tmp_path / 'symbol_registry.json'
    path.write_text('{"symbols": ["INJUSDT"], "weig')      # truncated
    before = path.read_text()
    r = SymbolRegistry(seed_symbols=['SOLUSDT'], registry_path=path)
    assert path.read_text() == before, 'the unreadable registry was overwritten'
    assert r.get_symbols() == ['SOLUSDT'], 'should still run from the seed in memory'


def test_missing_file_is_still_seeded_and_written(tmp_path):
    """Existing behaviour for a genuinely absent file must not change."""
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)
    assert path.exists()
    assert json.loads(path.read_text())['symbols'] == ['INJUSDT']


def test_a_valid_file_is_loaded_unchanged(tmp_path):
    """The primary's normal path: nothing about loading changes."""
    path = tmp_path / 'symbol_registry.json'
    path.write_text(json.dumps(FULL))
    r = SymbolRegistry(seed_symbols=['NOTUSED'], registry_path=path)
    assert r.get_symbols() == ['INJUSDT', 'SOLUSDT']
    assert r.is_disabled('SOLUSDT')
    assert r.is_symbol_paused('INJUSDT')
    assert r.is_rank_disabled('INJUSDT', 3)


# --------------------------------------------------------------------------- #
# 3. read-only mount must not crash the instance                              #
# --------------------------------------------------------------------------- #

def test_read_only_registry_writes_nothing(tmp_path):
    path = tmp_path / 'symbol_registry.json'
    path.write_text(json.dumps(FULL))
    before = path.read_text()
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path, read_only=True)
    r.pause_symbol('INJUSDT')
    r.add_symbol('DOGEUSDT')
    assert path.read_text() == before, 'the mirror wrote to shared config'


def test_read_only_with_no_file_does_not_crash(tmp_path):
    """The seed path calls _persist(); on a :ro mount that used to raise inside
    __init__ and take the instance down at startup."""
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path, read_only=True)
    assert r.get_symbols() == ['INJUSDT']
    assert not path.exists()


def test_a_failed_write_does_not_raise(tmp_path, monkeypatch):
    """Belt and braces: even a writable instance must not die if the disk refuses."""
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

    def boom(self, target):
        raise PermissionError('read-only file system')

    monkeypatch.setattr(Path, 'replace', boom)
    r.pause_symbol('INJUSDT')          # must not propagate
    assert r.is_symbol_paused('INJUSDT'), 'in-memory state should still be updated'


def test_default_is_writable(tmp_path):
    """read_only must default to False so the trading bot is unaffected."""
    import inspect
    sig = inspect.signature(SymbolRegistry.__init__)
    assert sig.parameters['read_only'].default is False


def test_main_passes_read_only_from_virtual_only():
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    ctor = src[src.index('SymbolRegistry('):]
    ctor = ctor[:ctor.index(')')]
    assert 'read_only=' in ctor, 'the mirror must not be able to write shared config'


def test_persist_lands_even_when_rename_is_busy(tmp_path, monkeypatch):
    """symbol_registry.json is bind-mounted as a single file, so inside the container
    rename over it fails with EBUSY. The write must still land, or every pause, disable
    and weight change would silently stop persisting."""
    import config.safe_write as sw
    path = tmp_path / 'symbol_registry.json'
    r = SymbolRegistry(seed_symbols=['INJUSDT'], registry_path=path)

    def busy(self, target):
        raise OSError(16, 'Device or resource busy')

    monkeypatch.setattr(Path, 'replace', busy)
    sw._warned.clear()
    r.pause_symbol('INJUSDT')
    assert json.loads(path.read_text())['paused'], 'the pause was not persisted'
    assert list(tmp_path.glob('*.tmp')) == []
