import asyncio
import dataclasses
import json
import logging
import logging.handlers
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backtest import PRESETS, LOCKED_PRESETS
from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot.exporter import export, write_symbols_json
from bot.mode_manager import ModeManager
from bot.notifier import Notifier
from bot.order_executor import OrderExecutor, OrderState
from bot.symbol_registry import SymbolRegistry
from bot.virtual_tracker import VirtualTracker
from bot.risk_manager import RiskManager
from config.risk_config import load_risk_config

_PROJECT_ROOT = Path(__file__).resolve().parent
_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds

_last_balance_poll: float = 0.0
_BALANCE_POLL_INTERVAL = 30.0


def _should_poll_balance() -> bool:
    global _last_balance_poll
    now = time.monotonic()
    if now - _last_balance_poll >= _BALANCE_POLL_INTERVAL:
        _last_balance_poll = now
        return True
    return False


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


async def _heartbeat_loop(mode_manager: ModeManager, started_at: str,
                          symbol_registry: SymbolRegistry) -> None:
    while True:
        active = len(symbol_registry.get_symbols())
        disabled = len(symbol_registry.get_disabled())
        _write_bot_state(True, mode_manager.current_mode, started_at,
                         symbols_active=active, symbols_disabled=disabled)
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

    trades_fmt = logging.Formatter('%(asctime)s %(message)s')
    trades_handler = logging.handlers.RotatingFileHandler(
        'logs/trades.log', maxBytes=10 * 1024 * 1024, backupCount=5
    )
    trades_handler.setFormatter(trades_fmt)
    trades_logger = logging.getLogger('trades')
    trades_logger.setLevel(logging.INFO)
    trades_logger.addHandler(trades_handler)
    trades_logger.propagate = False


