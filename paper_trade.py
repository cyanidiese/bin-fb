"""
Paper trading runner — streams live Binance klines and runs a curated set
of presets with FakeOrder simulation. No real orders are placed.

Usage:
  python paper_trade.py

State is persisted to data/paper_state.json so sessions survive restarts.
Results are exported to dashboard/public/paper_results.json for the dashboard.
"""
import asyncio
import logging
import sys
from pathlib import Path

from bot.data_feed import DataFeed
from bot.paper_trader import PaperTrader
from config.settings import load_settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger('paper_trade')

# ── Presets to run in paper trading ────────────────────────────────────────
# A curated selection from the backtest analysis; edit freely.
# Keys must match Settings field names exactly.
PAPER_PRESETS: dict = {
    'default': {},

    # Best overall: arm-30%, trail-15%, tp×0.95, cooldown
    'trail_15_from_30_full': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # Tighter trail, no cooldown (compare vs full)
    'trail_15_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
    },
    'trail_15_from_30_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.15,
        'tp_multiplier': 0.95,
    },

    # Higher trade count baseline (wider trail)
    'trail_20_from_30': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
    },
    'trail_20_from_30_tp95': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
    },
    'trail_20_from_30_cooldown': {
        'partial_take_pct': 0.30,
        'trailing_stop_pct': 0.20,
        'loss_streak_max': 2,
        'loss_streak_cooldown_candles': 5,
        'global_pause_trigger_candles': 3,
        'global_pause_candles': 10,
    },

    # High quality ratio (SL adjusted to meet RR instead of skipping)
    'sl_adjust_rr_tp95': {
        'sl_adjust_to_rr': True,
        'min_profit_loss_ratio': 3.0,
        'tp_multiplier': 0.95,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
    },

    # Old-bot translated settings (RR=4x, 20% zone, arm-15%, trail-20%)
    'db_full_clone': {
        'min_profit_loss_ratio': 4.0,
        'proximity_zone_pct': 20.0,
        'partial_take_pct': 0.15,
        'trailing_stop_pct': 0.20,
        'tp_multiplier': 0.95,
        'min_sl_pct': 0.05,
        'max_sl_pct': 1.50,
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
}


async def main() -> None:
    settings = load_settings()
    feed = DataFeed(settings)

    logger.info(
        f"Paper trading {settings.symbol} {settings.timeframe} "
        f"— {len(PAPER_PRESETS)} presets"
    )

    logger.info("Loading historical klines...")
    klines = feed.load_klines(settings.symbol, settings.timeframe, settings.kline_limit)

    trader = PaperTrader(
        base_settings=settings,
        presets=PAPER_PRESETS,
        state_path=Path('data/paper_state.json'),
        export_path=Path('dashboard/public/paper_results.json'),
    )
    trader.build_from_klines(klines)

    logger.info("Starting live stream. Press Ctrl+C to stop.")
    try:
        await feed.stream_klines(
            symbol=settings.symbol,
            timeframe=settings.timeframe,
            on_candle_close=trader.on_candle,
            on_price_update=trader.on_price_update,
        )
    except KeyboardInterrupt:
        logger.info("Paper trader stopped.")


if __name__ == '__main__':
    asyncio.run(main())
