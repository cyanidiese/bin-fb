import json
import pytest
from pathlib import Path
from bot.leverage_tracker import LeverageTracker


@pytest.fixture
def path(tmp_path):
    return tmp_path / 'leverage_state.json'


def test_starts_at_level_1(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    assert lt.get_current_level() == 1


def test_does_not_advance_with_partial_graduation(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    assert lt.get_current_level() == 1  # ETHUSDT hasn't closed level 1 yet


def test_advances_when_all_symbols_graduate(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    lt.record_closed('ETHUSDT', 1)
    assert lt.get_current_level() == 2


def test_new_symbol_blocks_next_advance(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.add_symbol('ETHUSDT')          # ETHUSDT must now complete level 2
    lt.record_closed('BTCUSDT', 2)
    assert lt.get_current_level() == 2  # ETHUSDT has not closed level 2


def test_new_symbol_unblocks_after_one_close_at_current_level(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.add_symbol('ETHUSDT')
    lt.record_closed('BTCUSDT', 2)
    lt.record_closed('ETHUSDT', 2)   # both at level 2 → advance to 3
    assert lt.get_current_level() == 3


def test_remove_symbol_may_unblock_advance(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    lt.record_closed('BTCUSDT', 1)
    assert lt.get_current_level() == 1  # blocked by ETHUSDT
    lt.remove_symbol('ETHUSDT')         # only BTCUSDT needed, already done
    assert lt.get_current_level() == 2


def test_capped_at_max_level(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path, max_level=2)
    lt.record_closed('BTCUSDT', 1)   # advances to 2
    lt.record_closed('BTCUSDT', 2)   # would advance to 3, capped at max=2
    assert lt.get_current_level() == 2


def test_persists_state_and_loads(path):
    lt1 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    lt1.record_closed('BTCUSDT', 1)
    assert lt1.get_current_level() == 2
    # Second instance loads from disk
    lt2 = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    assert lt2.get_current_level() == 2


def test_no_advance_with_no_active_symbols(path):
    lt = LeverageTracker(mode='test', active_symbols=[], data_path=path)
    assert lt.get_current_level() == 1  # stays frozen


def test_record_closed_returns_true_when_advanced(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT'], data_path=path)
    advanced = lt.record_closed('BTCUSDT', 1)
    assert advanced is True


def test_record_closed_returns_false_when_not_advanced(path):
    lt = LeverageTracker(mode='test', active_symbols=['BTCUSDT', 'ETHUSDT'], data_path=path)
    advanced = lt.record_closed('BTCUSDT', 1)
    assert advanced is False
