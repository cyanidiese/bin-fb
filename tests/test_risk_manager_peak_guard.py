"""Peak-balance poisoning guards (2026-08-18 incident).

A transient totalWalletBalance reading of exactly 5000.0 raised the peak from
3724 to 5000 while the real USDT balance was ~3044. The next reading computed a
39.12% drawdown against that phantom peak and latched the hard stop, freezing
all real trading for 11+ hours. Actual drawdown against the true peak was 18.3%
— below the 20% limit.
"""
import json

import pytest

from bot.risk_manager import RiskManager


def _cfg(tmp_path, **extra):
    cfg = {
        "base_leverage": 2,
        "max_leverage": 10,
        "min_profit_factor": 0.0,
        "drawdown_warning_pct": 10,
        "drawdown_hard_stop_pct": 20,
        "symbol_weights": {},
        "balance_tiers": [
            {"min_balance_usdt": 0, "max_deploy_pct": 80, "max_leverage_ceiling": 10}
        ],
    }
    cfg.update(extra)
    path = tmp_path / "risk_config.json"
    path.write_text(json.dumps(cfg))
    return path


def _rm(tmp_path, **extra):
    return RiskManager(
        "test",
        initial_balance=1000.0,
        config_path=_cfg(tmp_path, **extra),
        state_path=tmp_path / "risk_state.json",
    )


# --------------------------------------------------------------------------- #
# Guard 1 — implausible upward jumps must not become the peak
# --------------------------------------------------------------------------- #

def test_transient_spike_does_not_latch_hard_stop(tmp_path):
    """The exact 2026-08-18 sequence must no longer freeze the bot."""
    rm = _rm(tmp_path)
    rm.seed_real_balance(3724.46)

    # Three consecutive bogus 5000.0 readings, then the true balance returns.
    for _ in range(3):
        rm.update_balance(5000.0)
    rm.update_balance(3043.94)

    snap = rm.snapshot()
    assert snap["hard_stop_active"] is False, "phantom drawdown re-latched the hard stop"
    allowed, reason = rm.can_open_sync("EIGENUSDT")
    assert allowed is True, f"trading still blocked: {reason}"


def test_implausible_reading_leaves_peak_untouched(tmp_path):
    rm = _rm(tmp_path, max_peak_jump_pct=20.0)
    rm.seed_real_balance(1000.0)

    rm.update_balance(5000.0)  # +400% — implausible

    assert rm.snapshot()["peak_balance"] == pytest.approx(1000.0)


def test_plausible_gain_still_raises_peak_fully(tmp_path):
    """A normal winning trade must ratchet the peak exactly as before."""
    rm = _rm(tmp_path, max_peak_jump_pct=20.0)
    rm.seed_real_balance(1000.0)

    rm.update_balance(1150.0)  # +15%, within the guard

    assert rm.snapshot()["peak_balance"] == pytest.approx(1150.0)


def test_repeated_bogus_reading_never_ratchets_peak(tmp_path):
    """The guard must hold even when the bad value repeats many times.

    An earlier per-step-clamp version of this guard failed here: the peak
    ratcheted 3724 -> 4469 -> 5000 across three readings and the stop still
    latched. Each comparison must be made against the same clean peak.
    """
    rm = _rm(tmp_path, max_peak_jump_pct=20.0)
    rm.seed_real_balance(1000.0)

    for _ in range(10):
        rm.update_balance(5000.0)

    assert rm.snapshot()["peak_balance"] == pytest.approx(1000.0)


def test_incremental_growth_ratchets_peak_normally(tmp_path):
    """Ordinary compounding gains still move the peak up step by step."""
    rm = _rm(tmp_path, max_peak_jump_pct=20.0)
    rm.seed_real_balance(1000.0)

    for bal in (1100.0, 1250.0, 1400.0, 1600.0):
        rm.update_balance(bal)

    assert rm.snapshot()["peak_balance"] == pytest.approx(1600.0)


def test_balance_itself_is_never_clamped(tmp_path):
    """Only the peak is guarded; the reported balance stays the live reading."""
    rm = _rm(tmp_path)
    rm.seed_real_balance(1000.0)

    rm.update_balance(5000.0)

    assert rm.get_balance() == pytest.approx(5000.0)


def test_genuine_drawdown_still_fires_hard_stop(tmp_path):
    """The guard must not weaken the real protection."""
    rm = _rm(tmp_path)
    rm.seed_real_balance(1000.0)

    rm.update_balance(750.0)  # -25% against a legitimate peak

    assert rm.snapshot()["hard_stop_active"] is True
    allowed, reason = rm.can_open_sync("EIGENUSDT")
    assert allowed is False
    assert reason == "hard_stop_active"


# --------------------------------------------------------------------------- #
# Guard 2 — reset_hard_stop must re-anchor the peak
# --------------------------------------------------------------------------- #

def test_reset_hard_stop_reanchors_peak(tmp_path):
    rm = _rm(tmp_path)
    rm.seed_real_balance(1000.0)
    rm.update_balance(750.0)
    assert rm.snapshot()["hard_stop_active"] is True

    rm.reset_hard_stop()

    snap = rm.snapshot()
    assert snap["hard_stop_active"] is False
    assert snap["peak_balance"] == pytest.approx(750.0), "stale peak survived the reset"


def test_reset_hard_stop_does_not_relatch_on_next_update(tmp_path):
    """Regression: before the fix the stop re-latched within one candle."""
    rm = _rm(tmp_path)
    rm.seed_real_balance(1000.0)
    rm.update_balance(750.0)
    rm.reset_hard_stop()

    rm.update_balance(750.0)  # next candle, same balance

    assert rm.snapshot()["hard_stop_active"] is False
    assert rm.can_open_sync("EIGENUSDT")[0] is True
