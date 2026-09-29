"""Signal filters: the per-signal gates and generators that decide whether a candidate trades.

Sections (one per former test file):
  - max SL clamp                 (was test_max_sl_clamp.py)
  - SL clamp is opt-in           (was test_sl_clamp_optional.py)
  - max_profit_pct level scoping (was test_max_profit_pct_levels.py)
  - parent-alignment hard gate   (was test_parent_alignment_gate.py)
  - duplicate-skip default on    (was test_duplicate_skip_default.py)
  - mean-reversion engine wiring (was test_mr_engine.py)
  - mean-reversion primitives    (was test_mean_reversion.py)
  - loss-streak cooldown         (was test_loss_streak.py)
"""
import dataclasses
import inspect
import re

import pytest

import bot.recommendation_engine as re_mod
from bot.analyzer import Analyzer
from bot.mean_reversion import MRConfig, Range, detect_range, MRSignal, mr_signal
from bot.recommendation import Recommendation, RecommendationTypes
from bot.recommendation_engine import RecommendationEngine
from config.settings import (
    Settings, clamp_sl_to_max, load_settings, max_profit_cap_applies,
)
from tests.factories import src


# =========================================================================== #
# Max SL clamp (was test_max_sl_clamp.py)                                     #
# =========================================================================== #
# An over-wide stop is clamped to the cap, not thrown away with the signal.
#
# `min_sl_pct` has always *widened* a too-tight stop ("SL floored: X% -> Y%") while
# `max_sl_pct` *rejected the signal outright*. That asymmetry was expensive: TIAUSDT, the
# #2 ranked symbol, had 107 signals rejected at 8.24-11.72% against an 8% cap, and in one
# 36h window 40 were rejected while the bot placed 1 real order in total.
#
# Clamping is also strictly safer than rejecting at the extreme end. The only catastrophic
# loss in 410 real trades was a TIAUSDT SELL with a 22.52% stop, -206.12; under a 12% clamp
# that same signal risks 12% instead of 22.52%.
#
# The clamp deliberately runs BEFORE the ATR floor and the RR rules in main.py, so a
# clamped stop is still subject to the preset's own geometry checks — if pulling the stop
# in makes it too tight for the instrument's volatility, min_sl_atr_mult rejects it.

