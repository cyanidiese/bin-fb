"""
Backtest runner — replays cached klines over a set of parameter presets
and saves results to:
  data/backtest_{timestamp}.json          (archive copy)
  dashboard/public/backtest_results.json  (live dashboard feed)

Usage:
  python backtest.py
  python backtest.py --klines data/BTCUSDT_15m_test.json
  python backtest.py --klines data/BTCUSDT_15m_test.json --out data/my_results.json

Presets are defined in PRESETS below.  Each value is a dict of Settings
field overrides; an empty dict means "use env defaults as-is".
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_settings
from bot.backtester import Backtester

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('backtest')

# ── Presets ───────────────────────────────────────────────────────────────────
# Keys must match Settings field names exactly.
PRESETS: dict = {
    # ── Base ──────────────────────────────────────────────────────────────────
    'default': {},

    # ── Entry zone variants ───────────────────────────────────────────────────
    'tight_entry': {
        'proximity_zone_pct': 5.0,
        'min_profit_loss_ratio': 2.0,
    },
    'medium_entry': {
        'proximity_zone_pct': 12.0,
        'min_profit_loss_ratio': 1.8,
    },
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
    'high_rr': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
    },
    'low_rr': {
        'min_profit_loss_ratio': 1.2,
        'min_profit_pct': 0.3,
    },

    # ── Structure sensitivity ─────────────────────────────────────────────────
    'conservative': {
        'min_profit_loss_ratio': 2.0,
        'min_swing_points': 4,
        'proximity_zone_pct': 8.0,
    },
    'aggressive': {
        'min_profit_loss_ratio': 1.2,
        'min_swing_points': 2,
        'proximity_zone_pct': 20.0,
        'min_profit_pct': 0.3,
    },
    'structure_sensitive': {
        'min_swing_points': 5,
        'swing_neighbours': 3,
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

    # ── New: earlier partial triggers ─────────────────────────────────────────
    # partial_50 was best standalone; test 40% and 30% as baselines
    'partial_40': {
        'partial_take_pct': 0.40,
    },
    'partial_30': {
        'partial_take_pct': 0.30,
    },

    # ── New: high_rr × partial combinations (key gap from round 1) ────────────
    # partial_high_rr used 60%; 50% partial is the sweet spot — test it with high_rr
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

    # ── New: push RR selectivity further ─────────────────────────────────────
    'very_high_rr': {
        'min_profit_loss_ratio': 3.0,
        'min_profit_pct': 1.5,
    },
    'very_high_rr_partial_50': {
        'min_profit_loss_ratio': 3.0,
        'min_profit_pct': 1.5,
        'partial_take_pct': 0.50,
    },

    # ── New: high_rr + tight entry zone (double selectivity filter) ───────────
    'high_rr_tight': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'proximity_zone_pct': 5.0,
    },
    'high_rr_tight_partial_50': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'proximity_zone_pct': 5.0,
        'partial_take_pct': 0.50,
    },

    # ── New: medium RR + partial (fills gap between conservative and high_rr) ─
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

    # ── New: "best combo" — all winning levers from round 1 combined ──────────
    'best_combo': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
        'min_swing_points': 4,
    },

    # ── Trailing stop — baseline (arm at 50%, trail 30% of gained) ────────────
    # Replaces fixed partial retrace with a dynamic trailing stop.
    # Close when price retraces 30% of the distance gained from entry to peak.
    'trail_30_from_50': {
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.30,
    },
    'trail_20_from_50': {
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },
    'trail_40_from_50': {
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.40,
    },

    # ── Trailing stop — arm earlier (30%), tighter trail ─────────────────────
    'trail_20_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
    },
    'trail_30_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.30,
    },

    # ── Old bot arm threshold (15% of TP) — never tested below 30% before ────
    # DB: partial_order_threshold_multiplier = 0.14-0.15 across all 4 layers
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
    'high_rr_trail_30': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.30,
    },
    'high_rr_trail_20': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },

    # ── Trailing stop × medium RR (more trades, dynamic trail) ───────────────
    'medium_rr_trail_30': {
        'min_profit_loss_ratio': 2.0,
        'min_profit_pct': 0.7,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.30,
    },

    # ── Old layer configs (from btcbt/db/trends.db) ───────────────────────────
    # Layer 0/2: RR=4x, entry 20%, arm 15%, trail 20%
    'db_layer_0': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },
    # Layer 1: RR=3x, arm 15%, trail 15% (tighter trail, looser RR)
    'db_layer_1': {
        'min_profit_loss_ratio': 3.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.15,
    },
    # Layer 3: RR=4x, arm 14%, trail 20% (marginal difference from layer 0)
    'db_layer_3': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.14,
        'trailing_stop_pct': 0.20,
    },

    # ── RR=4x standalone (DB used 4x as primary filter, we only tested up to 3x) ─
    'rr_4x': {
        'min_profit_loss_ratio': 4.0,
        'min_profit_pct': 1.0,
    },
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

    # ── Conservative TP multiplier (DB: take_profit_multiplier 0.85–0.95) ─────
    # Reduces TP target to make it easier to hit; trades win size for win rate.
    'tp_95pct': {
        'tp_multiplier': 0.95,
    },
    'tp_90pct': {
        'tp_multiplier': 0.90,
    },
    'tp_85pct': {
        'tp_multiplier': 0.85,
    },
    'tp_90pct_trail_20': {
        'tp_multiplier': 0.90,
        'partial_take_pct': 0.50,
        'trailing_stop_pct': 0.20,
    },
    'tp_90pct_high_rr': {
        'tp_multiplier': 0.90,
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
    },

    # ── SL distance filters (DB: min_loss=15pts, max_loss=30pts) ─────────────
    # Translated to % of entry: old bot ~0.05–0.12% at BTC 25k prices.
    # At current testnet ~85k, equivalent is ~0.15–0.35%. Test wider range.
    'sl_filter_tight': {
        'min_sl_pct': 0.10,
        'max_sl_pct': 0.80,
    },
    'sl_filter_medium': {
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
    },
    'sl_filter_trail': {
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },

    # ── Refined combinations (round 3 analysis) ──────────────────────────────
    # Baseline: trail_20_from_30 is best raw profit (60%, 10T, +589pts, MaxDD=3).
    # sl_adjust_rr_tp95 is best quality ratio (67%, 6T, +486pts, MaxDD=1).
    # Goal: find something between them — 5-8 trades at 65%+.

    # Axis 1: Add RR selectivity to trail_20_from_30 to cut losses
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
    # All selectivity levers combined on arm-30 trail-20
    'trail_20_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
    },

    # Axis 2: sl_adjust at arm 30% instead of 15% (sl_adjust_rr_tp95 uses 15%)
    'sl_adj_arm30_trail20': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
    },

    # Axis 3: Tighter trail (15%) from arm 30%
    'trail_15_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
    },

    # Axis 4: Wider entry zone (20%) — matches db_layer proximity, may pull in more good trades
    'trail_20_from_30_wide': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'proximity_zone_pct': 20.0,
    },

    # Axis 5: Structural quality gate on top of arm-30 trail-20
    'trail_20_from_30_struct': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'min_swing_points': 4,
    },

    # Axis 6: Cooldown on the best-profit preset (may cut MaxDD from 3 to 1)
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # Axis 7: High-RR (2.5, min_profit 1%) + arm-30 trail-20 — untested combination
    'high_rr_arm30_trail20': {
        'min_profit_loss_ratio': 2.5,
        'min_profit_pct': 1.0,
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
    },

    # ── Round 4: best formula = arm-30, trail-15, tp×0.95, cooldown ──────────
    # Findings: trail_15 > trail_20 (tighter = more profit/trade, same win rate)
    #           cooldown cuts MaxDD 3→2 and improves win rate 60%→62.5%
    #           tp×0.95 adds ~35 pts for free on same trade set
    # Explore all combinations of (trail_15, tp×0.95, cooldown)

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
    # Candidate for overall best — all proven improvements stacked
    'trail_15_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
    # tp×0.95 on the cooldown winner
    'trail_20_from_30_tp95_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # ── Candle-based directional cooldown ────────────────────────────────────
    # After N consecutive losses on one side, block that side for M candles.
    # At 15m timeframe: 5 candles ≈ 75 min, 10 candles ≈ 2.5 h.
    'cooldown_2loss': {
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
    },
    'cooldown_3loss': {
        'loss_streak_max': 3,
        'loss_streak_cooldown_candles': 5,
    },
    # Global pause: if BUY and SELL each lose within 3 candles of each other,
    # pause ALL entries for 10 candles (market likely ranging).
    'cooldown_global': {
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },
    # Cooldown applied on top of the best-performing config
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

    # ── Full old-bot clone with all translated settings ───────────────────────
    'db_full_clone': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
    },

    # ── SL tightening to meet RR (sl_adjust_to_rr=True) ──────────────────────
    # Instead of skipping a trade with insufficient R:R, move SL closer so it
    # just meets the ratio. Trades more often but with tighter stops.
    'sl_adjust_rr': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 2.5,
    },
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

    # ── Max TP distance filter (max_profit_pct) ───────────────────────────────
    # Skip overly wide TP targets — they rarely hit and inflate avg TP reach.
    # At BTC ~85k: 3% TP = 2550 pts, 5% = 4250 pts.
    'max_profit_3pct': {
        'max_profit_pct': 3.0,
    },
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
}


def main() -> None:
    parser = argparse.ArgumentParser(description='Backtest the recommendation engine.')
    parser.add_argument(
        '--klines',
        help='Path to a klines JSON cache file. Defaults to data/{SYMBOL}_{TIMEFRAME}_test.json.',
    )
    parser.add_argument(
        '--out',
        help='Output JSON file path. Defaults to data/backtest_{timestamp}.json.',
    )
    args = parser.parse_args()

    settings = load_settings()

    # ── Resolve klines source ─────────────────────────────────────────────────
    if args.klines:
        klines_path = Path(args.klines)
    else:
        suffix = 'test' if settings.trading_mode == 'testnet' else 'live'
        klines_path = Path('data') / f'{settings.symbol}_{settings.timeframe}_{suffix}.json'

    if not klines_path.exists():
        logger.error(f"Klines file not found: {klines_path}")
        logger.error("Run the bot first to populate the cache, or specify --klines <path>.")
        sys.exit(1)

    with open(klines_path) as f:
        klines = json.load(f)
    logger.info(f"Loaded {len(klines)} klines from {klines_path}")

    # ── Run ───────────────────────────────────────────────────────────────────
    backtester = Backtester(settings)
    results = backtester.run(klines, PRESETS)

    # ── Build output payload ──────────────────────────────────────────────────
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    preset_dicts = {}
    for name, r in results.items():
        d = r.to_dict()
        d['settings'] = PRESETS[name]
        preset_dicts[name] = d

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'symbol': settings.symbol,
        'timeframe': settings.timeframe,
        'klines_file': str(klines_path),
        'total_klines': len(klines),
        'presets': preset_dicts,
    }

    # ── Archive copy ──────────────────────────────────────────────────────────
    archive_path = Path(args.out) if args.out else Path('data') / f'backtest_{ts}.json'
    archive_path.parent.mkdir(exist_ok=True)
    with open(archive_path, 'w') as f:
        json.dump(output, f, indent=2)
    logger.info(f"Archive saved to {archive_path}")

    # ── Dashboard live feed ───────────────────────────────────────────────────
    dashboard_path = Path('dashboard') / 'public' / 'backtest_results.json'
    if dashboard_path.parent.exists():
        with open(dashboard_path, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"Dashboard feed updated at {dashboard_path}")
    else:
        logger.warning(f"Dashboard public dir not found — skipping {dashboard_path}")

    # ── Print summary table ───────────────────────────────────────────────────
    print()
    header = f"{'Preset':<25} {'Trades':>6} {'Wins':>5} {'Part':>5} {'Trail':>6} {'Loss':>5} {'Win%':>6} {'Profit%':>8} {'Pts':>8} {'MaxDD':>6} {'AvgTP%':>7}"
    print(header)
    print('─' * len(header))
    for name, r in results.items():
        print(
            f"{name:<25} {r.total():>6} {r.wins():>5} {r.partials():>5} {r.trails():>6} {r.losses():>5} "
            f"{r.win_rate():>5.1%} {r.total_profit_pct():>+8.2f} "
            f"{r.total_profit_pts():>+8.1f} {r.max_consecutive_losses():>6} "
            f"{r.avg_max_tp_reach_pct():>6.1f}%"
        )
    print()


if __name__ == '__main__':
    main()
