"""
Preset library: named parameter overrides applied on top of the base Settings.

Each preset is a sparse dict of Settings field overrides — only the fields that
deviate from the env-configured defaults need to be present.  This means a preset
is intentionally a *delta*, not a full configuration.

At runtime the preset is applied with:
    preset_settings = dataclasses.replace(base_settings, **overrides)

PresetOverrides is a TypedDict(total=False) that documents the settable fields
and provides IDE autocomplete / type-checking.  TypedDict instances are plain
dicts at runtime, so there is no performance overhead.
"""

from __future__ import annotations

from typing import TypedDict


class PresetOverrides(TypedDict, total=False):
    # Entry selectivity
    proximity_zone_pct: float
    min_profit_pct: float
    min_profit_loss_ratio: float
    min_swing_points: int
    # Exit mechanics
    partial_take_pct: float
    trailing_stop_pct: float
    tp_multiplier: float
    # SL distance filters (0.0 = disabled)
    min_sl_pct: float
    max_sl_pct: float
    sl_adjust_to_rr: bool
    # TP distance cap (0.0 = disabled)
    max_profit_pct: float
    # Correction quality bonus (0.0 = disabled)
    correction_weight: float
    # Candle-based cooldown (0 = disabled)
    loss_streak_max: int
    loss_streak_cooldown_candles: int
    global_pause_trigger_candles: int
    global_pause_candles: int
    # Pre-confirmation entry flags
    lower_high_sell: bool
    higher_low_buy: bool
    # Duplicate-signal skip (0 = disabled)
    duplicate_skip_candles: int
    duplicate_skip_pct: float
    # Early loss exit (0 = disabled)
    max_losing_pct: float
    max_losing_amount_usdt: float
    max_losing_candles: int
    # Trailing stop activation gate (0.0 = disabled)
    trail_activation_pct: float
    # Trailing stop minimum distance as % of entry (0.0 = disabled)
    trail_min_distance_pct: float
    # Range position gate for continuation signals (1.0 = disabled)
    range_position_max: float
    # Minimum swing points (highs and lows each) for projection (default 3 = current behavior)
    min_swing_points_projection: int
    # When True: allow continuation signals even when parent trend opposes (default False)
    ignore_parent_alignment: bool
    # When True: re-enable the opposing-parent hard reject even if ignore_parent_alignment
    # is True (blocks continuation signals into a DEFINED opposing bigger trend, while still
    # allowing thin/undetermined parents so post-BoS droughts are not reintroduced). Default False.
    enforce_parent_alignment_hard: bool


# ── Locked presets ─────────────────────────────────────────────────────────────
# Top-performing presets that must never be modified or deleted.
# They are included in every backtest run and enforced read-only in the
# dashboard API. Add entries here to permanently preserve a preset.
LOCKED_PRESETS: dict[str, PresetOverrides] = {
    'trail_15_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
    'trail_15_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'range_position_max': 0.50,
    },
    'sl_adjust_rr_tp95': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'range_position_max': 0.65,
    },
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'range_position_max': 0.50,
    },
}