async def run() -> None:
    logger = logging.getLogger('main')
    trades_logger = logging.getLogger('trades')
    risk_cfg = load_risk_config()

    # Load symbol registry — source of truth for active symbols
    seed_symbols = [s.strip().upper() for s in os.getenv('SYMBOL', '').split(',') if s.strip()]
    symbol_registry = SymbolRegistry(seed_symbols=seed_symbols)
    symbols = symbol_registry.get_symbols()
    if not symbols:
        logger.error("No active symbols in registry — cannot start")
        sys.exit(1)

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

    # Load settings and build per-symbol state
    sym_settings: dict = {}
    analyzers: dict = {}
    for symbol in symbols:
        s = load_settings(symbol)
        sym_settings[symbol] = s
        engine = RecommendationEngine(s)
        analyzers[symbol] = Analyzer(s.swing_neighbours, engine)

    timeframe = sym_settings[symbols[0]].timeframe
    first_settings = sym_settings[symbols[0]]

    order_executor = OrderExecutor(
        mode=current_mode,
        settings=first_settings,
        risk_manager=risk_manager,
        notifier=notifier,
        symbol_registry=symbol_registry,
    )

    virtual_tracker = VirtualTracker(
        mode=current_mode,
        orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
        efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
    )

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _write_pid()
        _write_bot_state(running=True, mode=current_mode, started_at=started_at,
                         symbols_active=len(symbols))
    except Exception as exc:
        logger.warning(f"Failed to write bot state files: {exc}")

    write_symbols_json(symbols)
    logger.info(
        f"Bot starting | mode={current_mode} | "
        f"symbols={','.join(symbols)} | timeframe={timeframe}"
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

    for sym in symbols:
        bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
        virtual_tracker.seed_from_backtest(sym, bt_path)

    feed = DataFeed(first_settings)
    order_executor._feed = feed

    # Proactive exchange check + leverage brackets
    await order_executor.check_symbols_on_exchange(symbols)
    await order_executor.fetch_leverage_brackets(symbols)

    # Kline bootstrap + initial export
    for symbol in symbols:
        klines = feed.load_klines(symbol, timeframe, limit=1500)
        analyzers[symbol].build_from_klines(klines)
        recs = analyzers[symbol].get_recommendations()
        best = analyzers[symbol].get_best_recommendation()
        export(
            symbol, timeframe, current_mode,
            analyzers[symbol].get_current_price(), analyzers[symbol].get_trend(),
            analyzers[symbol].get_klines(), recs,
            analyzers[symbol].get_all_points(), best,
        )

    await order_executor.reconcile_with_exchange()
    notifier.notify("info", "Startup complete", f"{len(symbols)} symbol(s) active", "main")

    # ── Callbacks ──────────────────────────────────────────────────────── #

    async def _try_place_order(symbol: str, best, settings) -> None:
        preset_name = virtual_tracker.best_preset(symbol)
        all_presets = {**LOCKED_PRESETS, **PRESETS}
        overrides = all_presets.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        balance = risk_manager.get_balance()
        allocation = min(risk_manager.get_allocation(symbol), balance)
        entry = best.getEntryPrice()
        if entry <= 0 or allocation <= 0:
            return

        min_notional = await order_executor.get_min_notional(symbol)
        bracket_max = order_executor.get_bracket_max(symbol)
        target_lev = risk_manager.get_leverage(symbol)

        min_viable_lev = math.ceil(min_notional / allocation)
        if min_viable_lev > bracket_max:
            logger.info(f"[{symbol}] Cannot meet min_notional at any leverage, skipping")
            return

        actual_lev = max(min_viable_lev, min(target_lev, bracket_max))
        quantity = allocation * actual_lev / entry

        allowed, reason = risk_manager.can_open_sync(symbol, allocation)
        if not allowed:
            logger.info(f"[{symbol}] Order skipped: {reason}")
            return
        if quantity <= 0:
            return

        await order_executor.place_order(
            symbol=symbol,
            preset_name=preset_name or 'default',
            side=best.getSide(),
            entry=entry,
            tp=best.getTarget(),
            sl=best.getStop() or 0.0,
            quantity=quantity,
            leverage=actual_lev,
            partial_take_pct=preset_settings.partial_take_pct,
            trailing_stop_pct=preset_settings.trailing_stop_pct,
            level=best.getLevel(),
            signal_type=best.getType().value,
        )

    async def on_candle_close(symbol: str, kline: list) -> None:
        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        if symbol_registry.is_disabled(symbol):
            return

        if symbol not in sym_settings or symbol not in analyzers:
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        recs = analyzer.add_candle(kline)
        best = analyzer.get_best_recommendation()

        try:
            await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 10)
        except Exception as e:
            logger.warning(f"[{symbol}] Kline refresh failed: {e}")

        if _should_poll_balance():
            try:
                balance = await order_executor.fetch_account_balance()
                if balance > 0:
                    risk_manager.update_balance(balance)
            except Exception as exc:
                logger.warning(f"Balance fetch failed: {exc}")

        if best is not None and order_executor.get_state(symbol) == OrderState.IDLE:
            await _try_place_order(symbol, best, settings)

        export(
            symbol, timeframe, mode_manager.current_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best,
        )

        if best:
            trades_logger.info(f"BEST | symbol={symbol} | {best}")
        for rec in recs:
            trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")

    async def on_price_update(symbol: str, price: float) -> None:
        if symbol in analyzers:
            analyzers[symbol].update_price(price)

        closed = await order_executor.check_symbol_price(symbol, price)
        for c in closed:
            virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])

    async def on_switch_mode(target_mode: str) -> None:
        nonlocal virtual_tracker
        current_symbols = symbol_registry.get_symbols()
        await order_executor.close_all_orders_at_market()
        order_executor.reset_for_mode_switch(target_mode)
        risk_manager.reset_for_mode_switch(target_mode)
        settings_new = load_settings(current_symbols[0])
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
        await order_executor.fetch_leverage_brackets(current_symbols)
        for symbol in current_symbols:
            klines_new = await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 1500)
            analyzers[symbol].build_from_klines(klines_new)
        virtual_tracker = VirtualTracker(
            mode=target_mode,
            orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
            efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
        )
        for sym in current_symbols:
            bt_path = _PROJECT_ROOT / "dashboard" / "public" / f"backtest_results_{sym}.json"
            virtual_tracker.seed_from_backtest(sym, bt_path)
        notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

    async def on_stop_bot() -> None:
        await order_executor.close_all_orders_at_market()
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)

    # ── Task setup ─────────────────────────────────────────────────────── #

    _poll_task = asyncio.create_task(
        mode_manager.poll_loop(on_switch_mode=on_switch_mode, on_stop_bot=on_stop_bot)
    )
    _hb_task = asyncio.create_task(
        _heartbeat_loop(mode_manager, started_at, symbol_registry)
    )
    _watchdog_task = asyncio.create_task(
        feed.start_watchdog(
            get_symbols=symbol_registry.get_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    )

    try:
        await feed.stream_combined(
            get_symbols=symbol_registry.get_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    finally:
        for t in [_poll_task, _hb_task, _watchdog_task]:
            t.cancel()
        for t in [_poll_task, _hb_task, _watchdog_task]:
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
