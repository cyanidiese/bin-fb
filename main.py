import asyncio
import dataclasses
import json
import logging
import logging.handlers
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
from bot.virtual_order_simulator import VirtualOrderSimulator
from bot.risk_manager import RiskManager
from bot.leverage_scenario import create_scenario
from config.risk_config import load_risk_config
from bot.balance_history import record as bh_record
from bot.decision_log import record as dl_record

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
                     symbols_active: int = 0, symbols_disabled: int = 0,
                     phase: str = 'starting') -> None:
    _BOT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "running": running,
        "phase": phase,
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
                         symbols_active=active, symbols_disabled=disabled,
                         phase='running')
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
        min_interval_s=float(risk_cfg.get("telegram_notify_interval_s", 120)),
    )
    mode_manager = ModeManager(notifier=notifier)
    current_mode = mode_manager.current_mode

    risk_manager = RiskManager(
        mode=current_mode,
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
        project_root=_PROJECT_ROOT,
    )

    virtual_tracker = VirtualTracker(
        mode=current_mode,
        orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{current_mode}.json",
        efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{current_mode}.json",
    )

    def _scenario_data_path(scenario_name: str, mode: str) -> Path:
        if scenario_name == "default":
            return _PROJECT_ROOT / "data" / f"leverage_state_{mode}.json"
        return _PROJECT_ROOT / "data" / f"leverage_state_{scenario_name}_{mode}.json"

    _active_scenario_name: str = risk_cfg.get("scenario", "default")
    scenario = create_scenario(
        name=_active_scenario_name,
        mode=current_mode,
        active_symbols=symbol_registry.get_symbols(),
        data_path=_scenario_data_path(_active_scenario_name, current_mode),
        max_level=risk_cfg.get("max_leverage_level", 5),
    )

    all_presets = {**LOCKED_PRESETS, **PRESETS}

    def _virtual_lev(sym: str) -> int:
        score = virtual_tracker.get_efficiency_score(sym)
        return scenario.get_leverage(
            sym, score,
            risk_cfg.get("base_leverage", 1),
            risk_cfg.get("max_leverage_level", 5),
            125,  # Binance absolute max; virtual sim uses best-case ceiling
        )

    # min_notionals populated later after exchange fetch; default to 5 USDT until then
    min_notionals: dict[str, float] = {sym: 5.0 for sym in symbols}
    virtual_order_simulator = VirtualOrderSimulator(
        mode=current_mode,
        all_presets=all_presets,
        project_root=_PROJECT_ROOT,
        get_leverage=_virtual_lev,
        initial_balance=0.0,
        virtual_tracker=virtual_tracker,
        min_notionals=min_notionals,
        get_allocation=risk_manager.get_symbol_allocation,
        get_scenario=lambda: _active_scenario_name,
    )

    def _push_scenario_info() -> None:
        syms = symbol_registry.get_symbols()
        risk_manager.set_scenario_info(
            name=_active_scenario_name,
            global_level=scenario.get_global_level(),
            per_symbol={s: scenario.get_symbol_level(s) for s in syms},
        )

    _push_scenario_info()

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
        [sys.executable, "backtest.py", "--mode", current_mode],
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

    # Fetch real min_notionals and startup balance
    for sym in symbols:
        min_notionals[sym] = await order_executor.get_min_notional(sym)

    bh_path = _PROJECT_ROOT / 'data' / f'balance_history_{current_mode}.json'
    dl_path = _PROJECT_ROOT / 'data' / f'decision_log_{current_mode}.json'

    startup_balance = await order_executor.fetch_account_balance()
    if startup_balance > 0:
        risk_manager.seed_real_balance(startup_balance)
    bh_record(bh_path, balance=risk_manager.get_balance(), trigger='startup')
    virtual_order_simulator.apply_real_balance_if_fresh(risk_manager.get_balance())

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

    # Mutable container for balance TTL cache (allows mutation inside nested coroutine)
    _balance_cache_inner: list[tuple[float, float]] = [(0.0, 0.0)]
    _BALANCE_TTL = 5.0

    # Daily exchange-info refresh: re-fetch leverage brackets + min notionals every 96 candles
    # (96 × 15 min = 24 h). Counter increments only on the first symbol close per candle so
    # it ticks once per real candle regardless of how many symbols are active.
    _EXCHANGE_REFRESH_CANDLES = 96
    _candle_counter: list[int] = [0]
    _last_refresh_candle_open: list[int] = [0]

    async def _get_fresh_balance() -> float:
        now = time.monotonic()
        cached_val, cached_ts = _balance_cache_inner[0]
        if now - cached_ts < _BALANCE_TTL:
            return cached_val
        try:
            bal = await order_executor.fetch_account_balance()
        except Exception as exc:
            logger.warning(f"Balance fetch failed: {exc}")
            bal = 0.0
        if bal > 0:
            _balance_cache_inner[0] = (bal, now)
            return bal
        return cached_val

    async def _try_place_order(
        symbol: str, best, settings, balance: float, candle_ts: int
    ) -> float:
        preset_name = virtual_tracker.best_preset(symbol)
        overrides = all_presets.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        entry = best.getEntryPrice()
        if entry <= 0:
            return 0.0

        if preset_settings.min_sl_atr_mult > 0 and preset_settings.atr_lookback > 0:
            sl_raw = best.getStop()
            if sl_raw is not None and sl_raw > 0:
                klines_now = analyzers[symbol].get_klines()
                if klines_now:
                    tail = klines_now[-preset_settings.atr_lookback:]
                    avg_range = sum(float(k[2]) - float(k[3]) for k in tail) / len(tail)
                    if avg_range > 0 and abs(sl_raw - entry) < preset_settings.min_sl_atr_mult * avg_range:
                        dl_record(
                            dl_path, candle_ts=candle_ts, symbol=symbol,
                            decision='skip_sl_too_tight',
                            reason=f'sl_dist={abs(sl_raw - entry):.4f} < {preset_settings.min_sl_atr_mult}×avg_range={avg_range:.4f}',
                            balance=balance, leverage=0, efficiency_score=virtual_tracker.get_efficiency_score(symbol),
                            preset_name=preset_name, scenario=_active_scenario_name,
                        )
                        return 0.0

        bracket_max = order_executor.get_bracket_max(symbol)
        max_policy_lev = risk_cfg.get('max_leverage_level', 5)
        base_lev = risk_cfg.get('base_leverage', 1)
        eff_score = virtual_tracker.get_efficiency_score(symbol)
        actual_lev = scenario.get_leverage(symbol, eff_score, base_lev, max_policy_lev, bracket_max)
        if actual_lev <= 0:
            actual_lev = 1

        min_notional = min_notionals.get(symbol)
        if min_notional is None:
            min_notional = await order_executor.get_min_notional(symbol)
            min_notionals[symbol] = min_notional

        margin = min_notional / actual_lev
        eff_score = virtual_tracker.get_efficiency_score(symbol)

        if balance < margin:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_balance',
                reason=f'balance={balance:.2f} < margin={margin:.2f}',
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            logger.info(f"[{symbol}] Insufficient balance: {balance:.2f} < margin={margin:.2f}")
            return 0.0

        allowed, reason = risk_manager.can_open_sync(symbol)
        if not allowed:
            decision = 'skip_hard_stop' if 'hard_stop' in reason else 'skip_profit_factor'
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision=decision, reason=reason,
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            logger.info(f"[{symbol}] Order skipped: {reason}")
            return 0.0

        # If best preset changed since last order, verify exchange has no open position
        if order_executor._last_opened_preset.get(symbol) != preset_name:
            await order_executor.check_symbols_on_exchange([symbol])
            if order_executor.get_state(symbol) != OrderState.IDLE:
                return 0.0

        quantity = (margin * actual_lev) / entry

        bh_record(bh_path, balance=balance, trigger='order_open',
                  symbol=symbol, leverage=actual_lev)

        precision = best.getPrecision() if hasattr(best, 'getPrecision') else 0.0

        placed = await order_executor.place_order(
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
            balance_at_open=balance,
            signal_level=best.getLevel() or 0,
            precision_score=precision or 0.0,
            scenario=_active_scenario_name,
        )
        if placed:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='placed', reason='',
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name, scenario=_active_scenario_name,
                signal_type=best.getType().value,
                level=best.getLevel(),
                precision_score=precision or 0.0,
            )
            return margin
        return 0.0

    async def on_candle_close(symbol: str, kline: list) -> None:
        nonlocal risk_cfg, _active_scenario_name, scenario

        if os.path.exists('STOP'):
            logger.info("STOP file detected — halting.")
            raise SystemExit(0)

        # Signal file: dashboard requested a hard-stop reset
        _reset_signal = _PROJECT_ROOT / "data" / "reset_hard_stop.signal"
        if _reset_signal.exists():
            try:
                _reset_signal.unlink()
                risk_manager.reset_hard_stop()
                logger.info("Hard stop reset via dashboard signal")
            except Exception as _e:
                logger.warning(f"Failed to process reset_hard_stop signal: {_e}")

        # Hot-reload config and switch scenario if changed
        risk_cfg = load_risk_config()
        new_scenario_name = risk_cfg.get("scenario", "default")
        if new_scenario_name != _active_scenario_name:
            prior_global_level = scenario.get_global_level()
            _active_scenario_name = new_scenario_name
            scenario = create_scenario(
                name=new_scenario_name,
                mode=mode_manager.current_mode,
                active_symbols=symbol_registry.get_symbols(),
                data_path=_scenario_data_path(new_scenario_name, mode_manager.current_mode),
                max_level=risk_cfg.get("max_leverage_level", 5),
                inherit_from_level=prior_global_level if new_scenario_name == "allocation" else 0,
            )
            logger.info(f"Scenario switched to: {new_scenario_name}")
            _push_scenario_info()

        # Daily exchange-info refresh — fires once per candle (keyed on candle open time)
        candle_open = int(kline[0]) if kline else 0
        if candle_open and candle_open != _last_refresh_candle_open[0]:
            _last_refresh_candle_open[0] = candle_open
            _candle_counter[0] += 1
            if _candle_counter[0] >= _EXCHANGE_REFRESH_CANDLES:
                _candle_counter[0] = 0
                active_syms = symbol_registry.get_symbols()
                logger.info("Daily exchange-info refresh: fetching leverage brackets and min notionals")
                try:
                    await order_executor.fetch_leverage_brackets(active_syms)
                    for sym in active_syms:
                        min_notionals[sym] = await order_executor.get_min_notional(sym)
                    logger.info("Daily exchange-info refresh complete")
                except Exception as exc:
                    logger.warning(f"Daily exchange-info refresh failed (will retry next cycle): {exc}")

        if symbol_registry.is_disabled(symbol):
            return

        if symbol not in sym_settings or symbol not in analyzers:
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        recs = analyzer.add_candle(kline)
        best_for_this = analyzer.get_best_recommendation()

        try:
            await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 10)
        except Exception as e:
            logger.warning(f"[{symbol}] Kline refresh failed: {e}")

        # Fetch balance once per candle batch (5s TTL shared across all symbols)
        balance = await _get_fresh_balance()
        if balance > 0:
            risk_manager.update_balance(balance)

        # Efficiency-ranked cross-symbol placement loop
        candle_ts = int(kline[0]) if kline else 0
        candidates = []
        for sym in symbol_registry.get_symbols():
            if symbol_registry.is_disabled(sym):
                continue
            if order_executor.get_state(sym) != OrderState.IDLE:
                continue
            best_sym = (
                best_for_this if sym == symbol
                else (analyzers[sym].get_best_recommendation() if sym in analyzers else None)
            )
            if best_sym is None:
                continue
            score = virtual_tracker.get_efficiency_score(sym)
            candidates.append((sym, best_sym, sym_settings.get(sym, settings), score))

        candidates.sort(key=lambda x: x[3], reverse=True)
        if scenario.uses_weight_allocation:
            for sym, best, sym_s, _ in candidates:
                await _try_place_order(sym, best, sym_s, balance, candle_ts)
        else:
            # BestGetsFirst: each symbol gets the remaining deployable pool after prior orders
            deployable = risk_manager.get_deployable_budget()
            deployed = 0.0
            for sym, best, sym_s, _ in candidates:
                used = await _try_place_order(sym, best, sym_s, max(0.0, deployable - deployed), candle_ts)
                deployed += used

        await virtual_order_simulator.on_candle_close(
            symbol=symbol,
            analyzer=analyzer,
            best_preset_name=virtual_tracker.best_preset(symbol),
            base_settings=settings,
        )

        export(
            symbol, timeframe, mode_manager.current_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best_for_this,
        )

        if best_for_this:
            trades_logger.info(f"BEST | symbol={symbol} | {best_for_this}")
        for rec in recs:
            trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")

    async def on_price_update(symbol: str, price: float) -> None:
        if symbol in analyzers:
            analyzers[symbol].update_price(price)

        closed = await order_executor.check_symbol_price(symbol, price)
        for c in closed:
            virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])
            scenario.record_closed(c['symbol'], c.get('leverage', 1))
            _push_scenario_info()
            fresh_bal = await _get_fresh_balance()
            bh_record(
                bh_path, balance=fresh_bal, trigger='order_close',
                symbol=c['symbol'], leverage=c.get('leverage', 1),
                pnl_usdt=c.get('pnl_usdt'),
            )
            notifier.notify_trade_close(
                symbol=c['symbol'],
                side=c.get('side', ''),
                pnl_usdt=c.get('pnl_usdt', 0.0),
                entry_price=c.get('entry_price', 0.0),
                close_price=c.get('close_price', 0.0),
                preset_name=c.get('preset_name', ''),
                balance_after=fresh_bal,
            )

        virtual_closed = await virtual_order_simulator.check_prices(symbol, price)
        for vc in virtual_closed:
            virtual_tracker.record_closed_trade(symbol, vc['preset_name'], vc['pnl_usdt'])

    async def on_switch_mode(target_mode: str) -> None:
        nonlocal virtual_tracker, virtual_order_simulator, scenario
        current_symbols = symbol_registry.get_symbols()
        await virtual_order_simulator.close_all_open(current_symbols, feed)
        await order_executor.close_all_orders_at_market()
        order_executor.reset_for_mode_switch(target_mode)
        risk_manager.reset_for_mode_switch(target_mode)
        settings_new = load_settings(current_symbols[0])
        feed.reinit(target_mode, settings_new.api_key, settings_new.api_secret)
        bt_result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "backtest.py", "--mode", target_mode],
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
        scenario.reset_for_mode(
            target_mode,
            _scenario_data_path(_active_scenario_name, target_mode),
        )
        virtual_order_simulator = VirtualOrderSimulator(
            mode=target_mode,
            all_presets=all_presets,
            project_root=_PROJECT_ROOT,
            get_leverage=_virtual_lev,
            initial_balance=0.0,
            virtual_tracker=virtual_tracker,
            min_notionals=min_notionals,
            get_allocation=risk_manager.get_symbol_allocation,
            get_scenario=lambda: _active_scenario_name,
        )
        switch_balance = await order_executor.fetch_account_balance()
        if switch_balance > 0:
            risk_manager.update_balance(switch_balance)
        virtual_order_simulator.apply_real_balance_if_fresh(risk_manager.get_balance())
        notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

    async def on_stop_bot() -> None:
        current_symbols = symbol_registry.get_symbols()
        await virtual_order_simulator.close_all_open(current_symbols, feed)
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
