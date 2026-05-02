#!/usr/bin/env python3
"""
Single-preset backtest API helper.
Reads settings overrides from argv[1] as a JSON string.
Outputs JSON to stdout: trades + klines for chart rendering.

Called by the Next.js API route:
  python backtest_api.py '{"min_profit_pct": 0.5, ...}'
"""
import json
import logging
import os
import sys
from pathlib import Path

# Keep stdout clean for JSON
logging.disable(logging.CRITICAL)

from dotenv import load_dotenv

load_dotenv()

from config.settings import Settings
from bot.backtester import Backtester

DEFAULTS = {
    'swing_neighbours': 2,
    'min_swing_points': 3,
    'proximity_zone_pct': 10.0,
    'min_profit_pct': 0.5,
    'min_profit_loss_ratio': 1.5,
    'tp_multiplier': 1.0,
    'max_profit_pct': 0.0,
    'min_sl_pct': 0.0,
    'max_sl_pct': 0.0,
    'sl_adjust_to_rr': False,
    'partial_take_pct': 0.0,
    'trailing_stop_pct': 0.0,
    'loss_streak_max': 0,
    'loss_streak_cooldown_candles': 5,
    'global_pause_trigger_candles': 0,
    'global_pause_candles': 10,
    'correction_weight': 0.0,
    'lower_high_sell': False,
    'higher_low_buy': False,
}


def build_settings(overrides: dict) -> Settings:
    p = {**DEFAULTS, **overrides}
    symbol = os.getenv('SYMBOL', 'BTCUSDT').upper()
    timeframe = os.getenv('TIMEFRAME', '15m')
    return Settings(
        trading_mode='testnet',
        api_key='',
        api_secret='',
        symbol=symbol,
        timeframe=timeframe,
        kline_limit=1000,
        kline_cache_limit=5000,
        timezone='UTC',
        precision_similarity_threshold=0.10,
        projection_lookback=4,
        swing_neighbours=int(p['swing_neighbours']),
        min_swing_points=int(p['min_swing_points']),
        proximity_zone_pct=float(p['proximity_zone_pct']),
        min_profit_pct=float(p['min_profit_pct']),
        min_profit_loss_ratio=float(p['min_profit_loss_ratio']),
        tp_multiplier=float(p['tp_multiplier']),
        max_profit_pct=float(p['max_profit_pct']),
        min_sl_pct=float(p['min_sl_pct']),
        max_sl_pct=float(p['max_sl_pct']),
        sl_adjust_to_rr=bool(p['sl_adjust_to_rr']),
        partial_take_pct=float(p['partial_take_pct']),
        trailing_stop_pct=float(p['trailing_stop_pct']),
        correction_weight=float(p['correction_weight']),
        loss_streak_max=int(p['loss_streak_max']),
        loss_streak_cooldown_candles=int(p['loss_streak_cooldown_candles']),
        global_pause_trigger_candles=int(p['global_pause_trigger_candles']),
        global_pause_candles=int(p['global_pause_candles']),
        lower_high_sell=bool(p['lower_high_sell']),
        higher_low_buy=bool(p['higher_low_buy']),
    )


def find_klines() -> Path:
    # Prefer the same klines file that generated backtest_results.json so that
    # API results are directly comparable to the already-loaded dashboard data.
    results_path = Path('dashboard/public/backtest_results.json')
    if results_path.exists():
        try:
            with open(results_path) as f:
                klines_file = Path(json.load(f).get('klines_file', ''))
            if klines_file.exists():
                return klines_file
        except Exception:
            pass

    # Fall back: prefer the file with more history (test > live)
    symbol = os.getenv('SYMBOL', 'BTCUSDT').upper()
    timeframe = os.getenv('TIMEFRAME', '15m')
    for name in (f'{symbol}_{timeframe}_test.json', f'{symbol}_{timeframe}.json'):
        p = Path('data') / name
        if p.exists():
            return p
    raise FileNotFoundError(
        'No klines file found in data/. Run backtest.py first to populate the cache.'
    )


def main() -> None:
    overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}

    klines_path = find_klines()
    with open(klines_path) as f:
        klines = json.load(f)

    settings = build_settings(overrides)
    backtester = Backtester(settings)
    results = backtester.run(klines, {'custom': overrides})
    result = results['custom']

    klines_out = [
        {
            'index': i,
            'time': int(k[0]) // 1000,
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
        }
        for i, k in enumerate(klines)
    ]

    print(json.dumps({**result.to_dict(), 'klines': klines_out}))


if __name__ == '__main__':
    main()
