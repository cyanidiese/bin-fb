import asyncio
import dataclasses
import json
import logging
import logging.handlers
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from backtest import PRESETS, LOCKED_PRESETS
from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot import display
from bot.exporter import export, write_symbols_json
from bot.mode_manager import ModeManager
from bot.notifier import Notifier
from bot.order_executor import OrderExecutor, OrderState
from bot.virtual_tracker import VirtualTracker
from bot.risk_manager import RiskManager
from config.risk_config import load_risk_config

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
    risk_cfg = load_risk_config()
    notifier = Notifier(
        log_path=_PROJECT_ROOT / "data" / "system_log.json",
        alert_path=_PROJECT_ROOT / "dashboard" / "public" / "alert_state.json",
        telegram_token=risk_cfg.get("telegram", {}).get("token", ""),
        telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
    )
    mode_manager = ModeManager(notifier=notifier)
    current_mode = mode_manager.current_mode

    risk_manager = RiskManager(
        mode=current_mode,
        initial_balance=risk_cfg.get("test_starting_balance_usdt", 10000.0),
        notifier=notifier,
    )
    order_executor = OrderExecutor(
        mode=current_mode,
        settings=settings,
        risk_manager=risk_manager,
        notifier=notifier,
    )
    virtual_tracker = VirtualTracker(
        mode=current_mode,
        orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
        efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
    )
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _write_pid()
        _write_bot_state(running=True, mode=current_mode, started_at=started_at)
    except Exception as exc:
        logger.warning(f"Failed to write bot state files: {exc}")
    symbols = [settings.symbol]
    write_symbols_json(symbols)
    logger.info(
        f"Bot starting | mode={settings.trading_mode} | "
        f"symbol={settings.symbol} | timeframe={settings.timeframe}"
    )

    # Run obligatory startup backtest
    notifier.notify("info", "Running obligatory backtest", f"mode={current_mode}", "main")
    bt_result = subprocess.run(
        ["python", "backtest.py", "--mode", current_mode],
        capture_output=True,
        cwd=str(_PROJECT_ROOT),
    )
    if bt_result.returncode != 0:
        notifier.notify("emergency", "Obligatory backtest failed — cannot start",
                        bt_result.stderr.decode()[:500], "main")
        sys.exit(1)

    # Seed virtual tracker from backtest results
    for sym in symbols:
        bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
        virtual_tracker.seed_from_backtest(sym, bt_path)

    notifier.notify("info", "Startup sequence complete", f"{len(symbols)} symbol(s) active", "main")
    await order_executor.reconcile_with_exchange()

    feed = DataFeed(settings)
    order_executor._feed = feed

    async def on_switch_mode(target_mode: str) -> None:
        nonlocal virtual_tracker
        await order_executor.close_all_orders_at_market()
        order_executor.reset_for_mode_switch(target_mode)
        settings_new = load_settings()
        feed.reinit(target_mode, settings_new.api_key, settings_new.api_secret)
        bt_result = await asyncio.to_thread(
            subprocess.run,
            ["python", "backtest.py", "--mode", target_mode],
            capture_output=True,
            cwd=str(_PROJECT_ROOT),
        )
        if bt_result.returncode != 0:
            notifier.notify(
                "emergency",
                f"Backtest failed during mode switch to {target_mode}",
                bt_result.stderr.decode()[:500],
                "main",
            )
            return
        virtual_tracker = VirtualTracker(
            mode=target_mode,
            orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
            efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
        )
        for sym in symbols:
            bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
            virtual_tracker.seed_from_backtest(sym, bt_path)
        notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

    async def on_stop_bot() -> None:
        await order_executor.close_all_orders_at_market()
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)

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

        # Check software TP/SL/trailing on all open orders
        high = float(kline[2])
        low = float(kline[3])
        candle_open = float(kline[1])
        candle_close_price = float(kline[4])
        closed_orders = await order_executor.check_all_orders(high, low, candle_open, candle_close_price)
        for c in closed_orders:
            virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])

        # Update risk manager balance from exchange
        try:
            balance = await order_executor.fetch_account_balance()
            if balance > 0:
                risk_manager.update_balance(balance)
        except Exception as exc:
            logger.warning(f"Balance fetch failed: {exc}")

        # Signal → order placement
        if best is not None and order_executor.get_state(settings.symbol) == OrderState.IDLE:
            preset_name = virtual_tracker.best_preset(settings.symbol)
            all_presets = {**LOCKED_PRESETS, **PRESETS}
            overrides = all_presets.get(preset_name or 'default', {})
            preset_settings = dataclasses.replace(settings, **overrides)

            allocation = risk_manager.get_allocation(settings.symbol)
            leverage = risk_manager.get_leverage(settings.symbol)
            entry = best.getEntryPrice()
            quantity = (allocation * leverage / entry) if entry > 0 else 0.0

            allowed, reason = risk_manager.can_open_sync(settings.symbol, allocation)
            if allowed and quantity > 0:
                await order_executor.place_order(
                    symbol=settings.symbol,
                    preset_name=preset_name or 'default',
                    side=best.getSide(),
                    entry=entry,
                    tp=best.getTarget(),
                    sl=best.getStop() or 0.0,
                    quantity=quantity,
                    leverage=leverage,
                    partial_take_pct=preset_settings.partial_take_pct,
                    trailing_stop_pct=preset_settings.trailing_stop_pct,
                    level=best.getLevel(),
                    signal_type=best.getType().value,
                )
            elif not allowed:
                logger.info(f"Order skipped: {reason}")

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

    _poll_task = asyncio.create_task(
        mode_manager.poll_loop(on_switch_mode=on_switch_mode, on_stop_bot=on_stop_bot)
    )
    _hb_task = asyncio.create_task(_heartbeat_loop(current_mode, started_at))
    try:
        await feed.stream_klines(
            settings.symbol,
            settings.timeframe,
            on_candle_close,
            on_price_update,
        )
    finally:
        _poll_task.cancel()
        _hb_task.cancel()
        for t in [_poll_task, _hb_task]:
            try:
                await t
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