class TestBuy:
    def test_a_stop_inside_the_cap_is_untouched(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 94.0, 6.0, 'BUY', 12.0)
        assert (sl, pct, clamped) == (94.0, 6.0, False)

    def test_an_over_wide_stop_is_pulled_in_to_the_cap(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
        assert clamped is True
        assert pct == pytest.approx(12.0)
        assert sl == pytest.approx(88.0)          # entry * (1 - 12/100)

    def test_the_clamped_stop_stays_below_entry(self):
        sl, _, _ = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', 12.0)
        assert sl < 100.0


class TestSell:
    """sl_dist_pct is inflated x1.5 for SELL, so the inverse must divide by 1.5 —
    exactly what the existing min_sl_pct floor does."""

    def test_a_stop_inside_the_cap_is_untouched(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 104.0, 6.0, 'SELL', 12.0)
        assert (sl, pct, clamped) == (104.0, 6.0, False)

    def test_an_over_wide_stop_is_pulled_in_using_the_1_5_convention(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 122.52, 22.52 * 1.5, 'SELL', 12.0)
        assert clamped is True
        assert pct == pytest.approx(12.0)
        assert sl == pytest.approx(108.0)         # entry * (1 + 12/1.5/100)

    def test_the_clamped_stop_stays_above_entry(self):
        sl, _, _ = clamp_sl_to_max(100.0, 160.0, 90.0, 'SELL', 12.0)
        assert sl > 100.0

    def test_buy_and_sell_clamps_are_not_symmetric(self):
        """A SELL clamp lands closer in price terms because of the x1.5 weighting."""
        b, _, _ = clamp_sl_to_max(100.0, 70.0, 30.0, 'BUY', 12.0)
        s, _, _ = clamp_sl_to_max(100.0, 130.0, 30.0, 'SELL', 12.0)
        assert abs(100.0 - b) == pytest.approx(12.0)
        assert abs(100.0 - s) == pytest.approx(8.0)


class TestDisabled:
    def test_a_zero_cap_disables_clamping(self):
        """max_sl_pct=0 means 'no cap', the existing convention everywhere else."""
        sl, pct, clamped = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', 0.0)
        assert (sl, pct, clamped) == (50.0, 50.0, False)

    def test_a_negative_cap_disables_clamping(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 50.0, 50.0, 'BUY', -1.0)
        assert clamped is False


class TestConsistency:
    def test_clamping_always_reduces_risk(self):
        """The whole point: never widen, only pull in."""
        for side, sl in (('BUY', 60.0), ('SELL', 140.0)):
            for cap in (8.0, 10.0, 12.0, 15.0):
                raw_pct = abs(100.0 - sl) / 100.0 * 100 * (1.5 if side == 'SELL' else 1)
                new_sl, new_pct, clamped = clamp_sl_to_max(100.0, sl, raw_pct, side, cap)
                if clamped:
                    assert abs(100.0 - new_sl) <= abs(100.0 - sl), (side, cap)
                    assert new_pct <= raw_pct

    def test_it_is_idempotent(self):
        sl, pct, _ = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
        sl2, pct2, clamped2 = clamp_sl_to_max(100.0, sl, pct, 'BUY', 12.0)
        assert clamped2 is False
        assert (sl2, pct2) == (sl, pct)


class TestMaxSlClampEngines:
    def test_all_three_engines_use_the_shared_helper(self):
        """live, virtual and backtest must agree — otherwise the virtual statistics
        measure a different strategy than the one that trades, which is exactly why there
        was no data on the rejected signals for two months.

        Both behaviours are kept: rejecting is the default, clamping is opt-in per symbol
        (see TestSlClampOptional below). What matters here is that no engine rolls
        its own clamp arithmetic.
        """
        for f in ('main.py', 'bot/virtual_order_simulator.py', 'bot/backtester.py'):
            text = src(f)
            assert 'clamp_sl_to_max' in text, f'{f} does not use the shared helper'
            assert 'sl_clamp_enabled' in text, f'{f} does not gate on the flag'


# =========================================================================== #
# SL clamp is opt-in (was test_sl_clamp_optional.py)                          #
# =========================================================================== #

SL_CLAMP_ENGINES = ('main.py', 'bot/virtual_order_simulator.py', 'bot/backtester.py')


class TestSlClampOptional:
    """Clamping an over-wide stop is opt-in, per symbol, and OFF by default.

    Rejecting the signal is a two-month-old, data-backed behaviour. Switching every symbol
    to clamping in one deploy would change what the bot trades without a controlled
    comparison, so the flag ships disabled and is turned on per symbol via
    `risk_config.per_symbol_settings.<SYMBOL>.sl_clamp_enabled`, alongside the `max_sl_pct`
    that becomes the clamp target.
    """

    def test_the_flag_exists_on_settings(self):
        """It must live on Settings, because per_symbol_settings is applied with
        dataclasses.replace and silently drops keys that are not fields."""
        assert 'sl_clamp_enabled' in {f.name for f in dataclasses.fields(Settings)}

    def test_it_defaults_to_off(self, monkeypatch):
        monkeypatch.delenv('SL_CLAMP_ENABLED', raising=False)
        monkeypatch.setenv('TRADING_MODE', 'test')
        monkeypatch.setenv('TESTNET_API_KEY', 'k')
        monkeypatch.setenv('TESTNET_API_SECRET', 's')
        monkeypatch.setenv('SYMBOL', 'INJUSDT')
        assert load_settings().sl_clamp_enabled is False, \
            'shipping this on would change what the bot trades without a comparison'

    def test_the_env_var_can_turn_it_on(self, monkeypatch):
        for v in ('1', 'true', 'yes', 'TRUE'):
            monkeypatch.setenv('SL_CLAMP_ENABLED', v)
            monkeypatch.setenv('TRADING_MODE', 'test')
            monkeypatch.setenv('TESTNET_API_KEY', 'k')
            monkeypatch.setenv('TESTNET_API_SECRET', 's')
            monkeypatch.setenv('SYMBOL', 'INJUSDT')
            assert load_settings().sl_clamp_enabled is True, v

    def test_it_is_settable_per_symbol_via_dataclasses_replace(self, monkeypatch):
        """The exact mechanism risk_config uses: per_symbol_settings -> replace().

        Built through load_settings() rather than a synthetic Settings, so this cannot pass
        against a hand-made object that does not match the real one.
        """
        monkeypatch.delenv('SL_CLAMP_ENABLED', raising=False)
        monkeypatch.setenv('TRADING_MODE', 'test')
        monkeypatch.setenv('TESTNET_API_KEY', 'k')
        monkeypatch.setenv('TESTNET_API_SECRET', 's')
        monkeypatch.setenv('SYMBOL', 'INJUSDT')
        base = load_settings()
        assert base.sl_clamp_enabled is False

        # exactly what main.py does with per_symbol_settings
        overrides = {'sl_clamp_enabled': True, 'max_sl_pct': 12.0}
        valid = {f.name for f in dataclasses.fields(Settings)}
        tuned = dataclasses.replace(base, **{k: v for k, v in overrides.items() if k in valid})

        assert tuned.sl_clamp_enabled is True, 'per-symbol enable did not take'
        assert tuned.max_sl_pct == 12.0, 'per-symbol percent did not take'
        assert base.sl_clamp_enabled is False, 'replace must not mutate the original'

    # ----------------------------------------------------------------------- #
    # All three engines must branch on the flag, and reject when it is off    #
    # ----------------------------------------------------------------------- #

    @pytest.mark.parametrize('path', SL_CLAMP_ENGINES)
    def test_every_engine_branches_on_the_flag(self, path):
        text = src(path)
        assert 'sl_clamp_enabled' in text, f'{path} does not consult the flag'

    @pytest.mark.parametrize('path', SL_CLAMP_ENGINES)
    def test_every_engine_still_has_a_reject_path(self, path):
        """With the flag off, behaviour must be exactly what ships today."""
        text = src(path)
        assert 'clamp_sl_to_max' in text, f'{path} lost the clamp'
        if path == 'main.py':
            assert "decision='skip_max_sl_pct'" in text, 'main.py lost the reject path'
        else:
            assert 'max_sl_pct' in text, f'{path} lost the reject path'

    def test_the_helper_itself_is_unconditional(self):
        """clamp_sl_to_max stays a pure function — the flag is the caller's decision, so
        the helper can be reasoned about and tested on its own."""
        text = inspect.getsource(clamp_sl_to_max)
        assert 'sl_clamp_enabled' not in text

    def test_clamping_is_still_correct_when_enabled(self):
        sl, pct, clamped = clamp_sl_to_max(100.0, 78.0, 22.0, 'BUY', 12.0)
        assert (round(sl, 6), pct, clamped) == (88.0, 12.0, True)


# =========================================================================== #
# max_profit_pct level scoping (was test_max_profit_pct_levels.py)            #
# =========================================================================== #

class TestMaxProfitPctLevels:
    """max_profit_pct can be scoped to specific trend levels.

    The TP is projected from the swing structure, so how far it lands depends on which
    level produced the signal. EIGENUSDT's L3 signals project targets the instrument
    does not reach; its L2 and L4 signals do not show the same problem, so the cap has
    to be level-scoped rather than global.
    """

    @pytest.fixture
    def base(self) -> Settings:
        return load_settings()

    def test_disabled_when_cap_is_zero(self, base):
        s = dataclasses.replace(base, max_profit_pct=0.0, max_profit_pct_levels=())
        assert max_profit_cap_applies(s, 3) is False

    def test_empty_levels_applies_everywhere(self, base):
        """Existing presets carry no level list and must keep their current behaviour."""
        s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=())
        for level in (None, 1, 2, 3, 4, 9):
            assert max_profit_cap_applies(s, level) is True

    def test_scoped_to_listed_level_only(self, base):
        s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3,))
        assert max_profit_cap_applies(s, 3) is True
        assert max_profit_cap_applies(s, 2) is False
        assert max_profit_cap_applies(s, 4) is False

    def test_unknown_level_is_left_uncapped(self, base):
        """A level-scoped cap is not applied to a signal whose level we cannot confirm."""
        s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3,))
        assert max_profit_cap_applies(s, None) is False

    def test_multiple_levels(self, base):
        s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=(3, 4))
        assert max_profit_cap_applies(s, 3) is True
        assert max_profit_cap_applies(s, 4) is True
        assert max_profit_cap_applies(s, 2) is False

    def test_accepts_a_json_list(self, base):
        """risk_config.json supplies a list, not a tuple, via per_symbol_settings."""
        s = dataclasses.replace(base, max_profit_pct=8.0, max_profit_pct_levels=[3])
        assert max_profit_cap_applies(s, 3) is True
        assert max_profit_cap_applies(s, 2) is False

    def test_per_symbol_override_reaches_the_field(self, base):
        """per_symbol_settings merges by Settings field name — the key must be valid."""
        valid = {f.name for f in dataclasses.fields(Settings)}
        assert 'max_profit_pct_levels' in valid
        merged = dataclasses.replace(base, **{'max_profit_pct': 8.0, 'max_profit_pct_levels': [3]})
        assert max_profit_cap_applies(merged, 3) is True


