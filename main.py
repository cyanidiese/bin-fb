import asyncio
import dataclasses
import json
import logging
import logging.handlers
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config.presets import ALL_PRESETS, LOCKED_PRESETS, PRESETS
from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot.exporter import export, write_symbols_json
from bot.mode_manager import ModeManager
from bot.notifier import Notifier
from bot.telegram_menu import TelegramMenu
from bot.order_executor import BotHaltError, OrderExecutor, OrderState
from bot.symbol_registry import SymbolRegistry
from bot.virtual_tracker import VirtualTracker
from bot.virtual_order_simulator import VirtualOrderSimulator
from bot.risk_manager import RiskManager
from bot.leverage_scenario import create_scenario
from config.risk_config import load_risk_config
from bot.balance_history import record as bh_record
from bot.decision_log import record as dl_record
from bot.lot_constraint_detector import adjust_constrained_symbols
from bot.weight_rebalancer import WeightRebalancer

_PROJECT_ROOT = Path(__file__).resolve().parent
_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds
KLINE_REFRESH_EVERY = 4   # refresh once every N candles per symbol
KLINE_STAGGER_SECS = 2    # seconds between each symbol's background refresh task


def _tf_to_ms(timeframe: str) -> int:
    units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000}
    return int(timeframe[:-1]) * units.get(timeframe[-1], 60_000)


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
        emergency_repeat_interval_s=float(risk_cfg.get("emergency_repeat_interval_s", 1800)),
        warning_repeat_interval_s=float(risk_cfg.get("warning_repeat_interval_s", 14400)),
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

    all_presets = ALL_PRESETS

    def _virtual_lev(sym: str) -> int:
        override = symbol_registry.get_leverage_override(sym)
        if override > 0:
            return min(override, 125)
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
        get_allocation=risk_manager.get_allocation_for_balance,
        get_bgf_allocation=risk_manager.get_bgf_allocation_for_balance,
        get_scenario=lambda: _active_scenario_name,
        rank_max=len(all_presets),
        is_rank_disabled=symbol_registry.is_rank_disabled,
    )

    _wr_cfg = risk_cfg.get("weight_rebalancer", {})
    weight_rebalancer = WeightRebalancer(
        symbol_registry=symbol_registry,
        risk_manager=risk_manager,
        settings=first_settings,
        get_klines_fn=lambda sym: analyzers[sym].get_klines() if sym in analyzers else [],
        candle_duration_ms=_tf_to_ms(timeframe),
        mode=current_mode,
        risk_config_path=Path("risk_config.json"),
        data_dir=Path("data"),
        cfg=_wr_cfg,
    )

    _tg_cfg = risk_cfg.get("telegram", {})
    _tg_token = _tg_cfg.get("token", "")
    _tg_owner_id = int(_tg_cfg.get("chat_id", "0") or "0")
    telegram_menu = TelegramMenu(
        token=_tg_token,
        owner_chat_id=_tg_owner_id,
        risk_manager=risk_manager,
        symbol_registry=symbol_registry,
        project_root=_PROJECT_ROOT,
        get_mode=lambda: mode_manager.current_mode,
        get_active_symbols=symbol_registry.get_symbols,
        get_open_orders=order_executor.get_open_orders,
        rank_max=len(all_presets),
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
    _startup_cfg = load_risk_config()
    _bt_klines = int(_startup_cfg.get("backtest_klines", 1500))
    bt_result = subprocess.run(
        [sys.executable, "backtest.py", "--mode", current_mode, "--klines-count", str(_bt_klines)],
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

    virtual_order_simulator.set_lot_cache(order_executor._lot_cache)

    # Pre-warm the lot cache for all symbols via a single exchange-info call
    await order_executor.prefetch_lot_sizes(symbols[0] if symbols else 'BTCUSDT')
    virtual_order_simulator.set_lot_cache(order_executor._lot_cache)  # re-wire after prefetch

    # Fetch real min_notionals and startup balance
    for sym in symbols:
        min_notionals[sym] = await order_executor.get_min_notional(sym)

    bh_path = _PROJECT_ROOT / 'data' / f'balance_history_{current_mode}.json'
    dl_path = _PROJECT_ROOT / 'data' / f'decision_log_{current_mode}.json'

    startup_balance = await order_executor.fetch_account_balance()
    if startup_balance > 0:
        risk_manager.seed_real_balance(startup_balance)
    bh_record(bh_path, balance=risk_manager.get_balance(), trigger='startup')
    virtual_order_simulator.sync_real_balance_on_start(risk_manager.get_balance())

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

    # Detect maxQty-constrained symbols now that klines and balance are available
    try:
        _prices: dict[str, float] = {
            _sym: analyzers[_sym].get_current_price()
            for _sym in symbols
            if _sym in analyzers and analyzers[_sym].get_current_price() > 0
        }
        from config.risk_config import load_risk_config as _lrc
        _rcfg = _lrc()
        _rcfg['_detected_balance'] = risk_manager.get_balance() or startup_balance or 1000.0
        _adjusted = adjust_constrained_symbols(
            lot_cache=order_executor._lot_cache,
            bracket_maxes=order_executor._bracket_max,
            prices=_prices,
            symbol_registry=symbol_registry,
            risk_cfg=_rcfg,
            active_symbols=symbols,
        )
        if _adjusted:
            logger.info(f"Auto-adjusted weights/leverage for constrained symbols: {_adjusted}")
    except Exception as _e:
        logger.warning(f"Constraint detection failed (non-critical): {_e}")

    def _write_open_positions() -> None:
        """Snapshot current open orders (real + virtual) to disk for the dashboard."""
        real_open = []
        for sym, oo in order_executor.get_open_orders().items():
            real_open.append({
                'symbol': sym,
                'preset_name': oo.preset_name,
                'side': oo.side,
                'entry_price': oo.entry_price,
                'tp': oo.tp_price,
                'sl': oo.sl_price,
                'quantity': oo.quantity,
                'leverage': oo.leverage,
                'scenario': oo.scenario,
                'open_time': oo.open_time,
                'status': 'open',
            })
        virtual_open = virtual_order_simulator.get_open_positions()
        payload = {
            'updated_at': datetime.now(timezone.utc).isoformat(),
            'real': real_open,
            'virtual': virtual_open,
        }
        _path = _PROJECT_ROOT / 'data' / f'open_positions_{mode_manager.current_mode}.json'
        _path.parent.mkdir(parents=True, exist_ok=True)
        _tmp = _path.with_suffix('.json.tmp')
        _tmp.write_text(json.dumps(payload))
        _tmp.replace(_path)

    await order_executor.reconcile_with_exchange()
    _write_open_positions()  # overwrite any stale file from a crashed previous session
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
    _kline_refresh_counters: dict[str, int] = {}
    _placed_this_candle: dict[str, int] = {}  # symbol → candle_open_ts of last placed order
    _pending_signals: dict[str, dict] = {}   # symbol → signal details of last placed order
    _recent_sl_hit: dict[str, dict] = {}     # "symbol:preset" → signal from last SL-hit order
    # Loss-streak directional cooldown state (mirrors backtester implementation)
    _loss_streak: dict[str, int] = {}        # "symbol:preset:side" → consecutive loss count
    _streak_blocked: dict[str, int] = {}     # "symbol:preset:side" → candle_ts after which block expires
    _global_pause_until: dict[str, int] = {} # "symbol:preset" → candle_ts after which global pause expires
    _last_loss_ts: dict[str, int] = {}       # "symbol:preset:side" → candle_ts of last loss

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
        symbol: str, best, settings, balance: float, candle_ts: int,
        trade_cap: float = 0.0,
    ) -> float:
        # Prevent placing more than one real order per symbol per 15m candle batch.
        # Multiple symbols closing at the same timestamp trigger multiple loop runs;
        # if an order closes and resets to IDLE within that window, we'd double-enter.
        if candle_ts > 0 and _placed_this_candle.get(symbol) == candle_ts:
            return 0.0

        preset_name = virtual_tracker.best_preset(symbol)
        overrides = all_presets.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        # Re-run the engine with the best preset's own settings so that proximity_zone_pct,
        # min_swing_points, etc. are applied consistently — same as _try_open in the virtual
        # simulator. The base-settings signal (passed in as `best`) only gates entry here.
        _current_px = analyzers[symbol].get_current_price() if symbol in analyzers else 0.0
        _trend = analyzers[symbol].get_trend() if symbol in analyzers else None
        if _trend is None:
            return 0.0
        _preset_entry_px = _current_px if _current_px > 0 else best.getEntryPrice()
        if _preset_entry_px <= 0:
            return 0.0
        best = RecommendationEngine(preset_settings).generate(_trend, _preset_entry_px)
        if best is None:
            return 0.0

        # A3: use analyzer's current price (updated by live ticks) instead of stale signal entry
        _raw_entry = best.getEntryPrice()
        entry = _current_px if _current_px > 0 else _raw_entry
        if entry <= 0:
            return 0.0

        side = best.getSide()
        raw_tp = best.getTarget()
        sl_raw = best.getStop()

        # C1: validate geometry (mirrors backtester.py lines 284-296)
        if sl_raw is None or sl_raw <= 0:
            return 0.0
        if side == 'BUY':
            if raw_tp is None or raw_tp <= entry or sl_raw >= entry:
                return 0.0
            tp = entry + (raw_tp - entry) * preset_settings.tp_multiplier
            sl_dist_pct = (entry - sl_raw) / entry * 100
            profit_dist_pct = (tp - entry) / entry * 100
        else:
            if raw_tp is None or raw_tp >= entry or sl_raw <= entry:
                return 0.0
            tp = entry - (entry - raw_tp) * preset_settings.tp_multiplier
            # SELL SL spikes are harsher — apply ×1.5 when checking min_sl_pct (matches backtester)
            sl_dist_pct = (sl_raw - entry) / entry * 100 * 1.5
            profit_dist_pct = (entry - tp) / entry * 100

        sl = sl_raw  # may be tightened by sl_adjust_to_rr below

        # Absolute SL floor: reject if SL is within 0.01% of entry (degenerate signal)
        if abs(sl - entry) < entry * 0.0001:
            return 0.0

        _eff_for_dl = virtual_tracker.get_efficiency_score(symbol)

        # max_profit_pct filter
        if preset_settings.max_profit_pct > 0 and profit_dist_pct > preset_settings.max_profit_pct:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_max_profit_pct',
                reason=f'profit={profit_dist_pct:.2f}% > max={preset_settings.max_profit_pct}%',
                balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            return 0.0

        # min_sl_pct filter
        if preset_settings.min_sl_pct > 0 and sl_dist_pct < preset_settings.min_sl_pct:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_min_sl_pct',
                reason=f'sl_dist={sl_dist_pct:.2f}% < min={preset_settings.min_sl_pct}%',
                balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            return 0.0

        # max_sl_pct filter
        if preset_settings.max_sl_pct > 0 and sl_dist_pct > preset_settings.max_sl_pct:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_max_sl_pct',
                reason=f'sl_dist={sl_dist_pct:.2f}% > max={preset_settings.max_sl_pct}%',
                balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            return 0.0

        # ATR-based SL floor (instrument-agnostic structural filter)
        if preset_settings.min_sl_atr_mult > 0 and preset_settings.atr_lookback > 0:
            klines_now = analyzers[symbol].get_klines() if symbol in analyzers else []
            if klines_now:
                tail = klines_now[-preset_settings.atr_lookback:]
                avg_range = sum(float(k[2]) - float(k[3]) for k in tail) / len(tail)
                if avg_range > 0 and abs(sl - entry) < preset_settings.min_sl_atr_mult * avg_range:
                    dl_record(
                        dl_path, candle_ts=candle_ts, symbol=symbol,
                        decision='skip_sl_too_tight',
                        reason=f'sl_dist={abs(sl - entry):.6f} < {preset_settings.min_sl_atr_mult}×avg_range={avg_range:.6f}',
                        balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                        preset_name=preset_name, scenario=_active_scenario_name,
                    )
                    return 0.0

        profit_dist = abs(tp - entry)
        loss_dist = abs(sl - entry)

        if loss_dist == 0:
            return 0.0

        # min_profit_loss_ratio — with optional sl_adjust_to_rr (mirrors backtester lines 318-333)
        if profit_dist / loss_dist < preset_settings.min_profit_loss_ratio:
            if preset_settings.sl_adjust_to_rr and profit_dist > 0:
                required_loss_dist = profit_dist / preset_settings.min_profit_loss_ratio
                if side == 'BUY':
                    sl = entry - required_loss_dist
                    _new_sl_pct = required_loss_dist / entry * 100
                else:
                    sl = entry + required_loss_dist
                    _new_sl_pct = required_loss_dist / entry * 100 * 1.5
                if preset_settings.min_sl_pct > 0 and _new_sl_pct < preset_settings.min_sl_pct:
                    dl_record(
                        dl_path, candle_ts=candle_ts, symbol=symbol,
                        decision='skip_sl_adjust_too_tight',
                        reason=f'adjusted_sl={_new_sl_pct:.3f}% < min_sl={preset_settings.min_sl_pct}%',
                        balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                        preset_name=preset_name, scenario=_active_scenario_name,
                    )
                    return 0.0
            else:
                rr = profit_dist / loss_dist
                dl_record(
                    dl_path, candle_ts=candle_ts, symbol=symbol,
                    decision='skip_rr',
                    reason=f'rr={rr:.2f} < min={preset_settings.min_profit_loss_ratio}',
                    balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                    preset_name=preset_name, scenario=_active_scenario_name,
                )
                return 0.0

        # Duplicate-signal skip: avoid re-entering a signal that closely resembles a recent SL hit
        if preset_settings.duplicate_skip_candles > 0 and candle_ts > 0:
            _key = f"{symbol}:{preset_name or 'default'}"
            _prev = _recent_sl_hit.get(_key)
            if _prev and _prev['side'] == side:
                _dur = _tf_to_ms(settings.timeframe)
                _candles_since = (candle_ts - _prev['candle_ts']) // _dur
                if _candles_since <= preset_settings.duplicate_skip_candles:
                    _p = preset_settings.duplicate_skip_pct / 100.0
                    if (_prev['entry'] > 0 and abs(entry - _prev['entry']) / _prev['entry'] <= _p and
                            _prev['sl'] > 0 and abs(sl - _prev['sl']) / _prev['sl'] <= _p and
                            _prev['tp'] > 0 and abs(tp - _prev['tp']) / _prev['tp'] <= _p):
                        logger.info(
                            f"[{symbol}] Signal skipped — duplicate of SL-hit signal "
                            f"{_candles_since} candle(s) ago (preset={preset_name}, side={side})"
                        )
                        dl_record(
                            dl_path, candle_ts=candle_ts, symbol=symbol,
                            decision='skip_duplicate_sl',
                            reason=f'duplicate of SL-hit signal {_candles_since} candle(s) ago',
                            balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                            preset_name=preset_name, scenario=_active_scenario_name,
                        )
                        return 0.0

        # Loss-streak gate — mirrors backtester directional cooldown (loss_streak_max=0 disables)
        if preset_settings.loss_streak_max > 0 and candle_ts > 0:
            _pk = f"{symbol}:{preset_name or 'default'}"
            if _global_pause_until.get(_pk, 0) >= candle_ts:
                logger.info(
                    f"[{symbol}] Signal skipped — global pause active (preset={preset_name})"
                )
                return 0.0
            _sk = f"{symbol}:{preset_name or 'default'}:{side}"
            if _streak_blocked.get(_sk, 0) >= candle_ts:
                logger.info(
                    f"[{symbol}] Signal skipped — {side} loss streak cooldown (preset={preset_name})"
                )
                return 0.0

        bracket_max = order_executor.get_bracket_max(symbol)
        max_policy_lev = risk_cfg.get('max_leverage_level', 5)
        base_lev = risk_cfg.get('base_leverage', 1)
        eff_score = virtual_tracker.get_efficiency_score(symbol)
        actual_lev = scenario.get_leverage(symbol, eff_score, base_lev, max_policy_lev, bracket_max)
        _lev_override = symbol_registry.get_leverage_override(symbol)
        if _lev_override > 0:
            actual_lev = min(_lev_override, bracket_max)
        if actual_lev <= 0:
            actual_lev = 1

        min_notional = min_notionals.get(symbol)
        if min_notional is None:
            min_notional = await order_executor.get_min_notional(symbol)
            min_notionals[symbol] = min_notional

        margin = min_notional / actual_lev
        eff_score = virtual_tracker.get_efficiency_score(symbol)

        # When balance can't fund the minimum margin at the scenario leverage,
        # try the lowest leverage that brings the margin within balance.
        if balance < margin and min_notional > 0 and balance > 0:
            lev_needed = math.ceil(min_notional / balance)
            if lev_needed <= bracket_max:
                actual_lev = lev_needed
                margin = min_notional / actual_lev
                logger.info(
                    f"[{symbol}] Leverage bumped to {actual_lev}x to meet min notional "
                    f"(balance={balance:.2f}, min_notional={min_notional:.2f})"
                )
            else:
                dl_record(
                    dl_path, candle_ts=candle_ts, symbol=symbol,
                    decision='skip_min_notional',
                    reason=(
                        f'balance={balance:.2f} too small for min_notional={min_notional:.2f} '
                        f'even at bracket_max={bracket_max}x'
                    ),
                    balance=balance, leverage=bracket_max, efficiency_score=eff_score,
                    preset_name=preset_name, scenario=_active_scenario_name,
                )
                logger.info(f"[{symbol}] Balance too small for min notional at any leverage — skipping")
                return 0.0

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

        # Determine per-trade margin from scenario allocation or proportional BGF cap
        if scenario.uses_weight_allocation:
            active_syms = [
                s for s in symbol_registry.get_symbols()
                if not symbol_registry.is_disabled(s) and not symbol_registry.is_symbol_paused(s)
            ]
            trade_margin = max(risk_manager.get_symbol_allocation(symbol, active_syms), margin)
        else:
            # trade_cap is the pre-computed proportional share for this symbol
            cap = trade_cap if trade_cap > 0 else margin
            trade_margin = max(min(balance, cap), margin)

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

        # 2% buffer ensures step-rounding never drops notional below the exchange floor (-4164 guard).
        quantity = trade_margin * actual_lev * 1.02 / entry

        bh_record(bh_path, balance=balance, trigger='order_open',
                  symbol=symbol, leverage=actual_lev)

        precision = best.getPrecision() if hasattr(best, 'getPrecision') else 0.0

        placed = await order_executor.place_order(
            symbol=symbol,
            preset_name=preset_name or 'default',
            side=side,
            entry=entry,
            tp=tp,
            sl=sl,
            quantity=quantity,
            leverage=actual_lev,
            partial_take_pct=preset_settings.partial_take_pct,
            trailing_stop_pct=preset_settings.trailing_stop_pct,
            max_losing_pct=preset_settings.max_losing_pct,
            max_losing_amount_usdt=preset_settings.max_losing_amount_usdt,
            max_losing_candles=preset_settings.max_losing_candles,
            level=best.getLevel(),
            signal_type=best.getType().value,
            balance_at_open=balance,
            signal_level=best.getLevel() or 0,
            precision_score=precision or 0.0,
            scenario=_active_scenario_name,
        )
        # Mark as attempted regardless of outcome: prevents repeated failed attempts
        # in the same candle batch when other symbols' close events trigger the loop.
        _placed_this_candle[symbol] = candle_ts
        if placed:
            _pending_signals[symbol] = {
                'preset_name': preset_name or 'default',
                'side': side,
                'entry': entry,
                'sl': sl,
                'tp': tp,
                'candle_ts': candle_ts,
            }
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='placed', reason='',
                balance=balance, leverage=actual_lev, efficiency_score=eff_score,
                preset_name=preset_name, scenario=_active_scenario_name,
                signal_type=best.getType().value,
                level=best.getLevel(),
                precision_score=precision or 0.0,
            )
            return trade_margin
        return 0.0

    def _update_loss_streak(c: dict, ts: int) -> None:
        """Update per-preset directional loss streak state after an order closes."""
        sym = c['symbol']
        pname = c.get('preset_name', 'default')
        side = c.get('side', '')
        overrides = all_presets.get(pname, {})
        sym_s = sym_settings.get(sym, first_settings)
        ps = dataclasses.replace(sym_s, **overrides)
        if ps.loss_streak_max <= 0:
            return
        tf_ms = _tf_to_ms(timeframe)
        sk = f"{sym}:{pname}:{side}"
        other_sk = f"{sym}:{pname}:{'SELL' if side == 'BUY' else 'BUY'}"
        if c.get('result') == 'loss':
            cnt = _loss_streak.get(sk, 0) + 1
            _last_loss_ts[sk] = ts
            if cnt >= ps.loss_streak_max:
                _streak_blocked[sk] = ts + ps.loss_streak_cooldown_candles * tf_ms
                _loss_streak[sk] = 0
                logger.info(
                    f"[{sym}] {side} loss streak {ps.loss_streak_max}/{ps.loss_streak_max} "
                    f"(preset={pname}) — {side} blocked for {ps.loss_streak_cooldown_candles} candles"
                )
            else:
                _loss_streak[sk] = cnt
            if ps.global_pause_trigger_candles > 0:
                other_ts = _last_loss_ts.get(other_sk, 0)
                if other_ts > 0 and (ts - other_ts) <= ps.global_pause_trigger_candles * tf_ms:
                    pk = f"{sym}:{pname}"
                    _global_pause_until[pk] = ts + ps.global_pause_candles * tf_ms
                    logger.info(
                        f"[{sym}] Global pause triggered (preset={pname}) — "
                        f"both sides lost within {ps.global_pause_trigger_candles} candles — "
                        f"paused for {ps.global_pause_candles} candles"
                    )
        else:
            _loss_streak[sk] = 0

    async def _refresh_klines_bg(symbol: str, count: int, stagger: float) -> None:
        if stagger > 0:
            await asyncio.sleep(stagger)
        try:
            await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, count)
        except Exception as _e:
            logger.debug(f"[{symbol}] Background kline refresh failed: {_e}")

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
                await order_executor.sync_positions_with_exchange()

        if symbol_registry.is_disabled(symbol):
            return

        if symbol not in sym_settings or symbol not in analyzers:
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        incoming_open_ms = int(kline[0])
        try:
            if feed.has_gap(symbol, timeframe, incoming_open_ms):
                asyncio.create_task(_refresh_klines_bg(symbol, count=100, stagger=0))
            else:
                _kline_refresh_counters[symbol] = _kline_refresh_counters.get(symbol, 0) + 1
                if _kline_refresh_counters[symbol] >= KLINE_REFRESH_EVERY:
                    _kline_refresh_counters[symbol] = 0
                    _syms_list = symbol_registry.get_symbols()
                    _idx = _syms_list.index(symbol) if symbol in _syms_list else 0
                    asyncio.create_task(
                        _refresh_klines_bg(symbol, count=20, stagger=_idx * KLINE_STAGGER_SECS)
                    )
        except Exception as _gap_e:
            logger.debug(f"[{symbol}] Gap check error: {_gap_e}")

        candle_to_add = kline
        recs = analyzer.add_candle(candle_to_add)
        best_for_this = analyzer.get_best_recommendation()

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
            if symbol_registry.is_symbol_paused(sym):
                continue
            if symbol_registry.get_weight(sym) == 0.0:
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
            virtual_order_simulator.set_candle_alloc_context(True, {})
            deployable = risk_manager.get_deployable_budget()
            deployed_w = 0.0
            for sym, best, sym_s, _ in candidates:
                remaining_w = max(0.0, deployable - deployed_w)
                if remaining_w <= 0:
                    break
                used_w = await _try_place_order(sym, best, sym_s, risk_manager.get_balance(), candle_ts)
                deployed_w += used_w
        else:
            # BestGetsFirst: proportional caps derived from efficiency scores (disabled already excluded)
            bgf_top_n = int(risk_cfg.get("bgf_top_n", 0))
            if bgf_top_n > 0:
                candidates = candidates[:bgf_top_n]
            deployable = risk_manager.get_deployable_budget()
            total_score = sum(max(0.0, s) for _, _, _, s in candidates)
            bgf_fractions = {
                sym: (max(0.0, score) / total_score if total_score > 0 else 1.0 / len(candidates))
                for sym, _, _, score in candidates
            } if candidates else {}
            virtual_order_simulator.set_candle_alloc_context(False, bgf_fractions)
            deployed = 0.0
            for sym, best, sym_s, score in candidates:
                remaining = max(0.0, deployable - deployed)
                if remaining <= 0:
                    break
                if total_score > 0:
                    sym_cap = deployable * max(0.0, score) / total_score
                else:
                    sym_cap = deployable / len(candidates) if candidates else 0.0
                if sym_cap <= 0:
                    continue
                used = await _try_place_order(sym, best, sym_s, remaining, candle_ts, trade_cap=sym_cap)
                deployed += used

        # D1: OHLC-level SL/TP check — catches gaps that per-tick checks miss.
        # Use REST-refreshed candle_to_add when available; it has more accurate OHLC than the WS close event.
        candle_high = float(candle_to_add[2])
        candle_low = float(candle_to_add[3])
        candle_open_price = float(candle_to_add[1])
        candle_close_price = float(candle_to_add[4])
        candle_closed = await order_executor.check_symbol_candle(
            symbol, candle_high, candle_low, candle_open_price, candle_close_price,
        )
        for c in candle_closed:
            if not (c['pnl_usdt'] == 0.0 and c.get('close_price') == c.get('entry_price')):
                virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])
            if c.get('result') == 'loss':
                _sig = _pending_signals.get(c['symbol'])
                if _sig and _sig['preset_name'] == c.get('preset_name'):
                    _recent_sl_hit[f"{c['symbol']}:{_sig['preset_name']}"] = _sig
            _update_loss_streak(c, candle_ts)
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

        if symbol_registry.get_weight(symbol) > 0.0:
            await virtual_order_simulator.on_candle_close(
                symbol=symbol,
                analyzer=analyzer,
                best_preset_name=virtual_tracker.best_preset(symbol),
                base_settings=settings,
            )

        weight_rebalancer.on_candle_close(candle_ts)

        export(
            symbol, timeframe, mode_manager.current_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best_for_this,
        )

        try:
            _write_open_positions()
        except Exception as _wop_exc:
            logger.debug(f"open_positions write failed: {_wop_exc}")

        if best_for_this:
            trades_logger.info(f"BEST | symbol={symbol} | {best_for_this}")
        for rec in recs:
            trades_logger.info(f"CANDIDATE | symbol={symbol} | {rec}")

    async def on_price_update(symbol: str, price: float) -> None:
        if symbol in analyzers:
            analyzers[symbol].update_price(price)

        closed = await order_executor.check_symbol_price(symbol, price)
        # Approximate the current candle's open timestamp for loss-streak tracking
        _now_ms = int(time.time() * 1000)
        _tick_tf_ms = _tf_to_ms(timeframe)
        _approx_candle_ts = _now_ms - (_now_ms % _tick_tf_ms)

        for c in closed:
            # Skip recording when pnl=0 and close==entry — indicates avgPrice fallback, not a real result.
            if not (c['pnl_usdt'] == 0.0 and c.get('close_price') == c.get('entry_price')):
                virtual_tracker.record_closed_trade(c['symbol'], c['preset_name'], c['pnl_usdt'])
            if c.get('result') == 'loss':
                _sig = _pending_signals.get(c['symbol'])
                if _sig and _sig['preset_name'] == c.get('preset_name'):
                    _recent_sl_hit[f"{c['symbol']}:{_sig['preset_name']}"] = _sig
            _update_loss_streak(c, _approx_candle_ts)
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
            if not (vc['pnl_usdt'] == 0.0 and vc.get('close_price') == vc.get('entry_price')):
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
            get_allocation=risk_manager.get_allocation_for_balance,
            get_scenario=lambda: _active_scenario_name,
            rank_max=len(all_presets),
            is_rank_disabled=symbol_registry.is_rank_disabled,
        )
        switch_balance = await order_executor.fetch_account_balance()
        if switch_balance > 0:
            risk_manager.update_balance(switch_balance)
        virtual_order_simulator.sync_real_balance_on_start(risk_manager.get_balance())
        notifier.notify("info", f"Mode switched to {target_mode}", "", "mode_manager")

    async def on_stop_bot() -> None:
        current_symbols = symbol_registry.get_symbols()
        await virtual_order_simulator.close_all_open(current_symbols, feed)
        await order_executor.close_all_orders_at_market()
        _write_open_positions()  # clears to empty after all orders closed
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)

    # Register SIGTERM handler so `docker stop` / deploy triggers the same graceful
    # shutdown as the dashboard Stop button (closes virtual + real orders before exit).
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM,
        lambda: asyncio.create_task(on_stop_bot()),
    )

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
    _menu_task = asyncio.create_task(telegram_menu.run())

    try:
        await feed.stream_combined(
            get_symbols=symbol_registry.get_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    except BotHaltError as _halt_exc:
        logger.critical(f"Bot halt: {_halt_exc}")
        current_syms = symbol_registry.get_symbols()
        await virtual_order_simulator.close_all_open(current_syms, feed)
        notifier.notify("emergency", "Bot halted — all symbols disabled", str(_halt_exc), "main")
    finally:
        for t in [_poll_task, _hb_task, _watchdog_task, _menu_task]:
            t.cancel()
        for t in [_poll_task, _hb_task, _watchdog_task, _menu_task]:
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
