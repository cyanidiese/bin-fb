"""Locked presets are per trading mode.

`locked_presets` was a flat {symbol: preset} dict shared by both instances. The mirror
mounts the same risk_config.json read-only, so the presets locked for testnet were also
being forced on the live market — where the ranking may well differ, which is the whole
reason the mirror exists.

Shape is now {"test": {...}, "live": {...}}. A legacy flat dict is read as the TEST set
so existing config keeps working unchanged, and live starts empty.
"""
import pytest

from config.risk_config import locked_presets_for

FLAT = {'EIGENUSDT': 'r5_sl_filter', 'TIAUSDT': 'l2_trend_buy'}
NESTED = {'test': {'TIAUSDT': 'l2_trend_buy'}, 'live': {'INJUSDT': 'r5_tight_rr3'}}


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


def test_main_passes_the_mode_everywhere():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'locked_presets_for' in src, 'main.py still reads locked_presets directly'
    assert 'risk_cfg.get("locked_presets"' not in src, \
        'a raw read remains — that instance would use the wrong mode'