# =========================================================================== #
# Parent-alignment hard gate (was test_parent_alignment_gate.py)              #
# =========================================================================== #

PARENT_GATE_BASE = load_settings()

PERMISSIVE_CFG = {
    'global_min_rr': 0.0, 'global_max_rr': 0.0, 'global_min_sl_pct': 0.0,
    'global_trend_regime_filter': False, 'global_blocked_signal_types': [],
    'global_max_level': 0, 'entry_zone_max_pct': 1.0, 'global_correction_weight': -1.0,
    'global_enforce_parent_alignment': False,
}


class FakeTrend:
    """Minimal stand-in; the parent/precision/range calls are overridden below."""
    pass


class GatedEngine(RecommendationEngine):
    def __init__(self, settings, opposing):
        super().__init__(settings)
        self._opposing = opposing

    def _parent_is_opposing(self, trend, side):
        return self._opposing

    def _passes_range_position(self, rec, trend):
        return True

    def _precision(self, rec, trend, correction_info=None, correction_weight_override=-1.0):
        return 0.5


def _make_gate_rec(side, rtype):
    if side == 'BUY':
        entry, tp, sl = 100.0, 110.0, 95.0
    else:
        entry, tp, sl = 100.0, 90.0, 105.0
    rec = Recommendation(None, tp, sl, side, rtype)
    rec.setEntryPrice(entry).setHowClose(0.0).setLevel(1)
    return rec


