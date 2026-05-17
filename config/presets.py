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
    },
    'sl_adjust_rr_tp95': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
}


# ── Experimental presets ───────────────────────────────────────────────────────
# Keys must match Settings field names exactly.
PRESETS: dict[str, PresetOverrides] = {
    # ── Base ──────────────────────────────────────────────────────────────────
    'default': {},

    # ── Entry zone variants ───────────────────────────────────────────────────
    'loose_entry': {
        'proximity_zone_pct': 20.0,
        'min_profit_pct': 0.3,
        'min_profit_loss_ratio': 1.2,
    },
    'broad_zone': {
        'proximity_zone_pct': 30.0,
        'min_profit_loss_ratio': 1.5,
    },

    # ── RR variants ───────────────────────────────────────────────────────────
    'low_rr': {
        'min_profit_loss_ratio': 1.2,
        'min_profit_pct': 0.3,
    },

    # ── Structure sensitivity ─────────────────────────────────────────────────
    'aggressive': {
        'min_profit_loss_ratio': 1.2,
        'min_swing_points': 2,
        'proximity_zone_pct': 20.0,
        'min_profit_pct': 0.3,
    },

    # ── Partial take — standalone ─────────────────────────────────────────────
    'partial_50': {
        'partial_take_pct': 0.50,
    },
    'partial_60': {
        'partial_take_pct': 0.60,
    },
    'partial_70': {
        'partial_take_pct': 0.70,
    },

    # ── Partial take — combined presets ───────────────────────────────────────
    'partial_tight': {
        'partial_take_pct': 0.60,
        'proximity_zone_pct': 5.0,
        'min_profit_loss_ratio': 2.0,
    },
    'partial_high_rr': {
        'partial_take_pct': 0.60,
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
    },
    'partial_conservative': {
        'partial_take_pct': 0.50,
        'min_profit_loss_ratio': 2.0,
        'min_swing_points': 4,
    },

    # ── Earlier partial triggers ──────────────────────────────────────────────
    'partial_40': {
        'partial_take_pct': 0.40,
    },
    'partial_30': {
        'partial_take_pct': 0.30,
    },

    # ── high_rr × partial combinations ───────────────────────────────────────
    'high_rr_partial_50': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
    },
    'high_rr_partial_40': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.40,
    },

    # ── Push RR selectivity further ───────────────────────────────────────────
    'very_high_rr_partial_50': {
        'min_profit_loss_ratio': 3.0,
        'min_profit_pct': 1.5,
        'partial_take_pct': 0.50,
    },

    # ── high_rr + tight entry zone ────────────────────────────────────────────
    'high_rr_tight_partial_50': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'proximity_zone_pct': 5.0,
        'partial_take_pct': 0.50,
    },

    # ── Medium RR + partial ───────────────────────────────────────────────────
    'medium_rr_partial_50': {
        'min_profit_loss_ratio': 2.0,
        'min_profit_pct': 0.7,
        'partial_take_pct': 0.50,
    },
    'partial_40_conservative': {
        'partial_take_pct': 0.40,
        'min_profit_loss_ratio': 2.0,
        'min_swing_points': 4,
    },

    # ── All winning levers from round 1 combined ──────────────────────────────
    'best_combo': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
        'min_swing_points': 4,
    },

    # ── Trailing stop — baseline ──────────────────────────────────────────────
    'trail_30_from_50': {
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.30,
    },
    'trail_20_from_50': {
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },

    # ── Trailing stop — arm earlier (30%) ─────────────────────────────────────
    'trail_20_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
    },
    'trail_30_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.30,
    },

    # ── Old bot arm threshold (15% of TP) ─────────────────────────────────────
    'trail_20_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    'trail_15_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
    },
    'trail_25_from_15': {
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.25,
    },

    # ── Trailing stop × high RR filter ───────────────────────────────────────
    'high_rr_trail_20': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },

    # ── Old layer configs (from btcbt/db/trends.db) ───────────────────────────
    'db_layer_0': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    'db_layer_1': {
        'min_profit_loss_ratio': 3.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
    },
    'db_layer_3': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.14,
        'trailing_stop_pct': 0.20,
    },

    # ── RR=4x standalone ─────────────────────────────────────────────────────
    'rr_4x_trail_20': {
        'min_profit_loss_ratio': 4.0,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    'rr_3x_trail_15': {
        'min_profit_loss_ratio': 3.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
    },

    # ── Conservative TP multiplier ────────────────────────────────────────────
    'tp_90pct_trail_20': {
        'tp_multiplier': 0.90,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },

    # ── SL distance filters ───────────────────────────────────────────────────
    'sl_filter_trail': {
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },

    # ── Round 3: refined combinations ────────────────────────────────────────
    'trail_20_from_30_rr3': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 3.0,
    },
    'trail_20_from_30_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
    },
    'trail_20_from_30_sl_filter': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
    },
    'trail_20_from_30_rr3_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
    },
    'trail_20_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
    },
    'sl_adj_arm30_trail20': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
    },
    'trail_15_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
    },
    'trail_20_from_30_wide': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'proximity_zone_pct': 20.0,
    },
    'trail_20_from_30_struct': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_swing_points': 4,
    },
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
    'high_rr_arm30_trail20': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
    },

    # ── Round 4: best formula = arm-30, trail-15, tp×0.95, cooldown ──────────
    'trail_15_from_30_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'trail_15_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
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
    'trail_20_from_30_tp95_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # ── Candle-based directional cooldown ─────────────────────────────────────
    'cooldown_2loss': {
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'cooldown_3loss': {
        'loss_streak_max': 3,
        'loss_streak_cooldown_candles': 5,
    },
    'cooldown_global': {
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
    },

    # ── Correction quality bonus ──────────────────────────────────────────────
    'correction_w10': {'correction_weight': 0.10},
    'correction_w20': {'correction_weight': 0.20},
    'correction_w30': {'correction_weight': 0.30},
    'correction_w20_trail15_30': {
        'correction_weight': 0.20,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'correction_w20_high_rr': {
        'correction_weight': 0.20,
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
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
    },

    # ── SL tightening to meet RR ──────────────────────────────────────────────
    'sl_adjust_rr_trail': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 2.5,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    'sl_adjust_rr_tp95': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },

    # ── Max TP distance filter ────────────────────────────────────────────────
    'max_profit_2pct': {
        'max_profit_pct': 2.0,
    },
    'max_profit_2pct_trail': {
        'max_profit_pct': 2.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 4.0,
    },
    'max_profit_3pct_trail': {
        'max_profit_pct': 3.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 4.0,
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
    },
    'r5_sl_filter': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
    },
    'r5_sl_adjust': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'sl_adjust_to_rr': True, 'min_profit_loss_ratio': 3.0,
    },
    'r5_tight_rr3': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_profit_loss_ratio': 3.0,
    },
    'r5_tight_sl': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
    },
    'r5_all_filters': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'proximity_zone_pct': 5.0, 'min_profit_loss_ratio': 3.0,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50,
    },
    'r5_trail10': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.10, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },
    'r5_arm25': {
        'partial_take_pct': 0.25, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },
    'r5_arm20': {
        'partial_take_pct': 0.20, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },
    'r5_arm15_cooldown': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },

    # ── Round 6: patch BTC weakness in the best cross-symbol preset ───────────
    # r5_arm15_cooldown bleeds on BTC; BTC's top presets share: rr=4.0, trail=0.20, maxp=3.0
    'r6_arm15_rr4': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 4.0,
    },
    'r6_arm15_maxp3_trail20': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_profit_pct': 3.0,
    },
    'r6_arm15_full': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 4.0, 'max_profit_pct': 3.0,
    },

    # ── Round 7: gap-fill combinations ───────────────────────────────────────
    'r7_arm20_maxp3_trail20': {
        'partial_take_pct': 0.20, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'max_profit_pct': 3.0,
    },
    'r7_trail15_maxp3': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.15,
        'max_profit_pct': 3.0,
    },
    'r7_trail20_maxp3': {
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'max_profit_pct': 3.0,
    },
    'r7_full_clone_cooldown': {
        'min_profit_loss_ratio': 4.0, 'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20, 'tp_multiplier': 0.95,
        'min_sl_pct': 0.05, 'max_sl_pct': 1.50, 'max_profit_pct': 3.0,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },
    'r5_sl_adj_cooldown': {
        'sl_adjust_to_rr': True, 'min_profit_loss_ratio': 3.0, 'tp_multiplier': 0.95,
        'partial_take_pct': 0.15, 'trailing_stop_pct': 0.20,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
    },
    'r5_trail10_rr3': {
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.10, 'tp_multiplier': 0.95,
        'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3, 'global_pause_candles': 10,
        'min_profit_loss_ratio': 3.0,
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
    },

    # ── lower_high_sell: SELL at projected lower high before confirmation ─────
    'lh_sell_trail15': {
        'lower_high_sell': True,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'lh_sell_prox15_trail15': {
        'lower_high_sell': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'lh_sell_prox15_cooldown': {
        'lower_high_sell': True,
        'proximity_zone_pct': 15.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # ── higher_low_buy: BUY at projected higher low before confirmation ───────
    'hl_buy_prox10': {'higher_low_buy': True},
    'hl_buy_prox15': {'higher_low_buy': True, 'proximity_zone_pct': 15.0},
    'hl_buy_prox20': {'higher_low_buy': True, 'proximity_zone_pct': 20.0},
    'hl_buy_trail15': {
        'higher_low_buy': True,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'hl_buy_prox15_trail15': {
        'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },
    'hl_buy_prox15_cooldown': {
        'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # ── Both pre-confirmation flags enabled ───────────────────────────────────
    'pre_confirm_prox10': {'lower_high_sell': True, 'higher_low_buy': True},
    'pre_confirm_prox15': {
        'lower_high_sell': True, 'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
    },
    'pre_confirm_trail15': {
        'lower_high_sell': True, 'higher_low_buy': True,
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
    },
    'pre_confirm_prox15_trail15': {
        'lower_high_sell': True, 'higher_low_buy': True,
        'proximity_zone_pct': 15.0,
        'partial_take_pct': 0.30, 'trailing_stop_pct': 0.15, 'tp_multiplier': 0.95,
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
    },
}

# ── Convenience merge ─────────────────────────────────────────────────────────
# Callers that need the full set should use this rather than re-merging every time.
# LOCKED_PRESETS entries shadow any same-named PRESETS entry (locked always wins).
ALL_PRESETS: dict[str, PresetOverrides] = {**PRESETS, **LOCKED_PRESETS}
