import asyncio
import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone
from pathlib import Path

from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot import display
from bot.exporter import export, write_symbols_json

_PROJECT_ROOT = Path(__file__).resolve().parent
_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds


def _write_pid() -> None:
    _BOT_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_PID_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}))
    tmp.replace(_BOT_PID_PATH)


def _write_bot_state(running: bool, mode: str, started_at: str,
                     symbols_active: int = 0, symbols_disabled: int = 0) -> None:
    _BOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "running": running,
        "pid": os.getpid(),
        "mode": mode,
        "started_at": started_at,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "symbols_active": symbols_active,
        "symbols_disabled": symbols_disabled,
    }))
    tmp.replace(_BOT_STATE_PATH)


async def _heartbeat_loop(mode: str, started_at: str) -> None:
    while True:
        _write_bot_state(True, mode, started_at)
        await asyncio.sleep(_HEARTBEAT_INTERVAL)


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
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _write_pid()
        _write_bot_state(running=True, mode=settings.trading_mode, started_at=started_at)
    except Exception as exc:
        logger.warning(f"Failed to write bot state files: {exc}")
    write_symbols_json([settings.symbol])
    logger.info(
        f"Bot starting | mode={settings.trading_mode} | "
        f"symbol={settings.symbol} | timeframe={settings.timeframe}"
    )

    feed = DataFeed(settings)
    engine = RecommendationEngine(settings)
    analyzer = Analyzer(settings.swing_neighbours, engine)

    klines = feed.load_klines(settings.symbol, settings.timeframe, settings.kline_limit)
    analyzer.build_from_klines(klines)
    logger.info("Initial trend state built")

    recs = analyzer.get_recommendations()
    best = analyzer.get_best_recommendation()
    display.show(settings, analyzer.get_trend(), analyzer.get_current_price(), recs)
    export(
        settings.symbol, settings.timeframe, settings.trading_mode,
        analyzer.get_current_price(), analyzer.get_trend(),
        analyzer.get_klines(), recs,
        analyzer.get_all_points(),
        best,
    )

    async def on_candle_close(kline: list) -> None:
        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        recs = analyzer.add_candle(kline)
        try:
            feed.refresh_klines(settings.symbol, settings.timeframe, fetch_count=10)
        except Exception as e:
            logger.warning(f"Kline refresh failed — cache not updated: {e}")
        best = analyzer.get_best_recommendation()

        candle_close_time = int(kline[6]) // 1000
        display.show(settings, analyzer.get_trend(), analyzer.get_current_price(), recs, candle_close_time)
        export(
            settings.symbol, settings.timeframe, settings.trading_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs,
            analyzer.get_all_points(),
            best,
        )

        if best is not None:
            logger.info(f"Best signal: {best}")
            trades_logger.info(f"BEST | symbol={settings.symbol} | {best}")

        if recs:
            for rec in recs:
                trades_logger.info(f"CANDIDATE | symbol={settings.symbol} | {rec}")

    _first_tick = True

    def on_price_update(price: float) -> None:
        nonlocal _first_tick
        analyzer.update_price(price)
        if _first_tick:
            logger.info(f"First WebSocket tick received | price={price:.2f}")
            _first_tick = False

    _hb_task = asyncio.create_task(_heartbeat_loop(settings.trading_mode, started_at))
    try:
        await feed.stream_klines(
            settings.symbol,
            settings.timeframe,
            on_candle_close,
            on_price_update,
        )
    finally:
        _hb_task.cancel()
        try:
            await _hb_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    setup_logging()
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger('main').info("Bot stopped.")
    finally:
        try:
            state_text = _BOT_STATE_PATH.read_text() if _BOT_STATE_PATH.exists() else '{}'
            state = json.loads(state_text)
            _write_bot_state(
                running=False,
                mode=state.get('mode', 'test'),
                started_at=state.get('started_at', ''),
            )
        except Exception as exc:
            logging.getLogger('main').warning(f"Failed to write shutdown state: {exc}")