def _survives(monkeypatch, *, ignore, enforce_hard, g_enforce, opposing,
              side='BUY', rtype=RecommendationTypes.RISING_BELOW_LAST_HIGH):
    cfg = dict(PERMISSIVE_CFG, global_enforce_parent_alignment=g_enforce)
    monkeypatch.setattr(re_mod, 'load_risk_config', lambda: cfg)
    s = dataclasses.replace(PARENT_GATE_BASE, ignore_parent_alignment=ignore,
                            enforce_parent_alignment_hard=enforce_hard, signal_direction='both')
    eng = GatedEngine(s, opposing)
    out = eng._score_and_filter([(_make_gate_rec(side, rtype), FakeTrend(), None)])
    return len(out) == 1


class TestParentAlignmentGate:
    """Gate 1 — parent-alignment hard gate (decouples drought-escape from alignment enforcement).

    The opposing-parent hard reject must fire for continuation signals when the parent trend
    explicitly opposes, under ANY of:
      - ignore_parent_alignment=False  (original behaviour), OR
      - enforce_parent_alignment_hard=True (per-preset override), OR
      - global_enforce_parent_alignment=True (risk_config override)
    and must NOT fire when the parent is aligned or undetermined (no drought regression).
    """

    # --- opposing parent: gate should BLOCK under each enforcement path ---

    def test_blocks_when_alignment_not_ignored(self, monkeypatch):
        assert not _survives(monkeypatch, ignore=False, enforce_hard=False, g_enforce=False, opposing=True)

    def test_blocks_when_preset_enforce_hard(self, monkeypatch):
        # The core new behaviour: ignore=True would previously let this through.
        assert not _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False, opposing=True)

    def test_blocks_when_global_enforce(self, monkeypatch):
        assert not _survives(monkeypatch, ignore=True, enforce_hard=False, g_enforce=True, opposing=True)

    def test_passes_when_ignore_and_no_enforce(self, monkeypatch):
        # Legacy escape hatch preserved: ignore=True, no enforce → opposing signal passes.
        assert _survives(monkeypatch, ignore=True, enforce_hard=False, g_enforce=False, opposing=True)

    # --- aligned / undetermined parent: gate must NOT block (no drought regression) ---

    def test_passes_when_parent_not_opposing_even_if_enforced(self, monkeypatch):
        assert _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False, opposing=False)

    def test_sell_blocked_when_enforced_and_opposing(self, monkeypatch):
        assert not _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False,
                             opposing=True, side='SELL',
                             rtype=RecommendationTypes.LOWERING_ABOVE_LAST_LOW)

    # --- non-continuation (reversal/structural) types are exempt from the gate ---

    def test_reversal_type_exempt_even_when_enforced(self, monkeypatch):
        assert _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False,
                         opposing=True, side='BUY',
                         rtype=RecommendationTypes.RISING_ABOVE_SUPPOSED_HIGH)


