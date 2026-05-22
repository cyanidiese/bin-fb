"""
Backtest runner — replays cached klines over a set of parameter presets
and saves results to:
  data/backtest_{timestamp}.json          (archive copy)
  dashboard/public/backtest_results.json  (live dashboard feed)

Usage:
  python backtest.py
  python backtest.py --klines data/BTCUSDT_15m_test.json
  python backtest.py --klines data/BTCUSDT_15m_test.json --out data/my_results.json

Presets live in config/presets.py.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_settings, load_symbols
from config.presets import ALL_PRESETS, LOCKED_PRESETS
from bot.analyzer import Analyzer
from bot.backtester import Backtester
from bot.data_feed import DataFeed
from bot.exporter import export
from bot.recommendation_engine import RecommendationEngine
from config.risk_config import load_risk_config, _CONFIG_PATH as RISK_CONFIG_PATH

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('backtest')


def run_for_symbol(symbol: str, args) -> None:
    settings = load_settings(symbol)

    if args.klines:
        klines_path = Path(args.klines)
    else:
        suffix = 'test' if settings.trading_mode == 'test' else 'live'
        klines_path = Path('data') / f'{symbol}_{settings.timeframe}_{suffix}.json'

    if not args.no_fetch and not args.klines:
        try:
            # live_klines=True: always fetch from production API regardless of TRADING_MODE.
            # Klines are public data and production is far more stable than testnet.
            # The cache file path is unchanged (mode-appropriate suffix).
            feed = DataFeed(settings, live_klines=True)
            feed.refresh_klines(symbol, settings.timeframe, fetch_count=args.klines_count)
            logger.info(f"[{symbol}] Kline cache refreshed from production API")
        except Exception as e:
            logger.warning(f"[{symbol}] Could not refresh klines: {e} — using existing cache")

    if not klines_path.exists():
        logger.error(f"[{symbol}] Klines file not found: {klines_path}")
        return

    with open(klines_path) as f:
        klines = json.load(f)

    if args.klines_count and len(klines) > args.klines_count:
        klines = klines[-args.klines_count:]
    logger.info(f"[{symbol}] Loaded {len(klines)} klines from {klines_path}")

    # Write strategy page data for the dashboard
    try:
        _engine = RecommendationEngine(settings)
        _analyzer = Analyzer(settings.swing_neighbours, _engine)
        _analyzer.build_from_klines(klines)
        export(
            symbol=symbol,
            timeframe=settings.timeframe,
            mode=settings.trading_mode,
            current_price=float(klines[-1][4]),
            trend=_analyzer.get_trend(),
            klines=klines,
            recommendations=_analyzer.get_recommendations(),
            all_points_history=_analyzer.get_all_points(),
            best_recommendation=_analyzer.get_best_recommendation(),
        )
        logger.info(f"[{symbol}] Strategy results written to dashboard/public/results_{symbol}.json")
    except Exception as _e:
        logger.warning(f"[{symbol}] Failed to write strategy results: {_e}")

    all_presets = ALL_PRESETS
    risk_cfg = load_risk_config(RISK_CONFIG_PATH)
    backtester = Backtester(
        base_settings=settings,
        initial_balance=risk_cfg.get("backtest_initial_balance_usdt", 0.0),
        risk_config_path=RISK_CONFIG_PATH,
    )
    results = backtester.run(klines, all_presets)

    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    preset_dicts = {
        name: {**r.to_dict(), 'settings': all_presets[name]}
        for name, r in results.items()
    }

    code_locked = set(LOCKED_PRESETS.keys())
    extra_locked: list[str] = []
    dashboard_path = Path('dashboard') / 'public' / f'backtest_results_{symbol}.json'
    if dashboard_path.exists():
        try:
            with open(dashboard_path) as f:
                old = json.load(f)
            extra_locked = [
                n for n in old.get('locked_presets', [])
                if n not in code_locked and n in preset_dicts
            ]
        except Exception:
            pass

    output = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'symbol': symbol,
        'timeframe': settings.timeframe,
        'klines_file': str(klines_path),
        'total_klines': len(klines),
        'presets': preset_dicts,
        'locked_presets': list(code_locked) + extra_locked,
    }

    archive_path = Path('data') / f'backtest_{symbol}_{ts}.json'
    archive_path.parent.mkdir(exist_ok=True)
    with open(archive_path, 'w') as f:
        json.dump(output, f, indent=2)
    logger.info(f"[{symbol}] Archive saved to {archive_path}")

    # Keep only the 5 most recent archives for this symbol.
    all_archives = sorted(Path('data').glob(f'backtest_{symbol}_????????T??????.json'))
    for stale in all_archives[:-5]:
        try:
            stale.unlink()
            logger.info(f"[{symbol}] Removed old archive {stale.name}")
        except Exception:
            pass

    if dashboard_path.parent.exists():
        with open(dashboard_path, 'w') as f:
            json.dump(output, f, indent=2)
        logger.info(f"[{symbol}] Dashboard feed updated at {dashboard_path}")

    print(f"\n{'='*20} {symbol} {'='*20}")
    header = (
        f"{'Preset':<25} {'Trades':>6} {'Wins':>5} {'Part':>5} "
        f"{'Trail':>6} {'Loss':>5} {'Win%':>6} {'Profit%':>8} "
        f"{'Pts':>8} {'MaxDD':>6} {'AvgTP%':>7}"
    )
    print(header)
    print('─' * len(header))
    for name, r in results.items():
        print(
            f"{name:<25} {r.total():>6} {r.wins():>5} {r.partials():>5} "
            f"{r.trails():>6} {r.losses():>5} {r.win_rate():>5.1%} "
            f"{r.total_profit_pct():>+8.2f} {r.total_profit_pts():>+8.1f} "
            f"{r.max_consecutive_losses():>6} {r.avg_max_tp_reach_pct():>6.1f}%"
        )


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
    parser.add_argument(
        '--no-fetch',
        action='store_true',
        help='Skip fetching new klines from the API before running.',
    )
    parser.add_argument(
        '--klines-count',
        type=int,
        default=1500,
        help='Number of klines to fetch and use for the backtest (default: 1500).',
    )
    parser.add_argument(
        '--symbols',
        nargs='+',
        metavar='SYMBOL',
        help='Symbols to backtest. Overrides SYMBOLS from .env.',
    )
    parser.add_argument(
        '--mode', choices=['test', 'live'], default=None,
        help="Override TRADING_MODE for this backtest run ('test' uses testnet klines, 'live' uses fapi)",
    )
    args = parser.parse_args()

    import os
    if args.mode:
        os.environ['TRADING_MODE'] = args.mode

    symbols = [s.upper() for s in args.symbols] if args.symbols else load_symbols()
    for symbol in symbols:
        run_for_symbol(symbol, args)


if __name__ == '__main__':
    main()
