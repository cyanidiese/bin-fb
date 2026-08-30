"""Tier-aware ranking and single-rank preset substitution.

The tier bug: get_preset_efficiency returns only the VALUE half of the score, so a
preset with no trading history (tier 0, seeded 0.00) outranked a live-proven one whose
recent value was negative — purely because 0.00 > -2.40. Real trading results must
always outrank a backtest guess. Ranking must use the full (tier, value) key.
"""
import json
from pathlib import Path

import pytest

from bot.virtual_tracker import VirtualTracker


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    # min_trades=8 matches the live config, so <8 trades => tier 0
    t = VirtualTracker(
        mode="test",
        orders_path=tmp_path / "orders.json",
        efficiency_path=tmp_path / "eff.json",
        get_min_trades=lambda _s: 8,
    )
    monkeypatch.setattr("bot.virtual_tracker.load_risk_config",
                        lambda: {"ranking_window_size": 10, "preset_blocklist": []})
    t._efficiency = {"INJUSDT": {
        # live-proven but currently negative
        "proven_negative": {"trade_count": 55, "recent_trades": [-0.24] * 10,
                            "total_winning_usdt": -2.40, "seeded_winning_usdt": 500.0},
        # live-proven and positive
        "proven_positive": {"trade_count": 90, "recent_trades": [1.0] * 10,
                            "total_winning_usdt": 60.26, "seeded_winning_usdt": 0.0},
        # never traded — only a backtest seed
        "untested": {"trade_count": 0, "recent_trades": [],
                     "total_winning_usdt": 0.0, "seeded_winning_usdt": 0.0},
        "untested_great_seed": {"trade_count": 0, "recent_trades": [],
                                "total_winning_usdt": 0.0, "seeded_winning_usdt": 9999.0},
    }}
    return t


# ── the tier bug ───────────────────────────────────────────────────────── #

def test_rank_key_carries_the_tier(tracker):
    assert tracker.get_preset_rank_key("INJUSDT", "proven_negative")[0] == 1
    assert tracker.get_preset_rank_key("INJUSDT", "untested")[0] == 0


def test_proven_negative_outranks_untested_zero(tracker):
    """The exact live case: -2.40 with 55 trades must beat 0.00 with none."""
    ranked = tracker.ranked_presets("INJUSDT")
    assert ranked.index("proven_negative") < ranked.index("untested")


def test_proven_negative_outranks_a_huge_seed(tracker):
    """A backtest guess never beats real history, however large the guess."""
    ranked = tracker.ranked_presets("INJUSDT")
    assert ranked.index("proven_negative") < ranked.index("untested_great_seed")


def test_value_only_ordering_would_have_inverted_it(tracker):
    """Pins why the fix is needed: the old key puts the untested preset first."""
    old = sorted(["proven_negative", "untested"],
                 key=lambda n: tracker.get_preset_efficiency("INJUSDT", n), reverse=True)
    assert old[0] == "untested"                       # the bug
    new = sorted(["proven_negative", "untested"],
                 key=lambda n: tracker.get_preset_rank_key("INJUSDT", n), reverse=True)
    assert new[0] == "proven_negative"                # the fix


def test_best_preset_and_ranked_presets_agree_on_the_winner(tracker):
    assert tracker.ranked_presets("INJUSDT")[0] == tracker.best_preset("INJUSDT")


# ── substitution ───────────────────────────────────────────────────────── #

def test_substitute_skips_the_excluded_best(tracker):
    sub = tracker.substitute_preset("INJUSDT", exclude="proven_positive")
    assert sub != "proven_positive"


def test_substitute_never_returns_an_untested_preset(tracker):
    """Substitution places REAL money — it must never land on a preset with no
    trading history, whatever its backtest seed says."""
    for _ in range(3):
        sub = tracker.substitute_preset("INJUSDT", exclude="proven_positive")
        assert sub not in ("untested", "untested_great_seed")


def test_substitute_requires_a_positive_score(tracker):
    """With the only profitable preset excluded, there is no valid substitute —
    it must return None rather than fall through to the least-bad option."""
    assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") is None


def test_substitute_returns_the_next_profitable_preset(tracker):
    tracker._efficiency["INJUSDT"]["second_good"] = {
        "trade_count": 40, "recent_trades": [0.5] * 10,
        "total_winning_usdt": 30.0, "seeded_winning_usdt": 0.0,
    }
    assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") == "second_good"


def test_substitute_honours_the_blocklist(tracker, monkeypatch):
    tracker._efficiency["INJUSDT"]["second_good"] = {
        "trade_count": 40, "recent_trades": [0.5] * 10,
        "total_winning_usdt": 30.0, "seeded_winning_usdt": 0.0,
    }
    monkeypatch.setattr("bot.virtual_tracker.load_risk_config",
                        lambda: {"ranking_window_size": 10,
                                 "preset_blocklist": ["second_good"]})
    assert tracker.substitute_preset("INJUSDT", exclude="proven_positive") is None


def test_substitute_on_unknown_symbol_is_none(tracker):
    assert tracker.substitute_preset("NOPEUSDT", exclude=None) is None
