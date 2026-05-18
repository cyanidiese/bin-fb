"""
Archived presets — removed 2026-05-18.

Removal criterion: bottom-70 for at least one symbol AND not top-15 for any symbol
across the 15-symbol backtest suite (bottom/top measured by total_profit_pct).

To restore a preset, move its entry back into the PRESETS dict in presets.py.
"""

from config.presets import PresetOverrides

ARCHIVED: dict[str, PresetOverrides] = {
    # ── High-RR filters (consistently underperform across symbols) ────────────
    'best_combo':                  {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.5, 'min_swing_points': 4},
    'high_rr_arm30_trail20':       {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2},
    'high_rr_partial_40':          {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.4},
    'high_rr_partial_50':          {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.5},
    'high_rr_tight_partial_50':    {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'proximity_zone_pct': 5.0, 'partial_take_pct': 0.5},
    'high_rr_trail_20':            {'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.5, 'trailing_stop_pct': 0.2},
    'medium_rr_partial_50':        {'min_profit_loss_ratio': 2.0, 'min_profit_pct': 0.7, 'partial_take_pct': 0.5},
    'very_high_rr_partial_50':     {'min_profit_loss_ratio': 3.0, 'min_profit_pct': 1.5, 'partial_take_pct': 0.5},

    # ── Partial-only variants ─────────────────────────────────────────────────
    'partial_30':                  {'partial_take_pct': 0.3},
    'partial_40':                  {'partial_take_pct': 0.4},
    'partial_40_conservative':     {'partial_take_pct': 0.4, 'min_profit_loss_ratio': 2.0, 'min_swing_points': 4},

    # ── trail_20/30_from_50 ───────────────────────────────────────────────────
    'trail_20_from_50':            {'partial_take_pct': 0.5, 'trailing_stop_pct': 0.2},
    'trail_30_from_50':            {'partial_take_pct': 0.5, 'trailing_stop_pct': 0.3},
    'trail_30_from_30':            {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.3},
    'tp_90pct_trail_20':           {'tp_multiplier': 0.9, 'partial_take_pct': 0.5, 'trailing_stop_pct': 0.2},

    # ── trail_20_from_30 family ───────────────────────────────────────────────
    'trail_20_from_30':            {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2},
    'trail_20_from_30_rr3':        {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'min_profit_loss_ratio': 3.0},
    'trail_20_from_30_rr3_tp95':   {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'min_profit_loss_ratio': 3.0, 'tp_multiplier': 0.95},
    'trail_20_from_30_struct':     {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'min_swing_points': 4},
    'trail_20_from_30_tp95':       {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'tp_multiplier': 0.95},
    'trail_20_from_30_tp95_cooldown': {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'tp_multiplier': 0.95, 'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5, 'global_pause_trigger_candles': 3, 'global_pause_candles': 10},
    'trail_20_from_30_wide':       {'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'proximity_zone_pct': 20.0},
    'sl_adj_arm30_trail20':        {'sl_adjust_to_rr': True, 'min_profit_loss_ratio': 3.0, 'partial_take_pct': 0.3, 'trailing_stop_pct': 0.2, 'tp_multiplier': 0.95},

    # ── Cooldown-only variants ────────────────────────────────────────────────
    'cooldown_2loss':              {'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5},
    'cooldown_3loss':              {'loss_streak_max': 3, 'loss_streak_cooldown_candles': 5},
    'cooldown_global':             {'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5, 'global_pause_trigger_candles': 3, 'global_pause_candles': 10},

    # ── Correction-weight variants ────────────────────────────────────────────
    'correction_w10':              {'correction_weight': 0.1},
    'correction_w20':              {'correction_weight': 0.2},
    'correction_w30':              {'correction_weight': 0.3},
    'correction_w20_high_rr':      {'correction_weight': 0.2, 'min_profit_loss_ratio': 2.5, 'min_profit_pct': 1.0, 'partial_take_pct': 0.3, 'trailing_stop_pct': 0.15},

    # ── Pre-confirmation / higher-low / lower-high variants ───────────────────
    'hl_buy_prox10':               {'higher_low_buy': True},
    'hl_buy_prox15':               {'higher_low_buy': True, 'proximity_zone_pct': 15.0},
    'hl_buy_prox20':               {'higher_low_buy': True, 'proximity_zone_pct': 20.0},
    'hl_buy_prox15_cooldown':      {'higher_low_buy': True, 'proximity_zone_pct': 15.0, 'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5, 'global_pause_trigger_candles': 3, 'global_pause_candles': 10},
    'lh_sell_prox15_cooldown':     {'lower_high_sell': True, 'proximity_zone_pct': 15.0, 'loss_streak_max': 2, 'loss_streak_cooldown_candles': 5, 'global_pause_trigger_candles': 3, 'global_pause_candles': 10},
    'pre_confirm_prox10':          {'lower_high_sell': True, 'higher_low_buy': True},
    'pre_confirm_prox15':          {'lower_high_sell': True, 'higher_low_buy': True, 'proximity_zone_pct': 15.0},
}
