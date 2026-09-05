"""max_profit_pct can be scoped to specific trend levels.

The TP is projected from the swing structure, so how far it lands depends on which
level produced the signal. EIGENUSDT's L3 signals project targets the instrument
does not reach; its L2 and L4 signals do not show the same problem, so the cap has
to be level-scoped rather than global.
"""
import dataclasses

import pytest

from config.settings import Settings, load_settings, max_profit_cap_applies


@pytest.fixture
def base() -> Settings:
    return load_settings()


def test_disabled_when_cap_is_zero(base):
    s = dataclasses.replace(base, max_profit_pct=0.0, max_profit_pct_levels=())
    assert max_profit_cap_applies(s, 3) is False


def test_empty_levels_applies_everywhere(base):
    """Existing presets carry no level list and must keep their current behaviour."""
    s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=())
    for level in (None, 1, 2, 3, 4, 9):
        assert max_profit_cap_applies(s, level) is True


def test_scoped_to_listed_level_only(base):
    s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3,))
    assert max_profit_cap_applies(s, 3) is True
    assert max_profit_cap_applies(s, 2) is False
    assert max_profit_cap_applies(s, 4) is False


def test_unknown_level_is_left_uncapped(base):
    """A level-scoped cap is not applied to a signal whose level we cannot confirm."""
    s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3,))
    assert max_profit_cap_applies(s, None) is False


def test_multiple_levels(base):
    s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3, 4))
    assert max_profit_cap_applies(s, 3) is True
    assert max_profit_cap_applies(s, 4) is True
    assert max_profit_cap_applies(s, 2) is False


def test_accepts_a_json_list(base):
    """risk_config.json supplies a list, not a tuple, via per_symbol_settings."""
    s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=[3])
    assert max_profit_cap_applies(s, 3) is True
    assert max_profit_cap_applies(s, 2) is False


def test_per_symbol_override_reaches_the_field(base):
    """per_symbol_settings merges by Settings field name — the key must be valid."""
    valid = {f.name for f in dataclasses.fields(Settings)}
    assert 'max_profit_pct_levels' in valid
    merged = dataclasses.replace(base, **{'max_profit_pct': 8.0, 'max_profit_pct_levels': [3]})
    assert max_profit_cap_applies(merged, 3) is True