# =========================================================================== #
# Duplicate-skip default on (was test_duplicate_skip_default.py)              #
# =========================================================================== #
# The duplicate-signal skip must protect every preset, not just the 27 that opted in.
#
# After a stop-out the strategy often re-signals the same setup — same side, near the same
# entry/SL/TP — within a candle or three. `duplicate_skip_candles` refuses that re-entry
# (main.py:1135, and the simulator's copy at virtual_order_simulator.py:521). It was on for
# only 27 of 87 presets; the other 60 inherited a base default of 0.
#
# Measured 2026-09-13, 6 funded symbols, 60 days. A duplicate is a re-entry within 3 candles
# of a loss close, same side, entry/SL/TP within 3%:
#
#     duplicates, presets WITHOUT the filter   n=8885   28% win   -0.258%/trade
#     all virtual orders (baseline)            n=57874  42% win   -0.052%/trade
#     duplicates that leaked through WITH it   n=423    48% win   +0.372%/trade
#
# Five times worse than baseline. The third line is the control: where the filter is on, the
# re-entries outside its window are positive — the filter is catching the bad ones.
#
# In real money: 9 such orders in 60 days, ZERO winners, -57.38 total. That matters because
# the specific risk with any new block is cutting a winner — the EIGENUSDT `max_sl_pct`
# removal was reverted within a day once the order it would have blocked returned +48.93.
# Here there is no winner to cut.
#
# Fixed by changing one default rather than editing 60 preset definitions: presets are
# applied as overrides onto base Settings via dataclasses.replace, so presets that name
# their own value keep it. See docs/specs/2026-09-13-duplicate-skip-default-on.md.

def _preset_overrides() -> dict:
    """Parse each preset block's explicit duplicate_skip_candles, or 0 if absent."""
    text = src('config/presets.py')
    out = {}
    for m in re.finditer(r"^\s*'([A-Za-z0-9_]+)':\s*\{", text, re.M):
        name, start, depth, i = m.group(1), m.end(), 1, m.end()
        while i < len(text) and depth > 0:
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
            i += 1
        body = text[start:i]
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
        assert 'preset_settings.duplicate_skip_candles > 0' in src('main.py')

    def test_the_simulator_still_gates_on_it(self):
        assert 'duplicate_skip_candles' in src('bot/virtual_order_simulator.py')


# =========================================================================== #
# Mean-reversion helpers shared by the two MR sections                        #
# =========================================================================== #

def _k(o, h, l, c, t=0):
    return [t, o, h, l, c, 0]


# =========================================================================== #
# Mean-reversion engine wiring (was test_mr_engine.py)                        #
# =========================================================================== #