# ── Experimental presets ───────────────────────────────────────────────────────
# Keys must match Settings field names exactly.
PRESETS: dict[str, PresetOverrides] = {
    # ── Base ──────────────────────────────────────────────────────────────────
    'default': {
        'max_losing_pct': 25.0,
        'range_position_max': 0.30,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Entry zone variants ───────────────────────────────────────────────────
    'loose_entry': {
        'proximity_zone_pct': 20.0,
        'min_profit_pct': 0.3,
        'min_profit_loss_ratio': 1.2,
        'max_losing_pct': 25.0,
        'range_position_max': 0.30,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'broad_zone': {
        'proximity_zone_pct': 30.0,
        'min_profit_loss_ratio': 1.5,
        'max_losing_pct': 25.0,
        'range_position_max': 0.30,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── RR variants ───────────────────────────────────────────────────────────
    'low_rr': {
        'min_profit_loss_ratio': 1.2,
        'min_profit_pct': 0.3,
        'max_losing_pct': 25.0,
        'range_position_max': 0.30,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Structure sensitivity ─────────────────────────────────────────────────
    'aggressive': {
        'min_profit_loss_ratio': 1.2,
        'min_swing_points': 2,
        'proximity_zone_pct': 20.0,
        'min_profit_pct': 0.3,
        'max_losing_pct': 25.0,
        'range_position_max': 0.30,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Partial take — standalone ─────────────────────────────────────────────
    'partial_50': {
        'partial_take_pct': 0.50,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'partial_60': {
        'partial_take_pct': 0.60,
        'max_losing_pct': 70.0,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'partial_70': {
        'partial_take_pct': 0.70,
        'max_losing_pct': 70.0,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Partial take — combined presets ───────────────────────────────────────
    'partial_tight': {
        'partial_take_pct': 0.60,
        'proximity_zone_pct': 5.0,
        'min_profit_loss_ratio': 2.0,
        'max_losing_pct': 70.0,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'partial_high_rr': {
        'partial_take_pct': 0.60,
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'range_position_max': 0.65,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'partial_conservative': {
        'partial_take_pct': 0.50,
        'min_profit_loss_ratio': 2.0,
        'min_swing_points': 4,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Old bot arm threshold (15% of TP) ─────────────────────────────────────
    'trail_20_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'max_losing_pct': 25.0,
        'max_losing_candles': 2,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
        'duplicate_skip_candles': 4,
        'duplicate_skip_pct': 3.0,
        'max_losing_pct': 70.0,
        'max_losing_candles': 5,
        'range_position_max': 0.80,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    # Same as trail_15_from_15 but skip window reduced to 1 candle.
    # Used for symbols (e.g. DOGEUSDT) where the 3-candle skip blocks ~48% of signals.
    'trail_15_from_15_d1': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
        'duplicate_skip_candles': 1,
        'duplicate_skip_pct': 3.0,
        'max_losing_pct': 70.0,
        'max_losing_candles': 5,
        'range_position_max': 0.80,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_25_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.25,
        'max_losing_pct': 40.0,
        'range_position_max': 0.80,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Old layer configs (from btcbt/db/trends.db) ───────────────────────────
    'db_layer_0': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'db_layer_1': {
        'min_profit_loss_ratio': 3.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'db_layer_3': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.14,
        'trailing_stop_pct': 0.20,
        'max_losing_pct': 40.0,
        'max_losing_candles': 3,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── RR=4x standalone ─────────────────────────────────────────────────────
    'rr_4x_trail_20': {
        'min_profit_loss_ratio': 4.0,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'rr_3x_trail_15': {
        'min_profit_loss_ratio': 3.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── SL distance filters ───────────────────────────────────────────────────
    'sl_filter_trail': {
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'max_losing_pct': 40.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    'trail_20_from_30_sl_filter': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_20_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'range_position_max': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0,
        'max_losing_candles': 5,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'range_position_max': 0.50,
    },
    # ── Round 4: best formula = arm-30, trail-15, tp×0.95, cooldown ──────────
    'trail_15_from_30_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'range_position_max': 0.50,
    },
    'trail_15_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
    'db_clone_cooldown': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'range_position_max': 0.65,
    },

    'correction_w20_trail15_30': {
        'correction_weight': 0.20,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
    },
    # ── Full old-bot clone ────────────────────────────────────────────────────
    'db_full_clone': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'max_losing_candles': 3,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── SL tightening to meet RR ──────────────────────────────────────────────
    'sl_adjust_rr_trail': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 2.5,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 3.0,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'sl_adjust_rr_tp95': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'range_position_max': 0.65,
    },

    # ── Max TP distance filter ────────────────────────────────────────────────
    'max_profit_2pct': {
        'max_profit_pct': 2.0,
        'max_losing_pct': 40.0,
        'max_losing_candles': 2,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'max_profit_2pct_trail': {
        'max_profit_pct': 2.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 4.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'max_profit_3pct_trail': {
        'max_profit_pct': 3.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 4.0,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── Round 5: systematic exploration on proven best base ───────────────────
    # Base = trail_15_from_30_full: arm=30%, trail=15%, tp×0.95, cooldown(2/5/3/10)
    'r5_tight': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0,
    },
    'r5_rr3': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 3.0,
        'range_position_max': 0.50,
    },
    'r5_sl_filter': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
        'range_position_max': 0.50,
    },
    'r5_sl_adjust': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'sl_adjust_to_rr': True, 'min_profit_loss_ratio': 3.0,
        'range_position_max': 0.50,
    },
    'r5_tight_rr3': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_profit_loss_ratio': 3.0,
        'range_position_max': 0.50,
    },
    'r5_tight_sl': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
        'range_position_max': 0.50,
    },
    'r5_all_filters': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_profit_loss_ratio': 3.0,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
        'range_position_max': 0.50,
    },
    'r5_trail10': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.10, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_losing_pct': 40.0,
        'range_position_max': 0.80,
    },
    'r5_arm25': {
        'partial_take_pct': 0.25, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 2.0,
        'max_losing_pct': 55.0,
        'range_position_max': 0.80,
    },
    'r5_arm20': {
        'partial_take_pct': 0.20, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 2.0,
        'max_losing_pct': 70.0,
        'max_losing_candles': 5,
        'range_position_max': 0.80,
    },
    'r5_arm15_cooldown': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 2.0,
        'max_losing_pct': 70.0,
        'max_losing_candles': 5,
    },

    # ── Round 6: patch BTC weakness in the best cross-symbol preset ───────────
    # r5_arm15_cooldown bleeds on BTC; BTC's top presets share: rr=4.0, trail=0.20, maxp=3.0
    'r6_arm15_rr4': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 4.0,
        'duplicate_skip_candles': 10, 'duplicate_skip_pct': 1.0,
    },
    'r6_arm15_maxp3_trail20': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_profit_pct': 3.0,
        'duplicate_skip_candles': 10, 'duplicate_skip_pct': 1.0,
        'range_position_max': 0.80,
    },
    'r6_arm15_full': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 4.0, 'max_profit_pct': 3.0,
        'max_losing_pct': 70.0,
        'range_position_max': 0.10,
    },

    # ── Round 7: gap-fill combinations ───────────────────────────────────────
    'r7_arm20_maxp3_trail20': {
        'partial_take_pct': 0.20, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_profit_pct': 3.0,
        'duplicate_skip_candles': 10, 'duplicate_skip_pct': 1.0,
        'range_position_max': 0.80,
    },
    'r7_trail15_maxp3': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15,
        'max_profit_pct': 3.0,
        'range_position_max': 0.80,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'r7_trail20_maxp3': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'max_profit_pct': 3.0,
        'range_position_max': 0.80,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'r7_full_clone_cooldown': {
        'min_profit_loss_ratio': 4.0, 'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50, 'max_profit_pct': 3.0,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_losing_pct': 70.0,
        'range_position_max': 0.10,
    },
    'r5_sl_adj_cooldown': {
        'sl_adjust_to_rr': True, 'min_profit_loss_ratio': 3.0, 'tp_multiplier': 0.95,
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 2.0,
        'range_position_max': 0.65,
    },
    'r5_trail10_rr3': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.10, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 3.0,
        'range_position_max': 0.50,
    },

    # ── Combined: all new levers + db_full_clone base ─────────────────────────
    'full_clone_max_tp': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'max_profit_pct': 3.0,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'full_clone_sl_adjust': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'sl_adjust_to_rr': True,
        'max_losing_pct': 25.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── lower_high_sell: SELL at projected lower high before confirmation ─────
    'lh_sell_trail15': {
        'lower_high_sell': True,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.40,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'lh_sell_prox15_trail15': {
        'lower_high_sell': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.40,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'hl_buy_trail15': {
        'higher_low_buy': True,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.50,
        'max_losing_pct': 70.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
    },
    'hl_buy_prox15_trail15': {
        'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
    },
    'pre_confirm_trail15': {
        'lower_high_sell': True, 'higher_low_buy': True,
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'min_sl_pct': 0.40,
        'max_losing_pct': 70.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
    },
    'pre_confirm_prox15_trail15': {
        'lower_high_sell': True, 'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
    },

    # ── Oscillating-zone presets: designed for symbols that oscillate after a peak ──
    # oscillating_zone unlocks all four blockers that silence signals in a post-peak
    # descending L2 / pruned L1 state:
    #   1. ignore_parent_alignment=True   — allows BUY continuations when L2 is DESCENDING
    #   2. min_swing_points_projection=2  — allows projection with only 2 L1 swings per side
    #   3. min_profit_pct=0.2             — accepts low-amplitude projections from pruned history
    #   4. range_position_max=1.0         — disables the range gate (distorted by pruning)
    # WARNING: min_profit_pct=0.2 is fee-sensitive at live trading; verify broker fees.
    'oscillating_zone': {
        'ignore_parent_alignment': True,
        'min_swing_points_projection': 2,
        'min_profit_pct': 0.2,
        'range_position_max': 1.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.40,
        'duplicate_skip_candles': 2,
        'duplicate_skip_pct': 3.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    # Same as oscillating_zone but retains the 3-point projection minimum.
    # Use when the symbol has enough L1 history but the parent gate needs removing.
    'oscillating_no_parent_gate': {
        'ignore_parent_alignment': True,
        'range_position_max': 1.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },

    # ── L2 Break-of-Structure entry presets ───────────────────────────────────
    #
    # Strategy: enter at the confirmed L2 swing low/high, not before it.
    #
    # How it works:
    #   - When L1 price crosses the descending BoS level (confirming an L2 low),
    #     `getRecommendation()` at L2 fires RISING_BELOW_LAST_HIGH with:
    #       entry = current price (just above L1 BoS level)
    #       TP    = last confirmed L2 high  (getSupposedNextPoints(min_pts=1) anchors to last_high)
    #       SL    = L1 BoS level            (smaller_trend.getBreakOfStructure())
    #   - min_swing_points=2 allows L2 with just 1 high + 1 low (≥2 total)
    #   - min_swing_points_projection=1 anchors projection to last H/L (avg_diff=0)
    #   - Same logic fires LOWERING_ABOVE_LAST_LOW for the SELL side (descending BoS)
    #
    # l2_bos_entry: ignore parent trend (L3) — trades any L2 BoS regardless of macro.
    # l2_bos_trend: requires L3 to agree — conservative, higher quality but fewer signals.
    'l2_bos_entry': {
        'min_swing_points': 2,
        'min_swing_points_projection': 1,
        'ignore_parent_alignment': True,
        'range_position_max': 1.0,
        'tp_multiplier': 0.90,
        'partial_take_pct': 0.25,
        'trailing_stop_pct': 0.15,
        'trail_activation_pct': 2.0,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.2,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },
    'l2_bos_trend': {
        'min_swing_points': 2,
        'min_swing_points_projection': 1,
        'ignore_parent_alignment': False,
        'range_position_max': 1.0,
        'tp_multiplier': 0.90,
        'partial_take_pct': 0.25,
        'trailing_stop_pct': 0.30,
        'trail_activation_pct': 2.5,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.5,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },

    # ── Directional + regime-aware presets ────────────────────────────────────
    #
    # Problem: in a confirmed L2 downtrend (consecutive lower-highs + lower-lows),
    # BUY signals at each L2 low fire and lose because the bounce to the projected
    # lower-high is smaller than expected or price just keeps falling. Meanwhile
    # SELL signals at each L2 high would profit. The current presets trade both
    # sides equally, so losses cancel gains.
    #
    # Solution A — direction-specific presets (l2_trend_sell / l2_trend_buy):
    #   The virtual tracker backtests both. In a downtrend it learns l2_trend_sell
    #   outperforms and selects it. Direction filter is hard: only SELL (or only BUY)
    #   signals ever reach scoring.
    #
    # Solution B — regime-aware preset (l2_regime_aware / l2_regime_aware_strict):
    #   Each candle the engine checks if the generating trend has N consecutive
    #   lower-highs AND lower-lows. If yes → blocks BUY signals that candle.
    #   Same logic for ascending → blocks SELL. Falls back to both sides in neutral
    #   markets (oscillating). This is the "pattern recognition + auto-switch" the
    #   user requested.
    #
    # lower_high_sell=True / higher_low_buy=True: also capture pre-confirmation
    # signals (DESCENDING_NEAR_LOWER_HIGH / ASCENDING_NEAR_HIGHER_LOW) that fire
    # when price approaches the projected next swing point — earlier entry, same
    # trend direction. Useful in fast-moving trend legs.

    'l2_trend_sell': {
        'signal_direction': 'sell',
        'min_swing_points': 3,
        'min_swing_points_projection': 2,
        'ignore_parent_alignment': True,
        'lower_high_sell': True,
        'trailing_stop_pct': 0.30,
        'trail_activation_pct': 2.5,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.2,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },
    'l2_trend_buy': {
        'signal_direction': 'buy',
        'min_swing_points': 3,
        'min_swing_points_projection': 2,
        'ignore_parent_alignment': True,
        'higher_low_buy': True,
        'trailing_stop_pct': 0.30,
        'trail_activation_pct': 2.5,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.2,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },
    # Regime-aware: no hard direction lock — checks 3 consecutive L2 H/L structure
    # each candle and dynamically blocks the contra-trend side.
    # ignore_parent_alignment=True so it trades descending trends even when L3 was
    # ascending before the regime shift (e.g. early stage of a new downtrend).
    # max_losing_candles=96 (24h @ 15m): safety net added 2026-07-13 after a real
    # TIAUSDT SELL sat open 13 days with a TP ~32% away and no cap. Real trade
    # history for this preset family shows every winner closed within 16.3h and
    # every loss within 3.4h, so 24h can only stop a genuinely stuck position.
    'l2_regime_aware': {
        'trend_regime_filter': True,
        'trend_regime_lookback': 3,
        'min_swing_points': 3,
        'min_swing_points_projection': 2,
        'ignore_parent_alignment': True,
        'lower_high_sell': True,
        'higher_low_buy': True,
        'trailing_stop_pct': 0.30,
        'trail_activation_pct': 2.5,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.2,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },
    # Strict variant: requires L3 agreement in addition to regime filter.
    # Fewer signals but higher expected precision — good for symbols with
    # a stable macro trend (L3 stays aligned for long stretches).
    'l2_regime_aware_strict': {
        'trend_regime_filter': True,
        'trend_regime_lookback': 3,
        'min_swing_points': 3,
        'min_swing_points_projection': 2,
        'ignore_parent_alignment': False,
        'lower_high_sell': True,
        'higher_low_buy': True,
        'trailing_stop_pct': 0.30,
        'trail_activation_pct': 2.5,
        'trail_min_distance_pct': 1.0,
        'min_profit_loss_ratio': 1.5,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'duplicate_skip_candles': 3,
        'duplicate_skip_pct': 2.0,
        'max_losing_candles': 96,
    },

    # ── Round 8: targeted gap-fill additions ──────────────────────────────────

    # BTCUSDT: filter out micro-SL BUY entries (SL ≤ 0.3% keeps getting clipped).
    # Shares the cross-symbol arm15 base; adds min_sl_pct=0.30 to skip those entries
    # and max_sl_pct=1.50 to avoid outlier wide stops.
    'r8_btc_minsl_strict': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'max_profit_pct': 3.0,
        'min_sl_pct': 0.30,
        'max_sl_pct': 1.50,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'max_losing_pct': 25.0,
        'max_losing_candles': 2,
        'range_position_max': 0.10,
    },

    # SOLUSDT: hl_buy_prox15_trail15 is SOL's best BUY preset but has MaxDD≈6%.
    # Adding cooldown should cut drawdown to 3–4% with neutral profit impact.
    'r8_sol_hlbuy_cooldown': {
        'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
        'max_losing_pct': 55.0,
        'max_losing_candles': 2,
        'range_position_max': 0.80,
    },

    # ── Trail activation / min-distance variants ──────────────────────────────
    # Base: trail_15_from_30 (arm=30%, trail=15%, max_losing_pct=25, max_losing_candles=5)
    'trail_15_from_30_act2_min1': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0, 'max_losing_candles': 5,
        'trail_activation_pct': 2.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_30_act3_min1': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0, 'max_losing_candles': 5,
        'trail_activation_pct': 3.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_30_act5_min15': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15,
        'max_losing_pct': 25.0, 'max_losing_candles': 5,
        'trail_activation_pct': 5.0, 'trail_min_distance_pct': 1.5,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },

    # Base: trail_20_from_30_cooldown (arm=30%, trail=20%, cooldown 2/5/3/10)
    'trail_20_from_30_act2_min1': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.20,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'trail_activation_pct': 2.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
    },
    'trail_20_from_30_act3_min1': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.20,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'trail_activation_pct': 3.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
    },
    'trail_20_from_30_act5_min15': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.20,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'trail_activation_pct': 5.0, 'trail_min_distance_pct': 1.5,
        'range_position_max': 0.10,
    },

    # Base: trail_15_from_15 (arm=15%, trail=15%, dup-skip 3/3, max_losing_pct=70, max_losing_candles=5)
    'trail_15_from_15_act2_min1': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 3.0,
        'max_losing_pct': 70.0, 'max_losing_candles': 5,
        'trail_activation_pct': 2.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_15_act3_min1': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 3.0,
        'max_losing_pct': 70.0, 'max_losing_candles': 5,
        'trail_activation_pct': 3.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_15_from_15_act5_min15': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15,
        'duplicate_skip_candles': 3, 'duplicate_skip_pct': 3.0,
        'max_losing_pct': 70.0, 'max_losing_candles': 5,
        'trail_activation_pct': 5.0, 'trail_min_distance_pct': 1.5,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },

    # Base: trail_20_from_15 (arm=15%, trail=20%, max_losing_pct=25, max_losing_candles=2)
    'trail_20_from_15_act2_min1': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'max_losing_pct': 25.0, 'max_losing_candles': 2,
        'trail_activation_pct': 2.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_20_from_15_act3_min1': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'max_losing_pct': 25.0, 'max_losing_candles': 2,
        'trail_activation_pct': 3.0, 'trail_min_distance_pct': 1.0,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
    'trail_20_from_15_act5_min15': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'max_losing_pct': 25.0, 'max_losing_candles': 2,
        'trail_activation_pct': 5.0, 'trail_min_distance_pct': 1.5,
        'range_position_max': 0.10,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
    },
}

# ── Convenience merge ─────────────────────────────────────────────────────────
# Callers that need the full set should use this rather than re-merging every time.
# LOCKED_PRESETS entries shadow any same-named PRESETS entry (locked always wins).
ALL_PRESETS: dict[str, PresetOverrides] = {**PRESETS, **LOCKED_PRESETS}
