import asyncio
import logging
import logging.handlers
import os
from pathlib import Path

from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot import display
from bot import exporter


def setup_logging() -> None:
    Path('logs').mkdir(exist_ok=True)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    general = logging.handlers.RotatingFileHandler(
        'logs/bot.log', maxBytes=10 * 1024 * 1024, backupCount=5
    )
    general.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(general)

    # Separate trades log — signals only, one line per event
    trades_fmt = logging.Formatter('%(asctime)s %(message)s')
    trades_handler = logging.handlers.RotatingFileHandler(
        'logs/trades.log', maxBytes=10 * 1024 * 1024, backupCount=5
    )
    trades_handler.setFormatter(trades_fmt)
    trades_logger = logging.getLogger('trades')
    trades_logger.setLevel(logging.INFO)
    trades_logger.addHandler(trades_handler)
    trades_logger.propagate = False  # keep trades out of bot.log


async def run() -> None:
    logger = logging.getLogger('main')
    trades_logger = logging.getLogger('trades')
    settings = load_settings()
    logger.info(
        f"Bot starting | mode={settings.trading_mode} | "
        f"symbol={settings.symbol} | timeframe={settings.timeframe}"
    )

    feed = DataFeed(settings)
    analyzer = Analyzer(settings.swing_neighbours)

    klines = feed.load_klines(settings.symbol, settings.timeframe, settings.kline_limit)
    analyzer.build_from_klines(klines)
    logger.info("Initial trend state built")
    display.show(settings, analyzer.get_trend(), analyzer.get_current_price(), analyzer.get_recommendations())
    exporter.export(
        settings.symbol, settings.timeframe, settings.trading_mode,
        analyzer.get_current_price(), analyzer.get_trend(),
        analyzer.get_klines(), analyzer.get_recommendations(),
        analyzer.get_all_points(),
    )

    async def on_candle_close(kline: list) -> None:
        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        recs = analyzer.add_candle(kline)
        feed.append_kline(settings.symbol, settings.timeframe, kline, settings.kline_limit)

        candle_close_time = int(kline[6]) // 1000
        display.show(settings, analyzer.get_trend(), analyzer.get_current_price(), recs, candle_close_time)
        exporter.export(
            settings.symbol, settings.timeframe, settings.trading_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs,
            analyzer.get_all_points(),
        )

        if recs:
            for rec in recs:
                logger.info(f"Signal: {rec}")
                trades_logger.info(
                    f"SIGNAL | symbol={settings.symbol} | {rec}"
                )

    _first_tick = True

    def on_price_update(price: float) -> None:
        nonlocal _first_tick
        analyzer.update_price(price)
        if _first_tick:
            logger.info(f"First WebSocket tick received | price={price:.2f}")
            _first_tick = False

    await feed.stream_klines(
        settings.symbol,
        settings.timeframe,
        on_candle_close,
        on_price_update,
    )


if __name__ == '__main__':
    setup_logging()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger('main').info("Bot stopped.")
