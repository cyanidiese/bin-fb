"""The duplicate-signal skip must protect every preset, not just the 27 that opted in.

After a stop-out the strategy often re-signals the same setup — same side, near the same
entry/SL/TP — within a candle or three. `duplicate_skip_candles` refuses that re-entry
(main.py:1135, and the simulator's copy at virtual_order_simulator.py:521). It was on for
only 27 of 87 presets; the other 60 inherited a base default of 0.

Measured 2026-09-13, 6 funded symbols, 60 days. A duplicate is a re-entry within 3 candles
of a loss close, same side, entry/SL/TP within 3%:

    duplicates, presets WITHOUT the filter   n=8885   28% win   -0.258%/trade
    all virtual orders (baseline)            n=57874  42% win   -0.052%/trade
    duplicates that leaked through WITH it   n=423    48% win   +0.372%/trade

Five times worse than baseline. The third line is the control: where the filter is on, the
re-entries outside its window are positive — the filter is catching the bad ones.

In real money: 9 such orders in 60 days, ZERO winners, -57.38 total. That matters because
the specific risk with any new block is cutting a winner — the EIGENUSDT `max_sl_pct`
removal was reverted within a day once the order it would have blocked returned +48.93.
Here there is no winner to cut.

Fixed by changing one default rather than editing 60 preset definitions: presets are
applied as overrides onto base Settings via dataclasses.replace, so presets that name
their own value keep it. See docs/specs/2026-09-13-duplicate-skip-default-on.md.
"""
import os
import re
from pathlib import Path

import pytest

from config.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]


def _preset_overrides() -> dict:
    """Parse each preset block's explicit duplicate_skip_candles, or 0 if absent."""
    src = (ROOT / 'config' / 'presets.py').read_text()
    out = {}
    for m in re.finditer(r"^\s*'([A-Za-z0-9_]+)':\s*\{", src, re.M):
        name, start, depth, i = m.group(1), m.end(), 1, m.end()
        while i < len(src) and depth > 0:
            if src[i] == '{':
                depth += 1
            elif src[i] == '}':
                depth -= 1
            i += 1
        body = src[start:i]
        dm = re.search(r"'duplicate_skip_candles':\s*(\d+)", body)
        out[name] = int(dm.group(1)) if dm else None
    return out


class TestTheBaseDefault:
    def test_it_is_on_by_default(self):
        assert load_settings('BTCUSDT').duplicate_skip_candles == 3, \
            'presets that never opted in are still re-entering after a stop-out'

    def test_the_tolerance_default_is_unchanged(self):
        assert load_settings('BTCUSDT').duplicate_skip_pct == pytest.approx(2.0)

    def test_the_env_var_still_overrides(self, monkeypatch):
        monkeypatch.setenv('DUPLICATE_SKIP_CANDLES', '5')
        assert load_settings('BTCUSDT').duplicate_skip_candles == 5

    def test_zero_restores_the_old_behaviour(self, monkeypatch):
        """The instant revert: no code redeploy needed."""
        monkeypatch.setenv('DUPLICATE_SKIP_CANDLES', '0')
        assert load_settings('BTCUSDT').duplicate_skip_candles == 0


class TestExplicitPresetsAreUntouched:
    def test_presets_that_chose_a_value_keep_it(self):
        """The 27 that opted in were deliberately tuned — 1, 2, 3, 4 and 10 candles."""
        import dataclasses

        base = load_settings('BTCUSDT')
        for name, explicit in _preset_overrides().items():
            if explicit is None:
                continue
            merged = dataclasses.replace(base, duplicate_skip_candles=explicit)
            assert merged.duplicate_skip_candles == explicit, name

    def test_the_opted_in_count_is_what_we_measured(self):
        explicit = [v for v in _preset_overrides().values() if v is not None]
        assert len(explicit) == 27, (
            f'{len(explicit)} presets name duplicate_skip_candles; the measurement that '
            f'justified this change assumed 27 opted in and 60 did not'
        )

    def test_no_preset_silently_disables_it(self):
        """A preset setting it back to 0 would opt out of the protection — flag it."""
        zeros = [n for n, v in _preset_overrides().items() if v == 0]
        assert not zeros, f'presets explicitly disabling the duplicate skip: {zeros}'


class TestEveryPresetIsNowCovered:
    def test_all_87_presets_have_the_filter_active(self):
        base = load_settings('BTCUSDT')
        overrides = _preset_overrides()
        assert len(overrides) == 87
        effective = [
            (v if v is not None else base.duplicate_skip_candles)
            for v in overrides.values()
        ]
        assert all(v > 0 for v in effective), \
            f'{len([v for v in effective if v == 0])} presets still unprotected'


class TestTheCodeThatConsumesItIsUnchanged:
    """This change turns on existing, exercised code — it must stay wired."""

    def test_the_signal_path_still_gates_on_it(self):
        src = (ROOT / 'main.py').read_text()
        assert 'preset_settings.duplicate_skip_candles > 0' in src

    def test_the_simulator_still_gates_on_it(self):
        src = (ROOT / 'bot' / 'virtual_order_simulator.py').read_text()
        assert 'duplicate_skip_candles' in src