def _oscillating_klines(n=60, lo=100.0, hi=110.0):
    # NOTE: deviates from the task brief's literal fixture in two deliberate ways
    # (see task-4-report.md "Fixture bugs found" for the full diagnosis):
    #   1. A tiny deterministic jitter is added to alternating peak/trough candles.
    #      KlineProcessor (swing_neighbours=2) confirms a swing high/low only when a
    #      candle's high/low is *strictly* greater/less than same-parity neighbours
    #      2 candles away. A perfectly repeating hi/lo/hi/lo pattern ties at that
    #      distance, so zero swing points are ever confirmed and
    #      Trend.getCurrentPoint() stays None forever -- the jitter breaks the tie
    #      without changing the window's true hi/lo (both are still touched exactly).
    #   2. The final "poke" candle's high is set to exactly `hi` (touching the
    #      boundary) instead of overshooting it (e.g. 110.2). detect_range()'s
    #      window includes this last candle, so an overshoot becomes the new
    #      window max and shifts hi/mid, breaking the mid=105.0 assertion below.
    #      Touching (not exceeding) hi still satisfies mr_signal's wick-rejection
    #      condition (h > rng.hi - 0.02*span) and fires the SELL fade.
    out = []
    for i in range(n):
        if i % 2 == 0:
            h = hi - 0.3 if (i // 2) % 2 == 0 else hi
            out.append(_k(105, h, 104, 106, i * 900_000))
        else:
            l = lo + 0.3 if ((i - 1) // 2) % 2 == 0 else lo
            out.append(_k(105, 106, l, 104, i * 900_000))
    # final candle: SELL fade (touch hi, close back inside near top)
    out.append(_k(109, hi, 108.5, 109.0, n * 900_000))
    return out


class TestMrEngine:
    def test_engine_emits_mr_fade_when_enabled_and_range_confirmed(self):
        s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
        engine = RecommendationEngine(s)
        analyzer = Analyzer(s.swing_neighbours, engine)
        kl = _oscillating_klines()
        analyzer.build_from_klines(kl)
        trend = analyzer.get_trend()
        rec = engine.generate(trend, 109.0, recent_klines=kl)
        assert rec is not None
        assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
        assert rec.getSide() == 'SELL'
        assert abs(rec.getTarget() - 105.0) < 1e-6      # TP=mid
        assert rec.getStop() > 110.0                      # SL beyond boundary

    def test_engine_excludes_signal_candle_from_range(self):
        # Fidelity guard (Gate-A follow-up): detect_range must be computed on the
        # window BEFORE the signal candle, matching the validated probe mr_refine.py
        # (kl[i-W:i]). The fade candle deliberately pokes past the boundary, so
        # including it in the range contaminates hi/mid. Here the poke overshoots
        # to 110.5: if it were inside the range, mid would be (110.5+100)/2=105.25;
        # excluded, mid stays 105.0 and TP=mid=105.0.
        s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
        engine = RecommendationEngine(s)
        analyzer = Analyzer(s.swing_neighbours, engine)
        base = _oscillating_klines()[:-1]                      # 60 candles, range 100-110
        poke = _k(109, 110.5, 108.5, 109.0, 60 * 900_000)      # SELL fade, OVERSHOOTS top
        kl = base + [poke]
        analyzer.build_from_klines(kl)
        trend = analyzer.get_trend()
        rec = engine.generate(trend, 109.0, recent_klines=kl)
        assert rec is not None
        assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
        assert rec.getSide() == 'SELL'
        assert abs(rec.getTarget() - 105.0) < 1e-6            # mid excludes the 110.5 poke

    def test_analyzer_add_candle_routes_mr(self):
        s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
        engine = RecommendationEngine(s)
        analyzer = Analyzer(s.swing_neighbours, engine)
        kl = _oscillating_klines()
        analyzer.build_from_klines(kl[:-1])
        analyzer.update_price(109.0)
        analyzer.add_candle(kl[-1])                 # feeds final fade candle
        rec = analyzer.get_best_recommendation()
        assert rec is not None
        assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE

    def test_engine_ignores_mr_when_disabled(self):
        s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=False)
        engine = RecommendationEngine(s)
        analyzer = Analyzer(s.swing_neighbours, engine)
        kl = _oscillating_klines()
        analyzer.build_from_klines(kl)
        trend = analyzer.get_trend()
        rec = engine.generate(trend, 109.0, recent_klines=kl)
        # MR off -> no MR rec (trend engine may or may not fire, but never MR type)
        assert rec is None or rec.getType() != RecommendationTypes.MEAN_REVERT_FADE


# =========================================================================== #
# Mean-reversion primitives (was test_mean_reversion.py)                      #
# =========================================================================== #

def _oscillating(n=48, lo=100.0, hi=110.0):
    # alternately tags the low and the high -> both boundaries tested
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(_k(105, hi, 104, 106, i))   # tags high
        else:
            out.append(_k(105, 106, lo, 104, i))   # tags low
    return out


def _rng():
    return Range(hi=110.0, lo=100.0, mid=105.0, width=10.0 / 105.0)


class TestMeanReversion:
    def test_detect_range_qualifies_on_oscillating_window(self):
        cfg = MRConfig()
        rng = detect_range(_oscillating(), cfg)
        assert rng is not None
        assert abs(rng.hi - 110.0) < 1e-9 and abs(rng.lo - 100.0) < 1e-9
        assert abs(rng.mid - 105.0) < 1e-9

    def test_detect_range_rejects_one_sided_window(self):
        # Genuine one-sided window: the top is tagged every candle, but the low is
        # reached only ONCE (below min_touches=2) -> not a confirmed oscillating range.
        cfg = MRConfig()
        kl = []
        for i in range(48):
            if i == 0:
                kl.append(_k(105, 110, 100, 109, i))   # the ONLY candle reaching the low
            else:
                kl.append(_k(109, 110, 108, 109, i))   # hugs the top only
        assert detect_range(kl, cfg) is None

    def test_detect_range_rejects_breakout_series(self):
        # steadily rising -> width blows out / boundaries not both re-tested
        cfg = MRConfig()
        kl = [_k(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(48)]
        assert detect_range(kl, cfg) is None

    def test_detect_range_needs_full_window(self):
        cfg = MRConfig()
        assert detect_range(_oscillating(n=10), cfg) is None

    def test_mr_signal_sell_on_top_wick_rejection(self):
        cfg = MRConfig()
        # last candle: pokes above hi (110.2) but closes back inside near top (109.0)
        kl = _oscillating() + [_k(109, 110.2, 108.5, 109.0, 99)]
        sig = mr_signal(kl, _rng(), cfg)
        assert sig is not None and sig.side == 'SELL'
        assert abs(sig.tp - 105.0) < 1e-9            # TP = mid
        assert sig.sl > 110.0                         # SL beyond hi boundary
        assert abs(sig.entry - 109.0) < 1e-9          # entry = close

    def test_mr_signal_buy_on_bottom_wick_rejection(self):
        cfg = MRConfig()
        kl = _oscillating() + [_k(101, 101.5, 99.8, 101.0, 99)]  # pokes below lo, closes inside
        sig = mr_signal(kl, _rng(), cfg)
        assert sig is not None and sig.side == 'BUY'
        assert abs(sig.tp - 105.0) < 1e-9
        assert sig.sl < 100.0

    def test_mr_signal_none_when_mid_range(self):
        cfg = MRConfig()
        kl = _oscillating() + [_k(105, 105.5, 104.5, 105.0, 99)]  # close at mid, no fade
        assert mr_signal(kl, _rng(), cfg) is None

    def test_mr_signal_none_without_wick_rejection(self):
        cfg = MRConfig()
        # closes ABOVE hi (breakout, no rejection back inside) -> no fade
        kl = _oscillating() + [_k(109, 111.0, 108.5, 110.8, 99)]
        assert mr_signal(kl, _rng(), cfg) is None

    def test_mr_signal_fires_sell_when_close_above_hi_with_strong_wick(self):
        # Fidelity to the validated probe: a candle that pokes well above hi and
        # closes ABOVE hi but with a strong upper wick STILL fires a SELL fade.
        # The probe has NO "closed back inside the range" requirement.
        cfg = MRConfig()
        kl = _oscillating() + [_k(109, 115.0, 108.0, 111.0, 99)]  # h=115, close 111 > hi 110, big wick
        sig = mr_signal(kl, _rng(), cfg)
        assert sig is not None and sig.side == 'SELL'
        assert abs(sig.tp - 105.0) < 1e-9

    def test_config_wiring_exists(self):
        from config.presets import ALL_PRESETS
        assert RecommendationTypes.MEAN_REVERT_FADE.value == 'mean_revert_fade'
        s = load_settings('TIAUSDT')
        assert s.enable_mean_reversion is False       # OFF by default
        assert s.mr_window == 48 and s.mr_sl_buf == 0.5
        assert 'mr_fade' in ALL_PRESETS


# =========================================================================== #
# Loss-streak cooldown (was test_loss_streak.py)                              #
# =========================================================================== #
# Unit tests for the loss-streak directional cooldown logic.
#
# The state management lives inside main.run() closures, so we test the logic
# directly by replicating the exact dict operations used in _update_loss_streak
# and the gate check in _try_place_order.

_TF_MS = 15 * 60 * 1000  # 15-minute candle in ms


def _update(
    c: dict,
    ts: int,
    loss_streak: dict,
    streak_blocked: dict,
    global_pause_until: dict,
    last_loss_ts: dict,
    loss_streak_max: int,
    cooldown_candles: int,
    global_pause_trigger: int,
    global_pause_candles: int,
) -> None:
    """Mirror of main._update_loss_streak for isolated testing."""
    sym = c['symbol']
    pname = c.get('preset_name', 'default')
    side = c.get('side', '')
    if loss_streak_max <= 0:
        return
    sk = f"{sym}:{pname}:{side}"
    other_sk = f"{sym}:{pname}:{'SELL' if side == 'BUY' else 'BUY'}"
    if c.get('result') == 'loss':
        cnt = loss_streak.get(sk, 0) + 1
        last_loss_ts[sk] = ts
        if cnt >= loss_streak_max:
            streak_blocked[sk] = ts + cooldown_candles * _TF_MS
            loss_streak[sk] = 0
        else:
            loss_streak[sk] = cnt
        if global_pause_trigger > 0:
            other_ts = last_loss_ts.get(other_sk, 0)
            if other_ts > 0 and (ts - other_ts) <= global_pause_trigger * _TF_MS:
                pk = f"{sym}:{pname}"
                global_pause_until[pk] = ts + global_pause_candles * _TF_MS
    else:
        loss_streak[sk] = 0


def _blocked(
    sym: str,
    pname: str,
    side: str,
    candle_ts: int,
    streak_blocked: dict,
    global_pause_until: dict,
    loss_streak_max: int,
) -> bool:
    """Mirror of the gate check in _try_place_order."""
    if loss_streak_max <= 0:
        return False
    pk = f"{sym}:{pname}"
    if global_pause_until.get(pk, 0) >= candle_ts:
        return True
    sk = f"{sym}:{pname}:{side}"
    if streak_blocked.get(sk, 0) >= candle_ts:
        return True
    return False


def make_close(sym, pname, side, result):
    return {'symbol': sym, 'preset_name': pname, 'side': side, 'result': result}


class TestLossStreak:
    # ── Basic streak counting ─────────────────────────────────────────────────

    def test_first_loss_does_not_block(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
        assert not _blocked('BTCUSDT', 'p', 'BUY', ts + _TF_MS, sb, gp, 2)

    def test_second_loss_blocks(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
        # blocked for 5 candles after the 2nd loss
        assert _blocked('BTCUSDT', 'p', 'BUY', ts + _TF_MS, sb, gp, 2)
        assert _blocked('BTCUSDT', 'p', 'BUY', ts + 4 * _TF_MS, sb, gp, 2)

    def test_cooldown_expires_after_n_candles(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
        # 5 candles after last loss = block expires
        expire_ts = ts + _TF_MS + 5 * _TF_MS
        assert not _blocked('BTCUSDT', 'p', 'BUY', expire_ts + 1, sb, gp, 2)

    def test_win_resets_streak(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'trail'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
        # Win resets streak; another loss needed before block
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + 2 * _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
        assert not _blocked('BTCUSDT', 'p', 'BUY', ts + 3 * _TF_MS, sb, gp, 2)

    def test_sell_side_not_blocked_by_buy_streak(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        # BUY loses twice → BUY blocked
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 0, 0)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts + _TF_MS, ls, sb, gp, lt, 2, 5, 0, 0)
        # SELL side should still be free
        assert not _blocked('BTCUSDT', 'p', 'SELL', ts + _TF_MS, sb, gp, 2)

    # ── Global pause ──────────────────────────────────────────────────────────

    def test_global_pause_triggers_when_both_sides_lose_close_together(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        # BUY loss, then SELL loss 2 candles later (trigger=3)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 3, 10)
        _update(make_close('BTCUSDT', 'p', 'SELL', 'loss'), ts + 2 * _TF_MS, ls, sb, gp, lt, 2, 5, 3, 10)
        # Both sides should be globally paused for 10 candles
        assert _blocked('BTCUSDT', 'p', 'BUY', ts + 3 * _TF_MS, sb, gp, 2)
        assert _blocked('BTCUSDT', 'p', 'SELL', ts + 3 * _TF_MS, sb, gp, 2)

    def test_global_pause_does_not_trigger_if_sides_too_far_apart(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        # BUY loss, then SELL loss 5 candles later (trigger=3 → too far)
        _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 2, 5, 3, 10)
        _update(make_close('BTCUSDT', 'p', 'SELL', 'loss'), ts + 5 * _TF_MS, ls, sb, gp, lt, 2, 5, 3, 10)
        assert not gp  # no global pause triggered

    def test_zero_loss_streak_max_disables_feature(self):
        ls, sb, gp, lt = {}, {}, {}, {}
        ts = 1_000_000
        for _ in range(10):
            _update(make_close('BTCUSDT', 'p', 'BUY', 'loss'), ts, ls, sb, gp, lt, 0, 5, 0, 0)
            ts += _TF_MS
        assert not _blocked('BTCUSDT', 'p', 'BUY', ts, sb, gp, 0)
