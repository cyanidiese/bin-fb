"""Gate 1 — parent-alignment hard gate (decouples drought-escape from alignment enforcement).

The opposing-parent hard reject must fire for continuation signals when the parent trend
explicitly opposes, under ANY of:
  - ignore_parent_alignment=False  (original behaviour), OR
  - enforce_parent_alignment_hard=True (per-preset override), OR
  - global_enforce_parent_alignment=True (risk_config override)
and must NOT fire when the parent is aligned or undetermined (no drought regression).
"""
import dataclasses
import pytest

import bot.recommendation_engine as re_mod
from bot.recommendation_engine import RecommendationEngine
from bot.recommendation import Recommendation, RecommendationTypes
from config.settings import load_settings

BASE = load_settings()

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


def _make_rec(side, rtype):
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
    s = dataclasses.replace(BASE, ignore_parent_alignment=ignore,
                            enforce_parent_alignment_hard=enforce_hard, signal_direction='both')
    eng = GatedEngine(s, opposing)
    out = eng._score_and_filter([(_make_rec(side, rtype), FakeTrend(), None)])
    return len(out) == 1


# --- opposing parent: gate should BLOCK under each enforcement path ---

def test_blocks_when_alignment_not_ignored(monkeypatch):
    assert not _survives(monkeypatch, ignore=False, enforce_hard=False, g_enforce=False, opposing=True)

def test_blocks_when_preset_enforce_hard(monkeypatch):
    # The core new behaviour: ignore=True would previously let this through.
    assert not _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False, opposing=True)

def test_blocks_when_global_enforce(monkeypatch):
    assert not _survives(monkeypatch, ignore=True, enforce_hard=False, g_enforce=True, opposing=True)

def test_passes_when_ignore_and_no_enforce(monkeypatch):
    # Legacy escape hatch preserved: ignore=True, no enforce → opposing signal passes.
    assert _survives(monkeypatch, ignore=True, enforce_hard=False, g_enforce=False, opposing=True)


# --- aligned / undetermined parent: gate must NOT block (no drought regression) ---

def test_passes_when_parent_not_opposing_even_if_enforced(monkeypatch):
    assert _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False, opposing=False)

def test_sell_blocked_when_enforced_and_opposing(monkeypatch):
    assert not _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False,
                         opposing=True, side='SELL',
                         rtype=RecommendationTypes.LOWERING_ABOVE_LAST_LOW)


# --- non-continuation (reversal/structural) types are exempt from the gate ---

def test_reversal_type_exempt_even_when_enforced(monkeypatch):
    assert _survives(monkeypatch, ignore=True, enforce_hard=True, g_enforce=False,
                     opposing=True, side='BUY',
                     rtype=RecommendationTypes.RISING_ABOVE_SUPPOSED_HIGH)
