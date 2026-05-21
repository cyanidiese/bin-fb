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
    'duplicate_skip_candles': 0,
    'duplicate_skip_pct': 2.0,
    'correction_weight': 0.0,
    'lower_high_sell': False,
    'higher_low_buy': False,
    'min_sl_atr_mult': 0.0,
    'atr_lookback': 20,
}


def build_settings(overrides: dict, symbol: str | None = None) -> Settings:
    p = {**DEFAULTS, **overrides}
    if symbol is None:
        symbol = os.getenv('SYMBOL', 'BTCUSDT').upper()
    timeframe = os.getenv('TIMEFRAME', '15m')
    return Settings(
        trading_mode='test',
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
        duplicate_skip_candles=int(p['duplicate_skip_candles']),
        duplicate_skip_pct=float(p['duplicate_skip_pct']),
        lower_high_sell=bool(p['lower_high_sell']),
        higher_low_buy=bool(p['higher_low_buy']),
        min_sl_atr_mult=float(p['min_sl_atr_mult']),
        atr_lookback=int(p['atr_lookback']),
    )


def find_klines(symbol: str) -> tuple[Path, int | None]:
    """Return (klines_path, total_klines_used_at_backtest_time).
    total_klines is None when there is no reference backtest JSON to read from."""
    results_path = Path(f'dashboard/public/backtest_results_{symbol}.json')
    if results_path.exists():
        try:
            with open(results_path) as f:
                data = json.load(f)
            klines_file = Path(data.get('klines_file', ''))
            total_klines = data.get('total_klines')
            if klines_file.exists():
                return klines_file, total_klines
        except Exception:
            pass

    timeframe = os.getenv('TIMEFRAME', '15m')
    for name in (f'{symbol}_{timeframe}_test.json', f'{symbol}_{timeframe}.json'):
        p = Path('data') / name
        if p.exists():
            return p, None
    raise FileNotFoundError(
        f'No klines file found for {symbol} in data/. '
        'Run backtest.py first to populate the cache.'
    )


def main() -> None:
    overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    symbol = (overrides.pop('symbol', None) or os.getenv('SYMBOL', 'BTCUSDT')).upper()

    klines_path, total_klines = find_klines(symbol)
    with open(klines_path) as f:
        klines = json.load(f)

    # Use the same kline window that produced the reference backtest JSON so
    # that the per-preset numbers shown in the Create page match the static results.
    if total_klines and len(klines) > total_klines:
        klines = klines[-total_klines:]

    settings = build_settings(overrides, symbol=symbol)
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
