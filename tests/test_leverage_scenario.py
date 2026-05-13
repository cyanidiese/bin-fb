# tests/test_leverage_scenario.py
import json
import math
import pytest
from pathlib import Path
from bot.leverage_scenario import (
    DefaultScenario, AllocationScenario, FirstHasMostScenario, create_scenario
)


@pytest.fixture
def path(tmp_path):
    return tmp_path / 'lev.json'


# ── DefaultScenario ────────────────────────────────────────────────────────── #

def test_default_starts_at_level_1(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1


def test_default_does_not_advance_until_all_symbols_close(path):
    s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 1


def test_default_advances_when_all_close(path):
    s = DefaultScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    s.record_closed('ETHUSDT', 1)
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2


def test_default_capped_by_max_policy(path):
    s = DefaultScenario('test', ['BTCUSDT'], path, max_level=5)
    s.record_closed('BTCUSDT', 1)  # advances to 2
    # max_policy=2 caps even though level is 2
    assert s.get_leverage('BTCUSDT', 0.0, 1, 2, 10) == 2


def test_default_get_global_level(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_global_level() == 2


def test_default_get_symbol_level_equals_global(path):
    s = DefaultScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_symbol_level('BTCUSDT') == s.get_global_level()


def test_default_persists_and_reloads(path):
    s1 = DefaultScenario('test', ['BTCUSDT'], path)
    s1.record_closed('BTCUSDT', 1)
    s2 = DefaultScenario('test', ['BTCUSDT'], path)
    assert s2.get_global_level() == 2


# ── AllocationScenario ─────────────────────────────────────────────────────── #

def test_allocation_each_symbol_tracks_independently(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    # BTCUSDT should advance; ETHUSDT stays at 1
    assert s.get_leverage('BTCUSDT', 0.0, 1, 5, 10) == 2
    assert s.get_leverage('ETHUSDT', 0.0, 1, 5, 10) == 1


def test_allocation_get_symbol_level(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_symbol_level('BTCUSDT') == 2
    assert s.get_symbol_level('ETHUSDT') == 1


def test_allocation_get_global_level_is_min(path):
    s = AllocationScenario('test', ['BTCUSDT', 'ETHUSDT'], path)
    s.record_closed('BTCUSDT', 1)
    assert s.get_global_level() == 1  # min of [2, 1]


def test_allocation_persists_and_reloads(path):
    s1 = AllocationScenario('test', ['BTCUSDT'], path)
    s1.record_closed('BTCUSDT', 1)
    s2 = AllocationScenario('test', ['BTCUSDT'], path)
    assert s2.get_symbol_level('BTCUSDT') == 2


def test_allocation_new_symbol_starts_at_1(path):
    s = AllocationScenario('test', ['BTCUSDT'], path)
    s.record_closed('BTCUSDT', 1)  # BTCUSDT at level 2
    s.add_symbol('ETHUSDT')
    assert s.get_symbol_level('ETHUSDT') == 1


# ── FirstHasMostScenario ───────────────────────────────────────────────────── #

def test_first_has_most_score_0_gives_base(path):
    s = FirstHasMostScenario()
    assert s.get_leverage('BTCUSDT', 0.0, 2, 5, 10) == 2


def test_first_has_most_score_1_gives_max_policy(path):
    s = FirstHasMostScenario()
    assert s.get_leverage('BTCUSDT', 1.0, 2, 5, 10) == 5


def test_first_has_most_score_half(path):
    s = FirstHasMostScenario()
    # base=2, max_policy=6 → range=4, floor(0.5*4)=2 → 2+2=4
    assert s.get_leverage('BTCUSDT', 0.5, 2, 6, 10) == 4


def test_first_has_most_capped_by_bracket_max(path):
    s = FirstHasMostScenario()
    # score=1.0, base=2, max_policy=10, bracket_max=3 → min(10, 3)=3
    assert s.get_leverage('BTCUSDT', 1.0, 2, 10, 3) == 3


def test_first_has_most_record_closed_is_noop(path):
    s = FirstHasMostScenario()
    s.record_closed('BTCUSDT', 5)  # must not raise


def test_first_has_most_get_global_level_returns_0(path):
    s = FirstHasMostScenario()
    assert s.get_global_level() == 0


# ── Factory ────────────────────────────────────────────────────────────────── #

def test_create_scenario_default(path):
    s = create_scenario('default', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, DefaultScenario)


def test_create_scenario_allocation(path):
    s = create_scenario('allocation', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, AllocationScenario)


def test_create_scenario_first_has_most(path):
    s = create_scenario('first_has_most', 'test', [], path, 5)
    assert isinstance(s, FirstHasMostScenario)


def test_create_scenario_unknown_falls_back_to_default(path):
    s = create_scenario('bogus', 'test', ['BTCUSDT'], path, 5)
    assert isinstance(s, DefaultScenario)
