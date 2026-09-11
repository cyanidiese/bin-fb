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
from config.settings import (
    load_settings, Settings, max_profit_cap_applies, clamp_sl_to_max,
)
from bot.rate_limit_guard import (
    guard as rl_guard, RateLimited, _SETTLE_S, unresolved_ban_endpoints,
)
from bot.system_log import read_entries as read_system_log
from bot.analyzer import Analyzer
from bot import analysis_log
from bot.data_feed import DataFeed
from bot.recommendation_engine import RecommendationEngine
from bot.exporter import export, write_symbols_json
from bot.mode_manager import ModeManager, opposite_mode, read_mode_file
from bot.instance_paths import backtest_results_name, instance_path
from bot.notifier import Notifier
from bot.telegram_menu import TelegramMenu
from bot.order_executor import BotHaltError, OrderExecutor, OrderState
from bot.symbol_registry import SymbolRegistry
from bot.virtual_tracker import VirtualTracker
from bot.virtual_order_simulator import VirtualOrderSimulator
from bot.risk_manager import RiskManager
from bot.leverage_scenario import create_scenario
from config.risk_config import (
    load_risk_config, save_risk_config, get_min_trades_for_ranking,
    locked_presets_for,
)
from bot.balance_history import last_known as bh_last_known, record as bh_record
from bot.decision_log import record as dl_record
from bot.lot_constraint_detector import adjust_constrained_symbols
from bot.weight_rebalancer import WeightRebalancer

_PROJECT_ROOT = Path(__file__).resolve().parent
# Set once in run() from Settings.virtual_only. Module-level because the writers below
# are called from startup, a 10-second heartbeat loop and shutdown — guarding at each
# call site means one missed site reintroduces the bug, and the bug here is: press Stop,
# get a success response, trading bot still running.
_VIRTUAL_ONLY: bool = False

_BOT_PID_PATH = _PROJECT_ROOT / "data" / "bot_pid.json"
_BOT_STATE_PATH = _PROJECT_ROOT / "dashboard" / "public" / "bot_state.json"
_HEARTBEAT_INTERVAL = 10  # seconds
# Periodic REST re-fetch per symbol, as a sanity check against WebSocket drift.
# Was 4 (15 calls/hour). Since on_candle_close() now appends every WS candle to the
# cache, the cache tracks the stream continuously and this is no longer how history is
# maintained — it only guards against silent drift. A genuine missed candle is still
# caught immediately by feed.has_gap(), which triggers a repair fetch, so once a day per
# symbol is enough: 15 calls/hour -> 15 calls/day.
KLINE_REFRESH_EVERY = int(os.getenv('KLINE_REFRESH_EVERY', '96'))
KLINE_STAGGER_SECS = 2    # seconds between each symbol's background refresh task
# Long enough to clear the rate-limit guard's settling window, so a gap refresh
# suppressed by settling is retried once rather than waiting a whole candle.
_KLINE_RETRY_AFTER_S = _SETTLE_S + 1.0


def _tf_to_ms(timeframe: str) -> int:
    units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000}
    return int(timeframe[:-1]) * units.get(timeframe[-1], 60_000)


def _write_pid() -> None:
    # The dashboard Stop button kills whatever PID is in this file. A virtual-only
    # instance must never own it, or Stop kills the wrong process.
    if _VIRTUAL_ONLY:
        return
    _BOT_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _BOT_PID_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"pid": os.getpid()}))
    tmp.replace(_BOT_PID_PATH)


def _write_bot_state(running: bool, mode: str, started_at: str,
                     symbols_active: int = 0, symbols_disabled: int = 0,
                     phase: str = 'starting') -> None:
    # The 'is the bot alive' indicator belongs to the trading bot. A virtual-only
    # instance would otherwise overwrite it every 10 seconds from the heartbeat.
    if _VIRTUAL_ONLY:
        return
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


# Re-exported so main.py refers to per-instance paths by one name. The primitive lives
# in bot/ because backtest.py and bot/risk_manager.py need it too and cannot import main.
_instance_path = instance_path


def _log_paths() -> tuple[Path, Path]:
    """(bot.log, trades.log) for this instance.

    Runs before Settings and ModeManager exist, so it reads the two inputs directly.
    A mirror's log name must track the market it reads — the opposite of the primary's
    recorded mode — and not TRADING_MODE, which a mirror ignores. The primary's paths
    do not depend on mode at all, which is what keeps logrotate and every existing
    `tail` working after a mode switch.
    """
    mirror = os.getenv('VIRTUAL_ONLY', 'false').lower() in ('1', 'true', 'yes')
    mode = opposite_mode(read_mode_file()) if mirror else 'test'
    base = Path('logs')
    return (_instance_path(base, 'bot.log', mode, mirror),
            _instance_path(base, 'trades.log', mode, mirror))


def setup_logging() -> None:
    Path('logs').mkdir(exist_ok=True)
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    _bot_log, _trades_log = _log_paths()

    general = logging.handlers.RotatingFileHandler(
        str(_bot_log), maxBytes=10 * 1024 * 1024, backupCount=5
    )
    general.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(general)

    trades_fmt = logging.Formatter('%(asctime)s %(message)s')
    trades_handler = logging.handlers.RotatingFileHandler(
        str(_trades_log), maxBytes=10 * 1024 * 1024, backupCount=5
    )
    trades_handler.setFormatter(trades_fmt)
    trades_logger = logging.getLogger('trades')
    trades_logger.setLevel(logging.INFO)
    trades_logger.addHandler(trades_handler)
    trades_logger.propagate = False


def _resolve_mode(base_settings: "Settings", mode_manager: "ModeManager") -> str:
    """Return the one mode this process runs, and make the feed agree with it.

    current_mode names every data file; Settings.trading_mode picks the REST and
    WebSocket endpoints. These came from two independent sources — data/bot_mode.json
    and the TRADING_MODE env var — so a disagreement meant writing '_live'-suffixed
    files while reading testnet prices, or the reverse. They agree on the server today,
    which is the only reason it has never bitten; nothing kept them in step.

    Stamping the resolved mode back onto Settings makes the market the bot reads and the
    market its filenames claim the same thing by construction. bot_mode.json wins
    because that is what the dashboard writes and what current_mode already used.
    """
    resolved = mode_manager.current_mode
    if base_settings.trading_mode != resolved:
        logging.getLogger('main').warning(
            f"Mode source disagreement: TRADING_MODE={base_settings.trading_mode!r}, "
            f"resolved={resolved!r} (mirror={base_settings.virtual_only}). "
            f"Using {resolved!r} for both filenames and endpoints."
        )
    base_settings.trading_mode = resolved
    return resolved


async def _mirror_watch(mode_manager: "ModeManager") -> None:
    """Exit when the primary's mode changes, so the container restarts as its opposite.

    Restarting beats switching in place: on_switch_mode() closes orders, refetches the
    balance and rebuilds every mode-scoped object, and a partial failure would leave
    this instance writing to a mix of both suffixes. A fresh process cannot be
    half-switched.

    Two consecutive confirmations 30s apart are required. bot_mode.json is written
    atomically by both its writers so a torn read is not possible, but the dashboard
    re-runs every backtest immediately after writing it — exiting mid-backtest for a
    value that is about to settle would be churn for nothing.
    """
    confirmations = 0
    while True:
        await asyncio.sleep(30.0)
        if not mode_manager.mirror_target_changed():
            confirmations = 0
            continue
        confirmations += 1
        if confirmations < 2:
            logging.getLogger('main').info(
                "Mirror: primary mode change seen, confirming in 30s"
            )
            continue
        logging.getLogger('main').warning(
            f"Mirror: primary mode changed — this instance ran "
            f"{mode_manager.current_mode!r}. Exiting so the container restarts as the "
            f"new opposite."
        )
        # os._exit rather than sys.exit or a raise: this is a background task, where an
        # exception would be swallowed by the task and never reach the event loop. The
        # mirror holds no positions and no credentials, so there is nothing to flush.
        os._exit(0)


async def run() -> None:
    logger = logging.getLogger('main')
    trades_logger = logging.getLogger('trades')
    risk_cfg = load_risk_config()
    _get_min_trades = lambda sym: get_min_trades_for_ranking(risk_cfg, sym)

    # Load symbol registry — source of truth for active symbols
    seed_symbols = [s.strip().upper() for s in os.getenv('SYMBOL', '').split(',') if s.strip()]
    # Loaded before the registry because virtual_only decides whether the registry may
    # be written at all, and _load() writes on its seed path. It is also needed before
    # the Notifier (virtual_only decides whether Telegram is wired) and before the
    # ModeManager (current_mode names every data file). load_settings() normalises
    # trading_mode to exactly 'test' or 'live', the same vocabulary current_mode uses.
    _base_settings = load_settings()
    _virtual_only = _base_settings.virtual_only
    symbol_registry = SymbolRegistry(
        seed_symbols=seed_symbols,
        # Shared config is input only for the mirror. Task 2 skips the callers that
        # mutate it; this closes the one write reachable from __init__.
        read_only=_virtual_only,
    )
    symbols = symbol_registry.get_symbols()
    if not symbols:
        logger.error("No active symbols in registry — cannot start")
        sys.exit(1)

    # The notifier's own files need a per-instance name, and it is built before
    # ModeManager exists (ModeManager takes the notifier). Resolve the mode from the
    # same two inputs ModeManager uses, then check below that they agree — if they ever
    # diverged, one instance would split its output across two suffixes.
    _instance_mode = (
        opposite_mode(read_mode_file()) if _virtual_only else read_mode_file())

    notifier = Notifier(
        log_path=_instance_path(
            _PROJECT_ROOT / "data", "system_log.json", _instance_mode, _virtual_only),
        alert_path=_instance_path(
            _PROJECT_ROOT / "dashboard" / "public", "alert_state.json",
            _instance_mode, _virtual_only),
        # A statistics-only mirror sends nothing: it has no trades to report, and two
        # bots alerting on one API ban is worse than one bot doing it. Notifier skips
        # sending when the token is empty, so local logging is unaffected and no code
        # downstream needs to know. Blanking the token here rather than guarding each
        # notify() call means a future caller cannot reintroduce a message by accident.
        telegram_token=(
            "" if _base_settings.virtual_only
            else risk_cfg.get("telegram", {}).get("token", "")
        ),
        telegram_chat_id=risk_cfg.get("telegram", {}).get("chat_id", ""),
        min_interval_s=float(risk_cfg.get("telegram_notify_interval_s", 120)),
        emergency_repeat_interval_s=float(risk_cfg.get("emergency_repeat_interval_s", 1800)),
        warning_repeat_interval_s=float(risk_cfg.get("warning_repeat_interval_s", 14400)),
    )
    mode_manager = ModeManager(
        notifier=notifier,
        mirror=_virtual_only,
    )
    current_mode = _resolve_mode(_base_settings, mode_manager)
    if _instance_mode != mode_manager.current_mode:
        # Not reachable: both resolutions read the same validated file through the same
        # two helpers. Checked rather than assumed because the failure is silent — the
        # notifier would log to one suffix while everything else used another.
        raise RuntimeError(
            f"Mode resolution disagreement: notifier paths used {_instance_mode!r} but "
            f"ModeManager resolved {mode_manager.current_mode!r}"
        )

    risk_manager = RiskManager(
        mode=current_mode,
        notifier=notifier,
        # The mirror runs on a zero balance. Sharing risk_state.json would blank the
        # trading bot's risk page with those zeros.
        state_path=_instance_path(
            _PROJECT_ROOT / "dashboard" / "public", "risk_state.json",
            current_mode, _virtual_only),
        # The mirror reads its own backtest. Without this it would size from
        # backtest_results_{symbol}.json, which the primary owns and sizes real
        # orders from.
        mirror=_base_settings.virtual_only,
    )

    # Load settings and build per-symbol state
    sym_settings: dict = {}
    analyzers: dict = {}
    for symbol in symbols:
        s = load_settings(symbol)
        # DataFeed is built from a per-symbol Settings, not from _base_settings, so
        # stamping only the base object would leave the endpoint free to disagree with
        # the mode that names the files. See _resolve_mode().
        s.trading_mode = current_mode
        sym_settings[symbol] = s
        engine = RecommendationEngine(s)
        analyzers[symbol] = Analyzer(s.swing_neighbours, engine)

    timeframe = sym_settings[symbols[0]].timeframe
    first_settings = sym_settings[symbols[0]]

    # A virtual-only instance gathers preset statistics on live charts and touches
    # nothing else: no real orders, no private endpoints, no Telegram, and no writes
    # to config shared with the trading bot. Every guard below is a plain skip — none
    # of them changes what a real order does.
    global _VIRTUAL_ONLY
    # Already resolved above, before the notifier needed it. Re-derived from the
    # per-symbol Settings only to confirm the two agree: VIRTUAL_ONLY comes from the
    # environment and is never overridden per symbol, and if that ever changed, some
    # files would take one name and some the other.
    if first_settings.virtual_only != _virtual_only:
        raise RuntimeError(
            f"virtual_only disagreement: base={_virtual_only}, "
            f"per-symbol={first_settings.virtual_only}"
        )
    _VIRTUAL_ONLY = _virtual_only
    if _virtual_only:
        logger.warning(
            "VIRTUAL-ONLY instance: no real orders, no private endpoints, no Telegram, "
            "no shared-config writes. Collecting preset statistics only."
        )

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
        get_min_trades=_get_min_trades,
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

    # Optional startup backtest — off by default (risk_config.startup_backtest).
    #
    # It is a blocking subprocess across all 15 symbols and it dominated every restart:
    # measured 256-567s, worst 9m11s, with 551s of completely silent log between
    # "Bot starting" and the feed being built — about 96% of startup. For that whole
    # window there is no WebSocket, no candle processing and no position monitoring in
    # this process (open positions are covered only by their exchange-side stop-loss).
    # Deploys are the main source of restarts, so that cost was being paid constantly
    # for results that persist in dashboard/public/backtest_results_*.json regardless.
    #
    # Enable it from the dashboard Settings page when a fresh backtest is wanted, or
    # run one on demand from the Backtest page. When it IS requested and fails, startup
    # still aborts — that fail-closed behaviour is deliberately unchanged.
    _startup_cfg = load_risk_config()
    if _startup_cfg.get("startup_backtest", False):
        notifier.notify("info", "Running startup backtest", f"mode={current_mode}", "main")
        _bt_klines = int(_startup_cfg.get("backtest_klines", 1500))
        _bt_started = time.monotonic()
        bt_result = subprocess.run(
            [sys.executable, "backtest.py", "--mode", current_mode, "--klines-count", str(_bt_klines)],
            capture_output=True,
            cwd=str(_PROJECT_ROOT),
        )
        if bt_result.returncode != 0:
            notifier.notify("emergency", "Startup backtest failed — cannot start",
                            bt_result.stderr.decode()[:500], "main")
            sys.exit(1)
        logger.info(
            f"Startup backtest finished in {time.monotonic() - _bt_started:.0f}s "
            f"({len(symbols)} symbols, {_bt_klines} klines)"
        )
    else:
        # Say how stale the seeds are. Silently reusing month-old backtest results
        # would be a trap: they feed RiskManager's leverage and cross-symbol allocation
        # as well as the virtual tracker's preset seeds.
        _ages, _missing = [], []
        for _sym in symbols:
            _bt_file = _PROJECT_ROOT / "dashboard" / "public" / backtest_results_name(
                _sym, current_mode, _virtual_only)
            if _bt_file.exists():
                _ages.append((time.time() - _bt_file.stat().st_mtime) / 3600.0)
            else:
                _missing.append(_sym)
        if _ages:
            logger.info(
                f"Startup backtest skipped (risk_config.startup_backtest=false) — "
                f"reusing existing results, age {min(_ages):.1f}-{max(_ages):.1f}h "
                f"(median {sorted(_ages)[len(_ages) // 2]:.1f}h)"
            )
        if _missing:
            logger.warning(
                f"Startup backtest skipped and no results exist for "
                f"{', '.join(_missing)} — these symbols start with no preset seeds and "
                f"base leverage until a backtest is run from the dashboard"
            )

    for sym in symbols:
        # Seed from THIS instance's backtest. The mirror backtested the other market;
        # seeding the primary's file would mix the two markets' preset statistics.
        bt_path = _PROJECT_ROOT / "dashboard" / "public" / backtest_results_name(
            sym, current_mode, _virtual_only)
        virtual_tracker.seed_from_backtest(sym, bt_path)

    feed = DataFeed(first_settings, live_klines=first_settings.live_klines)
    # Ban start/end are announced to Telegram: an outage that silently pauses reads is
    # otherwise invisible until someone reads the log.
    rl_guard.set_notifier(notifier.notify, mode=current_mode)

    # Restore a ban that was still running when the previous process exited. Guard
    # state is in-memory, so without this a restart mid-ban began clean and fired its
    # startup calls — kline loads, leverage brackets, balance — straight into the
    # active ban, extending it by ~2 minutes each. Loaded before DataFeed is used so
    # the first request is already suppressed.
    _rl_state_path = _instance_path(
        _PROJECT_ROOT / "data", "rate_limit_state.json", current_mode, _virtual_only)
    rl_guard.load_state(_rl_state_path)

    # Close out a ban alert the previous run never resolved. Guard state is in-memory,
    # so a restart while a block is armed kills it before _clear() can send the "ban
    # ended" notice — leaving the last Telegram message as "API ban started" for a ban
    # that may well have expired. Worded honestly: we do not know whether it is still
    # active until the first call finds out.
    try:
        _sys_log_path = _instance_path(
            _PROJECT_ROOT / "data", "system_log.json", current_mode, _virtual_only)
        for _stale_key in unresolved_ban_endpoints(read_system_log(_sys_log_path)):
            notifier.notify(
                "info",
                f"API ban tracking reset — {_stale_key}",
                (f"Endpoint:     {_stale_key}\n"
                 f"Trading mode: {current_mode}\n"
                 f"Reason:       the bot restarted while this endpoint was marked "
                 f"banned, so the previous run could not send its 'ban ended' "
                 f"notice.\n\n"
                 f"The rate-limit guard starts clear after a restart. If the ban is "
                 f"still active it will be detected again on the next read and you "
                 f"will get a fresh alert; if it has expired, nothing further "
                 f"happens."),
                "rate_limit_guard",
            )
    except Exception as _stale_exc:
        logger.debug(f"Ban-alert reconciliation skipped: {_stale_exc}")
    order_executor._feed = feed

    # Proactive exchange check + leverage brackets
    # check_symbols_on_exchange can _auto_disable() a symbol into the shared
    # symbol_registry.json, disabling it for the trading bot too. Leverage brackets
    # are a private endpoint and feed only the real order executor.
    if not _virtual_only:
        await order_executor.check_symbols_on_exchange(symbols)
        await order_executor.fetch_leverage_brackets(symbols)

    virtual_order_simulator.set_lot_cache(order_executor._lot_cache)

    # Pre-warm the lot cache for all symbols via a single exchange-info call
    await order_executor.prefetch_lot_sizes(symbols[0] if symbols else 'BTCUSDT')
    virtual_order_simulator.set_lot_cache(order_executor._lot_cache)  # re-wire after prefetch

    # Fetch real min_notionals and startup balance
    for sym in symbols:
        min_notionals[sym] = await order_executor.get_min_notional(sym)

    analysis_log.configure(
        # analysis_log holds a module-level handler singleton; two instances on one
        # file would interleave both markets into one analysis stream.
        _instance_path(_PROJECT_ROOT / 'logs', 'analysis.jsonl',
                       current_mode, _virtual_only),
        enabled=bool(risk_cfg.get('analysis_log_enabled', True)),
        max_bytes=int(risk_cfg.get('analysis_log_max_mb', 20)) * 1024 * 1024,
        backups=int(risk_cfg.get('analysis_log_backups', 5)),
    )
    bh_path = _PROJECT_ROOT / 'data' / f'balance_history_{current_mode}.json'
    # Where reconcile() records calculated-vs-exchange gaps. Unattributed wallet
    # movement (futures funding is the prime suspect) shows up here as drift with
    # zero trades, which is the only way to measure it.
    risk_manager.set_drift_log(_PROJECT_ROOT / 'data' / f'balance_drift_{current_mode}.json')
    dl_path = _PROJECT_ROOT / 'data' / f'decision_log_{current_mode}.json'

    # Mutable container for the balance TTL cache (mutable so the nested coroutine below
    # can update it). Declared before the startup read so that read can prime it.
    _balance_cache_inner: list[tuple[float, float]] = [(0.0, 0.0)]

    startup_balance = await order_executor.fetch_account_balance()
    # A restart inside a rate-limit ban gets 0.0 here — the guard refuses the call rather
    # than extending the ban, which is correct. But the seed below is gated on a positive
    # figure, so a 0 left RiskManager on its 1000.00 config default against a real
    # 3098.93 (observed 2026-09-09 17:35): deployable 680 instead of 2107, so every real
    # order sized at a third of intent until the ban expired 190 minutes later.
    # balance_history is the only last-known-good that exists before a successful call.
    if startup_balance <= 0:
        startup_balance = bh_last_known(bh_path)
        if startup_balance > 0:
            logger.warning(
                f"Balance unavailable at startup — seeded {startup_balance:.2f} USDT from "
                f"balance history instead of the config default"
            )
        else:
            logger.error(
                "Balance unavailable at startup and no usable history — sizing off the "
                "config default until the first successful read"
            )
    if startup_balance > 0:
        risk_manager.seed_real_balance(startup_balance)
        # Prime the placement-path cache too. It used to start at 0.0 and only fill on a
        # successful fetch, so after a restart one failed read left balance=0.00 and every
        # symbol hit skip_balance ('balance=0.00 < margin=1.00') while the bot already knew
        # the real figure. Observed 2026-09-08: seeded 3072.38 at 13:10, then two REZUSDT
        # orders blocked at 13:15 and 13:30 for insufficient balance.
        _balance_cache_inner[0] = (startup_balance, time.monotonic())
    bh_record(bh_path, balance=risk_manager.get_balance(),
              trigger='startup' if startup_balance > 0 else 'startup_unconfirmed')
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
            mirror=_virtual_only,
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

    def _live_price(sym: str) -> float:
        """Latest price the analyzer has for `sym`, or 0.0 when unknown."""
        try:
            az = analyzers.get(sym)
            return float(az.get_current_price()) if az is not None else 0.0
        except Exception:
            return 0.0

    def _unrealized(side: str, entry: float, qty: float, lev: float, price: float) -> dict:
        """Result so far on an open position, for the dashboard tooltip.

        Computed here rather than in the dashboard so there is one source of truth for a
        number a human may act on — the close button sits next to it.
        """
        if price <= 0 or entry <= 0 or qty <= 0:
            return {'current_price': None, 'unrealized_pnl_usdt': None, 'unrealized_pct': None}
        sign = 1.0 if side == 'BUY' else -1.0
        pnl = (price - entry) * qty * sign
        margin = entry * qty / lev if lev else 0.0
        return {
            'current_price': price,
            'unrealized_pnl_usdt': pnl,
            'unrealized_pct': (pnl / margin * 100.0) if margin > 0 else None,
        }

    def _write_open_positions() -> None:
        """Snapshot current open orders (real + virtual) to disk for the dashboard."""
        real_open = []
        for sym, oo in order_executor.get_open_orders().items():
            _px = _live_price(sym)
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
                **_unrealized(oo.side, oo.entry_price, oo.quantity, oo.leverage, _px),
            })
        virtual_open = []
        for vp in virtual_order_simulator.get_open_positions():
            _px = _live_price(vp.get('symbol', ''))
            virtual_open.append({
                **vp,
                **_unrealized(vp.get('side', 'BUY'), float(vp.get('entry_price') or 0),
                              float(vp.get('quantity') or 0), float(vp.get('leverage') or 1),
                              _px),
            })
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

    _restart_path = _PROJECT_ROOT / 'data' / f'restart_positions_{mode_manager.current_mode}.json'
    _restored = order_executor.restore_open_positions(_restart_path)
    if _restored:
        logger.info(f"Startup: {_restored} position(s) restored from restart state")
    # Private endpoint, and closing positions the bot has no record of is meaningless
    # for an instance that opens none.
    if not _virtual_only:
        await order_executor.reconcile_with_exchange()
    _write_open_positions()  # overwrite any stale file from a crashed previous session
    notifier.notify("info", "Startup complete", f"{len(symbols)} symbol(s) active", "main")

    # ── Callbacks ──────────────────────────────────────────────────────── #

    # One candle batch = all 15 symbols processing the same close. Measured over 66
    # batches on 2026-09-06: median 6.96s, p90 25.3s, max 27.8s. The old 5s TTL expired
    # mid-batch in 55% of them, so the same unchanged balance was re-fetched several
    # times per candle — and futures_account is our most expensive call (weight 5, vs 1
    # for klines). 60s covers every observed batch with better than 2x margin while
    # staying far below the 900s candle interval, so the balance still refreshes every
    # candle. Staleness is bounded by real activity rather than by this number: the
    # balance only moves when a position closes, and closes call _read_wallet_now(),
    # which bypasses the TTL and refreshes this cache on the way past.
    # Raised from 60s to one candle, paired with _balance_prefetch_loop() below. Every
    # -1003 on 2026-09-08 was this call, and all 13 landed within a second of a candle
    # boundary (+0.45s..+0.92s) — the instant every bot on the exchange reads its account,
    # on a CloudFront edge shared with other tenants. A 60s TTL guarantees the boundary
    # read misses the cache and goes to the network at exactly that spike. One candle means
    # the pre-fetch, which runs in the quiet middle, still covers the boundary.
    #
    # Staleness is not really 900s: the balance only moves when a position closes, and a
    # close calls _read_wallet_now(), which bypasses the TTL and refreshes this cache on the
    # way past. If a pre-fetch fails, the next boundary read finds the cache expired and
    # fetches as before — degrading to the old behaviour rather than serving a stale figure.
    _BALANCE_TTL = 900.0

    # Daily exchange-info refresh: re-fetch leverage brackets + min notionals every 96 candles
    # (96 × 15 min = 24 h). Counter increments only on the first symbol close per candle so
    # it ticks once per real candle regardless of how many symbols are active.
    _EXCHANGE_REFRESH_CANDLES = 96
    _candle_counter: list[int] = [0]
    _last_refresh_candle_open: list[int] = [0]
    _kline_refresh_counters: dict[str, int] = {}
    _placed_this_candle: dict[str, int] = {}  # symbol → candle_open_ts of last placed order
    _substituted_preset: dict[str, str] = {}  # symbol → substituted preset, per candle
    _symbols_synced_at: list[int] = [0]      # candle_ts of the last roster reconciliation
    _discard_logged: dict[str, int] = {}     # symbol → candle_ts of last discard log
    _norec_logged: dict[str, int] = {}       # symbol → candle_ts of last no_recommendation event
    _pending_signals: dict[str, dict] = {}   # symbol → signal details of last placed order
    _recent_sl_hit: dict[str, dict] = {}     # "symbol:preset" → signal from last SL-hit order
    # Loss-streak directional cooldown state (mirrors backtester implementation)
    _loss_streak: dict[str, int] = {}        # "symbol:side" → consecutive loss count
    _streak_blocked: dict[str, int] = {}     # "symbol:side" → candle_ts after which block expires
    _global_pause_until: dict[str, int] = {} # "symbol:preset" → candle_ts after which global pause expires
    _last_loss_ts: dict[str, int] = {}       # "symbol:preset:side" → candle_ts of last loss
    # Zone SL cooldown state — blocks re-entry after N consecutive SL hits at same level
    _zone_sl_count: dict[str, int] = {}      # "symbol:preset:side" → consecutive zone hit count
    _zone_sl_level: dict[str, float] = {}    # "symbol:preset:side" → SL price of current sequence
    _zone_sl_block: dict[str, int] = {}      # "symbol:preset:side" → candle_ts until zone block expires

    async def _get_fresh_balance() -> float:
        """TTL-cached wallet balance for sizing/risk. May return a stale value.

        Falling back to the cache on a failed fetch is deliberate here: sizing needs
        *a* number and last-known is better than 0. Do NOT use this to report a
        balance to the user — see _read_wallet_now().
        """
        # Guarded here rather than at each call site so every caller — including any
        # added later — is covered. futures_account is a private endpoint the
        # virtual-only instance has no credentials for, and virtual sizing uses the
        # rank-pool balances, not this one.
        if _virtual_only:
            return 0.0
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
        # Fetch failed. Prefer the TTL cache, then RiskManager's balance — its
        # last-known-good, updated on every successful read and every trade close.
        # Returning 0.0 here reads as "no funds" and blocks every order via skip_balance.
        if cached_val > 0:
            return cached_val
        return risk_manager.get_balance()

    def _before_after(closed: dict, wallet_after: float) -> tuple[float, float, bool]:
        """(before, after, was_computed) for a close notification.

        `after` prefers a confirmed exchange read. Otherwise it is the RUNNING balance,
        which apply_realised() has already advanced by this trade's PnL — so `before` is
        that minus the PnL.

        This replaced a fallback that read closed['wallet_at_open'], captured when the
        order OPENED. Measured 2026-09-11: a TIAUSDT position held 28 hours reported
        Before 3145.03 / After 3059.28 (3145.03 - 85.76) against an actual wallet of
        2917.76 — overstated by 141.52, because the wallet read failed under a rate-limit
        ban and the 28-hour-old snapshot was used instead.

        Returns (0.0, 0.0, False) when nothing is known, which still renders as "n/a".
        """
        try:
            pnl = float(closed.get('pnl_usdt') or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        if wallet_after and wallet_after > 0:
            return wallet_after - pnl, wallet_after, False
        running = risk_manager.get_balance()
        if running > 0:
            return running - pnl, running, True
        return 0.0, 0.0, False

    async def _read_wallet_now() -> float:
        """Uncached wallet read for figures we report to the user. 0.0 = unavailable.

        Two things went wrong on 2026-08-19 and both are closed off here:
        the TTL cache above was populated by the placement pass at the *top* of
        on_candle_close, so a close notified ~2s later reported the balance from
        BEFORE the close settled; and when futures_account returned -1003 (IP
        banned) the cache fallback silently served an hours-old figure. This
        bypasses the TTL entirely and returns 0.0 rather than any stale value, so
        the notifier prints "n/a" instead of a wrong number. Successful reads still
        refresh the shared cache so the call is not wasted.
        """
        try:
            bal = await order_executor.fetch_account_balance()
        except Exception as exc:
            logger.warning(f"Wallet read failed: {exc}")
            return 0.0
        if bal > 0:
            _balance_cache_inner[0] = (bal, time.monotonic())
            return bal
        logger.warning("Wallet read returned 0 — reporting balance as unavailable")
        return 0.0

    async def _try_place_order(
        symbol: str, best, settings, balance: float, candle_ts: int,
        trade_cap: float = 0.0,
        bypass_pct_cap: bool = False,
    ) -> float:
        # Every rejection is recorded. 17 of the 27 exits below used to return silently,
        # so a weighted, signalling symbol could produce nothing all day with no trace of
        # why — measured: 118 floor_sl_pct events with no follow-up decision, and
        # ETHFIUSDT/REZUSDT reaching "Using manually locked preset" and then vanishing.
        # decision_log.record()'s own docstring already listed skip_already_open and
        # skip_no_signal; they were never wired up.
        def _skip(decision: str, reason: str, preset: str | None = None,
                  eff: float = 0.0, lev: int = 0) -> float:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision=decision, reason=reason,
                balance=balance, leverage=lev, efficiency_score=eff,
                preset_name=preset, scenario=_active_scenario_name,
            )
            return 0.0

        # Prevent placing more than one real order per symbol per 15m candle batch.
        # Multiple symbols closing at the same timestamp trigger multiple loop runs;
        # if an order closes and resets to IDLE within that window, we'd double-enter.
        if candle_ts > 0 and _placed_this_candle.get(symbol) == candle_ts:
            return _skip('skip_already_placed_this_candle',
                         f'an order was already placed for {symbol} on this candle')

        # Trading blackout hours (UTC): skip real orders during high-volatility windows.
        # Virtual orders are unaffected — data collection continues normally.
        _blackout = set(risk_cfg.get('trading_blackout_hours', []))
        if _blackout and datetime.now(timezone.utc).hour in _blackout:
            return _skip('skip_blackout_hour',
                         f'trading blackout H{datetime.now(timezone.utc).hour:02d} UTC')

        _locked_presets = locked_presets_for(risk_cfg, current_mode)
        is_locked = symbol in _locked_presets
        if is_locked:
            preset_name = _locked_presets[symbol]
            logger.info(f"[{symbol}] Using manually locked preset: {preset_name}")
        else:
            preset_name = _substituted_preset.get(symbol) or virtual_tracker.best_preset(symbol)
            if symbol in _substituted_preset:
                logger.info(f"[{symbol}] Order uses substituted preset: {preset_name}")
            if _active_scenario_name != "tats" and virtual_tracker.is_virtual_only(symbol):
                return _skip('skip_virtual_only_floor',
                             'virtual-only floor active for this symbol', preset_name)
        _blocklist = risk_cfg.get("preset_blocklist", [])
        if not is_locked and preset_name in _blocklist:
            return _skip('skip_blocklisted_preset',
                         f"preset '{preset_name}' is blocklisted", preset_name)
        overrides = all_presets.get(preset_name or 'default', {})
        preset_settings = dataclasses.replace(settings, **overrides)

        # Per-symbol Settings overrides from risk_config (applied on top of preset).
        # Use this for symbol-specific tuning that isn't captured by any single preset,
        # e.g. a wider max_profit_pct for one symbol or a looser SL cap for another.
        _sym_cfg_overrides = risk_cfg.get("per_symbol_settings", {}).get(symbol, {})
        if _sym_cfg_overrides:
            _valid_fields = {f.name for f in dataclasses.fields(Settings)}
            _filtered = {k: v for k, v in _sym_cfg_overrides.items() if k in _valid_fields}
            if _filtered:
                try:
                    preset_settings = dataclasses.replace(preset_settings, **_filtered)
                except Exception:
                    pass  # malformed override — skip silently

        # Re-run the engine with the best preset's own settings so that proximity_zone_pct,
        # min_swing_points, etc. are applied consistently — same as _try_open in the virtual
        # simulator. The base-settings signal (passed in as `best`) only gates entry here.
        _current_px = analyzers[symbol].get_current_price() if symbol in analyzers else 0.0
        _trend = analyzers[symbol].get_trend() if symbol in analyzers else None
        if _trend is None:
            return _skip('skip_no_trend', 'analyzer has no trend for this symbol', preset_name)
        _preset_entry_px = _current_px if _current_px > 0 else best.getEntryPrice()
        if _preset_entry_px <= 0:
            return _skip('skip_no_price', 'no usable current price or signal entry', preset_name)
        best = RecommendationEngine(preset_settings).generate(_trend, _preset_entry_px)
        if best is None:
            # The candidate loop found a signal under the base/best-preset settings, but
            # re-running the engine under THIS preset's own settings produces none. For a
            # locked symbol that means the lock itself is why nothing trades.
            return _skip('skip_no_signal',
                         f"preset '{preset_name}' own settings generate no signal here",
                         preset_name)

        # A3: use analyzer's current price (updated by live ticks) instead of stale signal entry
        _raw_entry = best.getEntryPrice()
        entry = _current_px if _current_px > 0 else _raw_entry
        if entry <= 0:
            return _skip('skip_bad_entry', f'entry price {entry}', preset_name)

        side = best.getSide()
        raw_tp = best.getTarget()
        sl_raw = best.getStop()

        # C1: validate geometry (mirrors backtester.py lines 284-296)
        if sl_raw is None or sl_raw <= 0:
            return _skip('skip_bad_geometry', f'stop {sl_raw} is missing or <= 0', preset_name)
        if side == 'BUY':
            if raw_tp is None or raw_tp <= entry or sl_raw >= entry:
                return _skip('skip_bad_geometry',
                             f'BUY needs tp>entry>sl, got tp={raw_tp} entry={entry} sl={sl_raw}',
                             preset_name)
            tp = entry + (raw_tp - entry) * preset_settings.tp_multiplier
            sl_dist_pct = (entry - sl_raw) / entry * 100
            profit_dist_pct = (tp - entry) / entry * 100
        else:
            if raw_tp is None or raw_tp >= entry or sl_raw <= entry:
                return _skip('skip_bad_geometry',
                             f'SELL needs tp<entry<sl, got tp={raw_tp} entry={entry} sl={sl_raw}',
                             preset_name)
            tp = entry - (entry - raw_tp) * preset_settings.tp_multiplier
            # SELL SL spikes are harsher — apply ×1.5 when checking min_sl_pct (matches backtester)
            sl_dist_pct = (sl_raw - entry) / entry * 100 * 1.5
            profit_dist_pct = (entry - tp) / entry * 100

        sl = sl_raw  # may be tightened by sl_adjust_to_rr below

        # Absolute SL floor: reject if SL is within 0.01% of entry (degenerate signal)
        if abs(sl - entry) < entry * 0.0001:
            return _skip('skip_degenerate_sl',
                         f'stop within 0.01% of entry (sl={sl}, entry={entry})', preset_name)

        _eff_for_dl = virtual_tracker.get_efficiency_score(symbol)
        _global_min_sl = risk_cfg.get("global_min_sl_pct", 0.0)

        # max_profit_pct filter (optionally scoped to specific trend levels)
        _sig_level = best.getLevel()
        if max_profit_cap_applies(preset_settings, _sig_level) and profit_dist_pct > preset_settings.max_profit_pct:
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='skip_max_profit_pct',
                reason=(f'profit={profit_dist_pct:.2f}% > max={preset_settings.max_profit_pct}% '
                        f'(L{_sig_level})'),
                balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                preset_name=preset_name, scenario=_active_scenario_name,
            )
            return 0.0

        # SL floor — widen rather than reject if SL is too close to entry.
        # Effective minimum = max(global floor, preset min_sl_pct).
        # Per-symbol overrides are already merged into preset_settings above via per_symbol_settings,
        # so preset_settings.min_sl_pct already holds the per-symbol value when set.
        _effective_min_sl = max(
            preset_settings.min_sl_pct if preset_settings.min_sl_pct > 0 else 0.0,
            _global_min_sl,
        )
        if _effective_min_sl > 0 and sl_dist_pct < _effective_min_sl:
            _orig_sl = sl
            _orig_sl_pct = sl_dist_pct
            if side == 'BUY':
                sl = entry * (1.0 - _effective_min_sl / 100.0)
            else:
                # sl_dist_pct applies ×1.5 for SELL — invert to get actual price distance
                sl = entry * (1.0 + _effective_min_sl / 1.5 / 100.0)
            sl_dist_pct = _effective_min_sl
            logger.info(
                f"[{symbol}] SL floored: {_orig_sl_pct:.3f}% → {_effective_min_sl:.3f}%"
                f" ({_orig_sl:.6g} → {sl:.6g}) preset={preset_name}"
            )
            dl_record(
                dl_path, candle_ts=candle_ts, symbol=symbol,
                decision='floor_sl_pct',
                reason=f'sl_dist={_orig_sl_pct:.3f}% widened to min={_effective_min_sl:.3f}%',
                balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                preset_name=preset_name, scenario=_active_scenario_name,
            )

        # max_sl_pct — two behaviours, chosen per symbol by sl_clamp_enabled.
        #
        # OFF (default, and what has shipped since 2026-07-16): a stop wider than the
        # cap rejects the signal.
        #
        # ON: clamp the stop to the cap and trade it. Mirrors the SL floor above, which
        # has always widened rather than rejected. Clamping only ever reduces risk, and
        # everything below — the ATR floor and min_profit_loss_ratio/sl_adjust_to_rr —
        # still runs on the clamped value, so the trade is taken only if it satisfies
        # the preset's own geometry.
        if not preset_settings.sl_clamp_enabled:
            if preset_settings.max_sl_pct > 0 and sl_dist_pct > preset_settings.max_sl_pct:
                dl_record(
                    dl_path, candle_ts=candle_ts, symbol=symbol,
                    decision='skip_max_sl_pct',
                    reason=f'sl_dist={sl_dist_pct:.2f}% > max={preset_settings.max_sl_pct}%',
                    balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                    preset_name=preset_name, scenario=_active_scenario_name,
                )
                return 0.0
        else:
            _pre_clamp_sl, _pre_clamp_pct = sl, sl_dist_pct
            sl, sl_dist_pct, _sl_clamped = clamp_sl_to_max(
                entry, sl, sl_dist_pct, side, preset_settings.max_sl_pct)
            if _sl_clamped:
                logger.info(
                    f"[{symbol}] SL clamped: {_pre_clamp_pct:.3f}% → {sl_dist_pct:.3f}%"
                    f" ({_pre_clamp_sl:.6g} → {sl:.6g}) preset={preset_name}"
                )
                dl_record(
                    dl_path, candle_ts=candle_ts, symbol=symbol,
                    decision='clamp_max_sl_pct',
                    reason=(f'sl_dist={_pre_clamp_pct:.2f}% clamped to '
                            f'max={preset_settings.max_sl_pct}%'),
                    balance=balance, leverage=0, efficiency_score=_eff_for_dl,
                    preset_name=preset_name, scenario=_active_scenario_name,
                )

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
            return _skip('skip_degenerate_sl', 'loss distance is zero', preset_name,
                         _eff_for_dl)

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
                _floor_sl = max(
                    preset_settings.min_sl_pct if preset_settings.min_sl_pct > 0 else 0.0,
                    _global_min_sl,
                )
                if _floor_sl > 0 and _new_sl_pct < _floor_sl:
                    dl_record(
                        dl_path, candle_ts=candle_ts, symbol=symbol,
                        decision='skip_sl_adjust_too_tight',
                        reason=f'adjusted_sl={_new_sl_pct:.3f}% < min_sl={_floor_sl:.3f}%',
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

        # Symbol-level directional streak block — always enforced regardless of current preset.
        # Blocks are keyed by symbol:side so a preset switch never resets protection.
        _sk = f"{symbol}:{side}"
        if candle_ts > 0 and _streak_blocked.get(_sk, 0) >= candle_ts:
            return _skip('skip_loss_streak_cooldown',
                         f'{side} loss streak cooldown active', preset_name, _eff_for_dl)

        # Preset-specific guards (global pause and zone SL — only when preset opts in)
        if preset_settings.loss_streak_max > 0 and candle_ts > 0:
            _pk = f"{symbol}:{preset_name or 'default'}"
            if _global_pause_until.get(_pk, 0) >= candle_ts:
                return _skip('skip_global_pause',
                             'preset global pause active', preset_name, _eff_for_dl)
            if preset_settings.zone_sl_max > 0 and _zone_sl_block.get(_sk, 0) >= candle_ts:
                return _skip('skip_zone_sl_cooldown',
                             f'{side} zone SL cooldown active', preset_name, _eff_for_dl)

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

        # Hard cap: no single trade may exceed max_trade_pct% of the total deployable budget.
        # Prevents one symbol from consuming all capital when its BGF fraction is temporarily 100%.
        # bypass_pct_cap=True skips this for TATS single-signal mode (full budget is intentional).
        _max_trade_pct = float(risk_cfg.get("max_trade_pct", 0.0))
        if _max_trade_pct > 0 and not bypass_pct_cap:
            _deployable_total = risk_manager.get_deployable_budget()
            _max_alloc = _deployable_total * _max_trade_pct / 100.0
            if trade_margin > _max_alloc > margin:
                logger.debug(
                    f"[{symbol}] max_trade_pct cap: trade_margin {trade_margin:.2f} → {_max_alloc:.2f} "
                    f"({_max_trade_pct:.0f}% of deployable {_deployable_total:.2f})"
                )
                trade_margin = _max_alloc

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
        if order_executor._last_opened_preset.get(symbol) != preset_name and not _virtual_only:
            await order_executor.check_symbols_on_exchange([symbol])
            if order_executor.get_state(symbol) != OrderState.IDLE:
                return _skip('skip_already_open',
                             'exchange still reports a position/order for this symbol',
                             preset_name, _eff_for_dl)

        # 2% buffer ensures step-rounding never drops notional below the exchange floor (-4164 guard).
        quantity = trade_margin * actual_lev * 1.02 / entry

        bh_record(bh_path, balance=balance, trigger='order_open',
                  symbol=symbol, leverage=actual_lev)

        precision = best.getPrecision() if hasattr(best, 'getPrecision') else 0.0

        # The "Before" figure for this trade's close notification. The TTL cache is the
        # right source here and costs nothing: on_candle_close() called
        # _get_fresh_balance() moments ago in this same handler, so the cached figure is
        # both fresh and genuinely pre-trade. It is carried on the OpenOrder so it can
        # never be confused with a post-close read.
        #
        # The post-close "After" figure is the one that must NOT come from this cache —
        # see _read_wallet_now(), which exists because the cache once served a pre-close
        # balance as the settled one. `balance` above is the allocated per-symbol trade
        # cap, not the wallet.
        wallet_before = await _get_fresh_balance()

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
            trail_activation_pct=preset_settings.trail_activation_pct,
            trail_min_distance_pct=preset_settings.trail_min_distance_pct,
            level=best.getLevel(),
            signal_type=best.getType().value,
            balance_at_open=balance,
            signal_level=best.getLevel() or 0,
            precision_score=precision or 0.0,
            scenario=_active_scenario_name,
            wallet_at_open=wallet_before,
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

    _streak_state_path = _PROJECT_ROOT / "data" / f"streak_state_{current_mode}.json"

    def _save_streak_state() -> None:
        try:
            tmp = _streak_state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"loss_streak": _loss_streak, "streak_blocked": _streak_blocked}))
            tmp.replace(_streak_state_path)
        except Exception as _e:
            logger.warning(f"Failed to save streak state: {_e}")

    def _load_streak_state() -> None:
        try:
            if _streak_state_path.exists():
                data = json.loads(_streak_state_path.read_text())
                _loss_streak.update(data.get("loss_streak", {}))
                _streak_blocked.update(data.get("streak_blocked", {}))
                logger.info(f"Streak state loaded: {len(_streak_blocked)} active blocks")
        except Exception as _e:
            logger.debug(f"Failed to load streak state: {_e}")

    def _update_loss_streak(c: dict, ts: int) -> None:
        """Update symbol-level directional loss streak and zone SL cooldown state after order closes."""
        sym = c['symbol']
        pname = c.get('preset_name', 'default')
        side = c.get('side', '')
        overrides = all_presets.get(pname, {})
        sym_s = sym_settings.get(sym, first_settings)
        ps = dataclasses.replace(sym_s, **overrides)
        tf_ms = _tf_to_ms(timeframe)
        # Symbol-level key — preset switch never resets the streak counter
        sk = f"{sym}:{side}"

        # ── Loss streak cooldown ───────────────────────────────────────────
        # Count every real loss regardless of preset configuration so that
        # switching presets mid-session never resets an accumulated streak.
        # Blocking only fires when the active preset opts in via loss_streak_max.
        other_sk = f"{sym}:{'SELL' if side == 'BUY' else 'BUY'}"
        is_loss = c.get('result') == 'loss' or (
            c.get('result') in ('trail', 'partial') and c.get('pnl_usdt', 0.0) < 0
        )
        if is_loss:
            cnt = _loss_streak.get(sk, 0) + 1
            _last_loss_ts[sk] = ts
            _loss_streak[sk] = cnt
            if ps.loss_streak_max > 0 and cnt >= ps.loss_streak_max:
                _streak_blocked[sk] = ts + ps.loss_streak_cooldown_candles * tf_ms
                _loss_streak[sk] = 0
                logger.info(
                    f"[{sym}] {side} loss streak {ps.loss_streak_max}/{ps.loss_streak_max} "
                    f"(preset={pname}) — {side} blocked for {ps.loss_streak_cooldown_candles} candles"
                )
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
        _save_streak_state()

        # ── Zone SL cooldown (only on actual SL hits, not trail/partial) ──
        if ps.zone_sl_max > 0 and c.get('result') == 'loss':
            sl_price = c.get('sl', 0.0)
            if sl_price > 0:
                tol = ps.duplicate_skip_pct / 100.0
                prev_level = _zone_sl_level.get(sk, 0.0)
                same_zone = (
                    prev_level > 0
                    and abs(sl_price - prev_level) / max(prev_level, 1e-10) <= tol
                )
                if same_zone:
                    _zone_sl_count[sk] = _zone_sl_count.get(sk, 0) + 1
                else:
                    _zone_sl_count[sk] = 1
                    _zone_sl_level[sk] = sl_price
                if _zone_sl_count[sk] >= ps.zone_sl_max:
                    _zone_sl_block[sk] = ts + ps.zone_sl_cooldown_candles * tf_ms
                    _zone_sl_count[sk] = 0
                    _zone_sl_level[sk] = 0.0
                    logger.info(
                        f"[{sym}] {side} zone SL blocked after {ps.zone_sl_max} consecutive hits "
                        f"at SL≈{sl_price:.4f} (preset={pname}) — "
                        f"blocked for {ps.zone_sl_cooldown_candles} candles"
                    )

    async def _refresh_klines_bg(symbol: str, count: int, stagger: float) -> None:
        if stagger > 0:
            await asyncio.sleep(stagger)
        for _attempt in (1, 2):
            try:
                await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, count)
                return
            except RateLimited as _rl:
                # The guard suppressed this, so no request was made and the ban was not
                # extended. The common cause is the settling window right after a probe
                # succeeded: gap refreshes for all 15 symbols land within a second of
                # the candle close and meet a closed gate. Without this retry the gap
                # would stay unfilled for a whole candle. One retry only, and it goes
                # back through the guard — if the ban is still real it is suppressed
                # again rather than extending anything.
                if _attempt == 2:
                    logger.debug(
                        f"[{symbol}] Kline refresh still rate-limited after retry "
                        f"({_rl.remaining:.0f}s left) — will retry next candle"
                    )
                    return
                await asyncio.sleep(_KLINE_RETRY_AFTER_S)
            except Exception as _e:
                logger.debug(f"[{symbol}] Background kline refresh failed: {_e}")
                return

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

        # Sync registry from disk so dashboard changes (disable, weight edits)
        # take effect within one candle without a bot restart.
        _roster_added, _roster_removed = symbol_registry.reload_from_disk()

        # Roster changes: subscribe additions, unsubscribe removals, no restart needed.
        #
        # on_candle_close fires once per SYMBOL, so this is guarded to once per candle —
        # without that, the kline bootstrap below would re-fetch for every symbol in the
        # roster on every candle (22 API calls a candle at the current count).
        #
        # Written as a reconciliation of desired-vs-present rather than acting on the
        # (added, removed) delta, so it is idempotent and self-healing: a symbol held
        # back because it still had an open position is dropped on a later candle once
        # it is flat, even though the delta reported it removed only once.
        _this_candle = int(kline[0]) if kline else 0
        if _symbols_synced_at[0] != _this_candle:
            _symbols_synced_at[0] = _this_candle
            _desired = symbol_registry.get_symbols()
            _to_add = [s for s in _desired
                       if s not in sym_settings or s not in analyzers]
            _to_drop = [s for s in list(sym_settings) if s not in set(_desired)]
            _roster_changed = False

            for _sym in _to_add:
                try:
                    _s = load_settings(_sym)
                    _s.trading_mode = current_mode
                    # Bootstrap is a REST call and is behind the rate-limit guard, so it
                    # returns nothing during a ban. Adding the symbol anyway would leave
                    # an empty analyzer and no retry, because the next pass would see it
                    # as already present — so skip and pick it up on a later candle.
                    _kl = await asyncio.to_thread(
                        feed.load_klines, _sym, timeframe, 1500)
                    if not _kl:
                        logger.warning(
                            f"[{_sym}] added to the registry but no klines available "
                            f"yet (rate-limit ban or bad symbol) — retrying next candle")
                        continue
                    _az = Analyzer(_s.swing_neighbours, RecommendationEngine(_s))
                    _az.build_from_klines(_kl)
                except Exception as _add_exc:
                    logger.warning(
                        f"[{_sym}] could not be bootstrapped ({_add_exc}) — "
                        f"retrying next candle")
                    continue
                sym_settings[_sym] = _s
                analyzers[_sym] = _az
                _roster_changed = True
                logger.info(
                    f"[{_sym}] subscribed without a restart — "
                    f"{len(_kl)} klines bootstrapped")

            _open_real = {} if _virtual_only else order_executor.get_open_orders()
            for _sym in _to_drop:
                if _sym in _open_real:
                    # Never stop watching a symbol we hold. The exchange stop-loss would
                    # survive, but with no candles arriving the bot would never record
                    # the close. _subscribed_symbols() keeps it on the socket too.
                    logger.warning(
                        f"[{_sym}] removed from the registry but still holds an open "
                        f"real position — staying subscribed until it is flat")
                    continue
                sym_settings.pop(_sym, None)
                analyzers.pop(_sym, None)
                _roster_changed = True
                logger.info(f"[{_sym}] unsubscribed without a restart")

            if _roster_changed:
                feed.request_reconnect()

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
                    if not _virtual_only:
                        await order_executor.fetch_leverage_brackets(active_syms)
                    for sym in active_syms:
                        min_notionals[sym] = await order_executor.get_min_notional(sym)
                    logger.info("Daily exchange-info refresh complete")
                except Exception as exc:
                    logger.warning(f"Daily exchange-info refresh failed (will retry next cycle): {exc}")
                await order_executor.sync_positions_with_exchange()

        _sym_disabled = symbol_registry.is_disabled(symbol)

        if symbol not in sym_settings or symbol not in analyzers:
            return

        settings = sym_settings[symbol]
        analyzer = analyzers[symbol]

        if _sym_disabled:
            # Keep analyzer current and run virtual simulation for disabled symbols so we
            # collect preset performance data to inform future re-enablement decisions.
            # Placement logic is skipped entirely.
            recs = analyzer.add_candle(kline)
            _locked_preset = locked_presets_for(risk_cfg, mode_manager.current_mode).get(symbol)
            await virtual_order_simulator.on_candle_close(
                symbol=symbol,
                analyzer=analyzer,
                best_preset_name=virtual_tracker.best_preset(symbol),
                base_settings=settings,
                locked_preset=_locked_preset,
                virtual_only=True,
            )
            # Persist the candle for disabled symbols too. This branch used to return
            # before the append below, so their caches never advanced from the stream and
            # every restart re-fetched all of them: measured 2026-09-08, 65 load_klines
            # calls across 8 restarts — ~8 per restart, exactly the 8 disabled symbols.
            # They receive WS candles like any other symbol, so there is nothing to fetch.
            try:
                await asyncio.to_thread(feed.append_kline, symbol, timeframe, kline)
            except Exception as _cache_exc:
                logger.debug(f"[{symbol}] WS kline cache append failed: {_cache_exc}")
            return

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

        # Persist the candle the WebSocket just delivered. append_kline() existed but
        # was never called, so the on-disk cache only advanced when a REST refresh ran:
        # every restart needed a 15-call gap fetch to recover candles the stream had
        # already given us, and during an API ban the cache froze entirely, so a restart
        # mid-ban came back with stale history. With this the cache tracks the stream,
        # so candle history no longer depends on REST at all.
        #
        # Off the event loop: 15 symbols x ~640KB read+parse+write per candle close
        # would otherwise stall it for most of a second. After add_candle(), so a disk
        # problem can never delay or affect a trading decision — and swallowed for the
        # same reason.
        try:
            await asyncio.to_thread(feed.append_kline, symbol, timeframe, candle_to_add)
        except Exception as _cache_exc:
            logger.debug(f"[{symbol}] WS kline cache append failed: {_cache_exc}")

        # Fetch balance once per candle batch (TTL shared across all symbols)
        balance = await _get_fresh_balance()
        if balance > 0:
            risk_manager.update_balance(balance)

        # Efficiency-ranked cross-symbol placement loop
        candle_ts = int(kline[0]) if kline else 0
        # symbol -> preset chosen by substitution this candle. Cleared every candle so
        # a stale entry can never leak into a later order. _try_place_order reads this
        # instead of re-deriving best_preset, which would silently discard the
        # substitution and place the order on the WRONG preset's settings.
        _substituted_preset.clear()
        candidates = []
        # A virtual-only instance evaluates signals and runs the simulator, but never
        # places a real order. Emptying the candidate source skips the whole placement
        # pass without restructuring the allocation logic below — the flag only ever
        # removes work, it never alters an order.
        _placement_symbols = [] if _virtual_only else symbol_registry.get_symbols()
        for sym in _placement_symbols:
            if symbol_registry.is_disabled(sym):
                continue
            if symbol_registry.is_symbol_paused(sym):
                continue
            if _active_scenario_name != "tats" and symbol_registry.get_weight(sym) == 0.0:
                continue
            if order_executor.get_state(sym) != OrderState.IDLE:
                continue
            # TATS: no automatic quality gates — explicit registry disable is the only control.
            # is_tats_eligible() and is_virtual_only() are checked in non-TATS paths only.
            _sym_az = analyzer if sym == symbol else analyzers.get(sym)
            if _sym_az is None:
                continue
            best_sym = _sym_az.get_best_recommendation()
            _bp = None
            if best_sym is None:
                # Base-settings engine lacks hl_buy/lh_sell flags — fall back to the symbol's
                # best preset's own overrides so those signal types are not silently missed.
                # locked_presets takes precedence over VirtualTracker selection.
                _bp = (locked_presets_for(risk_cfg, mode_manager.current_mode).get(sym)
                       or virtual_tracker.best_preset(sym))
                _bp_ovr = all_presets.get(_bp or '', {})
                if _bp_ovr:
                    best_sym = _sym_az.get_recommendation_for_preset(_bp_ovr)
            _substituted_from = None
            # Per-symbol override, falling back to the global default — same pattern as
            # max_loss_usdt_per_symbol. Substitution value varies enormously by symbol
            # (measured +12.22/trade on INJUSDT vs -0.03 on MEMEUSDT), so a single
            # global switch would be wrong for most of the book either way.
            _sub_on = risk_cfg.get("substitution_enabled_per_symbol", {}).get(
                sym, risk_cfg.get("substitution_enabled", False))
            # A manual lock means "use exactly this preset" — never substitute away
            # from it. Without this guard the recommendation would come from the
            # substitute while _try_place_order (which takes the locked branch) would
            # size and manage the order with the LOCKED preset's settings, so entry/TP/SL
            # and the trail/partial rules would come from two different presets.
            _is_locked_sym = sym in locked_presets_for(risk_cfg, mode_manager.current_mode)
            if best_sym is None and _sub_on and not _is_locked_sym:
                # The best preset produced no recommendation. Fall back to ONE rank
                # down — the next preset that is both live-proven (tier 1) and
                # currently profitable. Deliberately a single rank: measured on real
                # data, substituting one rank was positive (+173.82 USDT over 53
                # extra orders) while going two or more was destructive (-10.28,
                # then -53.43), driven by EIGENUSDT's rank 3 at -259.
                _sub = virtual_tracker.substitute_preset(sym, _bp)
                if _sub:
                    _sub_ovr = all_presets.get(_sub, {})
                    if _sub_ovr:
                        best_sym = _sym_az.get_recommendation_for_preset(_sub_ovr)
                        if best_sym is not None:
                            _substituted_from, _bp = _bp, _sub
                            _substituted_preset[sym] = _sub
                            logger.info(
                                f"[{sym}] Substituted preset {_substituted_from!r} -> {_sub!r} "
                                f"(best had no recommendation)"
                            )
                            analysis_log.record(
                                'preset_substituted', symbol=sym, best_preset=_substituted_from,
                                used_preset=_sub, candle_ts=candle_ts,
                            )
            if best_sym is None:
                # The symbol is dropped here because neither base settings nor the
                # BEST preset produced a recommendation (and substitution either is
                # disabled or also found nothing). This is the population the
                # substitution feature serves; logging it is what makes the
                # opportunity measurable. At most one record per symbol per candle.
                # Same per-candle guard as the discard log above: the candidate loop
                # re-runs per closing symbol, so without this the event would be
                # written ~8x per symbol per candle and inflate the analysis log.
                if _norec_logged.get(sym) != candle_ts:
                    _norec_logged[sym] = candle_ts
                    analysis_log.record(
                        'no_recommendation', symbol=sym, best_preset=_bp,
                        candle_ts=candle_ts, scenario=_active_scenario_name,
                        substitution_enabled=bool(_sub_on),
                    )
                continue
            raw_score = virtual_tracker.get_efficiency_score(sym)
            # In BGF, apply symbol_weights as a multiplier so the rebalancer
            # can dampen allocation for symbols that are losing in practice.
            #
            # Defaults to 0.0, not 1.0: a symbol in symbol_registry.json but ABSENT from
            # symbol_weights has been given no allocation by anyone, and must not trade
            # real money on that basis. This mattered little while adding a symbol needed
            # a restart someone would notice; with the roster hot-reloaded, an SSH edit to
            # symbol_registry.json alone would otherwise start real orders on the next
            # candle. Measured 2026-09-09: all 22 live symbols had explicit entries, so
            # this default was unreachable — it is a guard, not a behaviour change.
            # Virtual simulation is unaffected and still runs for every subscribed symbol.
            if not scenario.uses_weight_allocation:
                sym_weight = risk_cfg.get("symbol_weights", {}).get(sym, 0.0)
                raw_score = raw_score * max(0.0, sym_weight)
            candidates.append((sym, best_sym, sym_settings.get(sym, settings), raw_score))

        candidates.sort(key=lambda x: x[3], reverse=True)
        if _active_scenario_name == "tats":
            # Zero-score candidates are dropped here. In practice this is almost
            # always symbol_weights[sym] == 0 zeroing the score at the multiply
            # above — a deliberate config choice, but previously invisible: a whole
            # day could pass with valid signals and no orders and nothing in the
            # log said why. Record it so "why did nothing trade?" is answerable.
            _dropped = [c for c in candidates if c[3] <= 0.0]
            candidates = [c for c in candidates if c[3] > 0.0]
            for _sym, _best, _s, _score in _dropped:
                # This loop re-runs for EVERY symbol whose candle closed, so without a
                # guard each discarded symbol is logged once per active symbol per
                # candle — 8x in practice, which made discard lines 72% of bot.log.
                # Log once per symbol per candle; the information is identical.
                if _discard_logged.get(_sym) == candle_ts:
                    continue
                _discard_logged[_sym] = candle_ts
                _w = risk_cfg.get("symbol_weights", {}).get(_sym, 0.0)
                _why = (f"symbol_weights={_w} zeroes the score" if _w == 0
                        else f"efficiency score {_score:.2f} <= 0")
                logger.info(f"[{_sym}] Signal discarded — {_why}")
                dl_record(
                    dl_path, candle_ts=candle_ts, symbol=_sym,
                    decision='skip_zero_score', reason=_why,
                    balance=0.0, leverage=0,
                    efficiency_score=virtual_tracker.get_efficiency_score(_sym),
                    preset_name='', scenario=_active_scenario_name,
                )
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
        elif _active_scenario_name == "tats":
            # TATS: single eligible signal → full deployable (max_trade_pct bypassed);
            # multiple eligible signals → BGF proportional allocation.
            deployable = risk_manager.get_deployable_budget()
            n = len(candidates)
            if n == 0:
                virtual_order_simulator.set_candle_alloc_context(False, {})
            elif n == 1:
                sym, best, sym_s, _ = candidates[0]
                # tats_min_weight: low-weight symbols in single-signal mode get a
                # weight-proportional cap instead of the full deployable budget.
                # Prevents w=1 symbols from accidentally consuming the whole account.
                #
                # Two weight sources exist and they disagree. Candidacy is decided by
                # risk_config.symbol_weights; the registry keeps its own, unrelated set,
                # and the registry is what sizes this branch.
                #
                # That breaks when the registry has NO weight for a symbol risk_config
                # funds. Measured 2026-09-09: REZUSDT held risk_config weight 13 — the
                # second largest allocation — against registry weight 0, so _sym_frac was
                # 0, sym_cap was 0.00, and every single-candidate candle was refused with
                # 'balance=0.00 < margin=1.00' (42 log events over 6 candles, ~2 lost
                # signal episodes). ETHFIUSDT (risk_config 9, registry 0) was primed to do
                # the same — together 41% of allocated weight.
                #
                # A registry weight of 0 on a funded symbol is missing data, not an
                # instruction to size it at zero, so risk_config supplies the share in
                # THAT case only. Doing it unconditionally (the first attempt at this fix)
                # silently resized the four symbols that already worked, cutting TIAUSDT
                # 421 -> 156 — a 63% cut to the most productive symbol under the current
                # locked presets (+92.09 over 9 real orders, 56% win). Narrow on purpose.
                #
                # The DECISION to take this path deliberately still reads the registry.
                # Switching it to risk_config would put every weight above tats_min_weight
                # and hand each sole candidate the entire deployable budget — measured, a
                # jump from ~421 to ~2107 margin, roughly 5x the observed position size.
                # That is a risk change, not a bug fix, so it is left alone; see TODO.md.
                _tats_min_w = float(risk_cfg.get('tats_min_weight', 0.0))
                _cfg_ws = risk_cfg.get('symbol_weights', {}) or {}
                if _tats_min_w > 0 and symbol_registry.get_weight(sym) < _tats_min_w:
                    _active_ws = [
                        s for s in symbol_registry.get_symbols()
                        if not symbol_registry.is_disabled(s) and not symbol_registry.is_symbol_paused(s)
                    ]
                    _reg_total = sum(symbol_registry.get_weight(s) for s in _active_ws)
                    _sym_frac = ((symbol_registry.get_weight(sym) / _reg_total)
                                 if _reg_total > 0 else 1.0)
                    if _sym_frac <= 0.0:
                        _total_w = sum(float(_cfg_ws.get(s, 0.0)) for s in _active_ws)
                        _sym_frac = ((float(_cfg_ws.get(sym, 0.0)) / _total_w)
                                     if _total_w > 0 else 0.0)
                    sym_cap = deployable * _sym_frac
                    virtual_order_simulator.set_candle_alloc_context(False, {sym: _sym_frac})
                    await _try_place_order(sym, best, sym_s, sym_cap, candle_ts, trade_cap=sym_cap)
                else:
                    virtual_order_simulator.set_candle_alloc_context(False, {sym: 1.0}, bypass_pct_cap=True)
                    await _try_place_order(sym, best, sym_s, deployable, candle_ts,
                                           trade_cap=deployable, bypass_pct_cap=True)
            else:
                total_score = sum(max(0.0, s) for _, _, _, s in candidates)
                bgf_fractions = {
                    sym: (max(0.0, s) / total_score if total_score > 0 else 1.0 / n)
                    for sym, _, _, s in candidates
                }
                virtual_order_simulator.set_candle_alloc_context(False, bgf_fractions)
                deployed = 0.0
                # Renormalise as the loop advances. Sizing each candidate from the
                # STATIC total let a candidate that was later refused hold its slice
                # hostage. Measured 2026-09-09 19:45 (deployable 2107.27, score =
                # efficiency x weight):
                #
                #   INJUSDT    428.24 x 14 = 5995  92.4%  cap ~1947  REFUSED, sl 16.37%>10%
                #   SOLUSDT     40.32 x  8 =  322   5.0%  cap  ~105  placed at 96
                #   ETHFIUSDT   19.07 x  9 =  172   2.7%  cap   ~56  placed at 51
                #
                # 1947 USDT reserved and never used, and the two symbols that DID trade
                # took 8% of the budget between them. Across the decision log, 8 of 9
                # multi-candidate candles that produced a placement wasted budget this
                # way, mean 56%. It is not random: the top-scoring symbol is often the
                # one with the widest stop, so it wins the allocation and then fails the
                # SL gate (skip_max_sl_pct did this on TIAUSDT 3x, INJUSDT 2x).
                #
                # `remaining` already tracked the unspent budget — only the fraction was
                # stale. max_trade_pct and max_order_notional_usdt (2000 = 400 margin at
                # 5x) still bound every order downstream, so this cannot produce a
                # position larger than the bot already places daily.
                _remaining_score = total_score
                for _i, (sym, best, sym_s, score) in enumerate(candidates):
                    remaining = max(0.0, deployable - deployed)
                    if remaining <= 0:
                        break
                    _s = max(0.0, score)
                    _left = len(candidates) - _i
                    sym_cap = (
                        remaining * _s / _remaining_score
                        if _remaining_score > 0
                        else (remaining / _left if _left > 0 else 0.0)
                    )
                    _remaining_score = max(0.0, _remaining_score - _s)
                    if sym_cap <= 0:
                        continue
                    used = await _try_place_order(sym, best, sym_s, remaining, candle_ts,
                                                  trade_cap=sym_cap)
                    deployed += used
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
            # Same renormalisation as the TATS branch above, and for the same reason —
            # a refused candidate must not hold its slice hostage. BGF is not the active
            # scenario, so this is fixed alongside rather than left as a latent bug.
            _remaining_score = total_score
            for _i, (sym, best, sym_s, score) in enumerate(candidates):
                remaining = max(0.0, deployable - deployed)
                if remaining <= 0:
                    break
                _s = max(0.0, score)
                _left = len(candidates) - _i
                if _remaining_score > 0:
                    sym_cap = remaining * _s / _remaining_score
                else:
                    sym_cap = remaining / _left if _left > 0 else 0.0
                _remaining_score = max(0.0, _remaining_score - _s)
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
            # Uncached: the TTL cache may hold a pre-close figure from the placement
            # pass earlier in this same handler. 0.0 means unavailable -> reported n/a.
            wallet_after = await _read_wallet_now()
            bh_record(
                bh_path,
                balance=wallet_after if wallet_after > 0 else await _get_fresh_balance(),
                trigger='order_close',
                symbol=c['symbol'], leverage=c.get('leverage', 1),
                pnl_usdt=c.get('pnl_usdt'),
            )
            # Advance the running balance FIRST: _before_after() reads it back and
            # derives `before` by subtracting this trade's PnL.
            risk_manager.apply_realised(c.get('pnl_usdt'))
            # A genuine uncached read is the moment to true up and record the drift.
            if wallet_after and wallet_after > 0:
                _drift = risk_manager.reconcile(wallet_after)
                if _drift is not None and abs(_drift) >= 1.0:
                    logger.warning(
                        f"Balance drift {_drift:+.2f} USDT on reconcile "
                        f"(calculated vs exchange) after {c['symbol']} close"
                    )
            _bb, _ba, _est = _before_after(c, wallet_after)
            notifier.notify_trade_close(
                symbol=c['symbol'],
                side=c.get('side', ''),
                pnl_usdt=c.get('pnl_usdt', 0.0),
                entry_price=c.get('fill_entry_price') or c.get('entry_price', 0.0),
                close_price=c.get('close_price', 0.0),
                preset_name=c.get('preset_name', ''),
                balance_before=_bb,
                balance_after=_ba,
                fee_usdt=c.get('fee_usdt', 0.0),
                balance_estimated=_est,
            )

        _locked_preset = locked_presets_for(risk_cfg, mode_manager.current_mode).get(symbol)
        # Whether the real-order slot is occupied at all — an order placed on this
        # candle, or a position still open from an earlier one. When it is free, the
        # simulator opens a rank-1 virtual order for the preset that would have traded,
        # so a blocked signal still produces a data point instead of vanishing.
        #
        # The open-position half matters: this was candle-scoped, so from the next candle
        # onwards the stand-in ran alongside a live real trade on the same preset. The
        # same condition already excludes the symbol from real-order candidates below.
        _real_slot_busy = (
            _placed_this_candle.get(symbol) == candle_ts
            or order_executor.get_state(symbol) != OrderState.IDLE
        )
        # The preset holding the open real position, so the simulator can keep that one
        # preset out of the virtual pools while its real trade runs. Scoped to the preset,
        # not the symbol: every other preset keeps collecting comparison data.
        _open_real = order_executor.get_open_orders().get(symbol)
        _real_preset = _open_real.preset_name if _open_real else None
        await virtual_order_simulator.on_candle_close(
            symbol=symbol,
            analyzer=analyzer,
            best_preset_name=virtual_tracker.best_preset(symbol),
            base_settings=settings,
            locked_preset=_locked_preset,
            real_slot_busy=_real_slot_busy,
            real_preset=_real_preset,
        )

        # save_risk_config() every candle. A virtual-only instance must never retune
        # the trading bot's real symbol allocation from its own virtual results.
        if not _virtual_only:
            weight_rebalancer.on_candle_close(candle_ts)

        export(
            symbol, timeframe, mode_manager.current_mode,
            analyzer.get_current_price(), analyzer.get_trend(),
            analyzer.get_klines(), recs, analyzer.get_all_points(), best_for_this,
            mirror=_virtual_only,
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
            # Uncached: the TTL cache may hold a pre-close figure from the placement
            # pass earlier in this same handler. 0.0 means unavailable -> reported n/a.
            wallet_after = await _read_wallet_now()
            bh_record(
                bh_path,
                balance=wallet_after if wallet_after > 0 else await _get_fresh_balance(),
                trigger='order_close',
                symbol=c['symbol'], leverage=c.get('leverage', 1),
                pnl_usdt=c.get('pnl_usdt'),
            )
            # Advance the running balance FIRST: _before_after() reads it back and
            # derives `before` by subtracting this trade's PnL.
            risk_manager.apply_realised(c.get('pnl_usdt'))
            # A genuine uncached read is the moment to true up and record the drift.
            if wallet_after and wallet_after > 0:
                _drift = risk_manager.reconcile(wallet_after)
                if _drift is not None and abs(_drift) >= 1.0:
                    logger.warning(
                        f"Balance drift {_drift:+.2f} USDT on reconcile "
                        f"(calculated vs exchange) after {c['symbol']} close"
                    )
            _bb, _ba, _est = _before_after(c, wallet_after)
            notifier.notify_trade_close(
                symbol=c['symbol'],
                side=c.get('side', ''),
                pnl_usdt=c.get('pnl_usdt', 0.0),
                entry_price=c.get('fill_entry_price') or c.get('entry_price', 0.0),
                close_price=c.get('close_price', 0.0),
                preset_name=c.get('preset_name', ''),
                balance_before=_bb,
                balance_after=_ba,
                fee_usdt=c.get('fee_usdt', 0.0),
                balance_estimated=_est,
            )

        virtual_closed = await virtual_order_simulator.check_prices(symbol, price)
        for vc in virtual_closed:
            # Rank 1 is recorded but NOT scored yet. It is the new pool that fills in the
            # signals the real slot could not take; letting it into preset_efficiency
            # immediately would change preset selection at the same moment the data
            # changes, leaving no baseline to compare against. Flipping this on is a
            # separate decision — see docs/specs/2026-09-07-rank1-statistics-gap.md.
            if vc.get('rank') == 1:
                continue
            # Bookkeeping exits, not strategy outcomes: 'promoted_to_real' frees a
            # preset so the real-order slot can use it, 'max_age' closes a position that
            # would otherwise hold its slot forever, and 'manual_close' is a human
            # pressing the button on this page. None says anything about whether the
            # preset works, so none belongs in the ranking.
            if vc.get('result') in ('promoted_to_real', 'max_age', 'manual_close'):
                continue
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
        if not _virtual_only:
            await order_executor.fetch_leverage_brackets(current_symbols)
        for symbol in current_symbols:
            klines_new = await asyncio.to_thread(feed.refresh_klines, symbol, timeframe, 1500)
            analyzers[symbol].build_from_klines(klines_new)
        virtual_tracker = VirtualTracker(
            mode=target_mode,
            orders_path=_PROJECT_ROOT / "data" / f"virtual_orders_{target_mode}.json",
            efficiency_path=_PROJECT_ROOT / "data" / f"preset_efficiency_{target_mode}.json",
            get_min_trades=_get_min_trades,
        )
        for sym in current_symbols:
            bt_path = _PROJECT_ROOT / "dashboard" / "public" / backtest_results_name(
                sym, mode_manager.current_mode, _virtual_only)
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

    async def on_close_order(payload: dict) -> dict:
        """Close one open position on request from the dashboard.

        payload: {symbol, kind: 'real'|'virtual', rank?, mode?}

        Returns {'ok': bool, 'error': str|None}. A missing position is ok=False with a
        readable reason rather than an exception, so pressing the button twice reads as a
        no-op instead of a failure.
        """
        sym = str(payload.get('symbol') or '').upper()
        kind = str(payload.get('kind') or '').lower()
        want_mode = payload.get('mode')

        if not sym:
            return {'ok': False, 'error': 'No symbol given'}
        # The command file is shared and only this instance polls it, so a request aimed
        # at the other instance must never be applied to our own position of that name.
        if want_mode and want_mode != mode_manager.current_mode:
            return {'ok': False, 'error': (
                f"This bot runs '{mode_manager.current_mode}'; the request was for "
                f"'{want_mode}'. Only the running instance can close its positions.")}

        if kind == 'real':
            res = await order_executor.close_order(sym, reason='manual_close')
            if res is None:
                return {'ok': False, 'error': f'{sym} has no open real position'}
            logger.info(
                f"[{sym}] MANUAL CLOSE (real) from dashboard: "
                f"{res.get('preset_name')} pnl={res.get('pnl_usdt', 0):.2f}")
            # notify() writes the system log and sends Telegram. 'warning' level on
            # purpose: real money was moved by hand, so it should stand out in the log
            # next to automated closes. close_order() has already sent the usual
            # trade-close message with the numbers.
            notifier.notify(
                'warning', f"Manual close — {sym}",
                (f"Closed by hand from the Trades page.\n"
                 f"Type:   real\n"
                 f"Side:   {res.get('side')}\n"
                 f"Preset: {res.get('preset_name')}\n"
                 f"PnL:    {res.get('pnl_usdt', 0):.2f} USDT"),
                'dashboard')
            _write_open_positions()
            return {'ok': True, 'error': None}

        if kind == 'virtual':
            try:
                rank = int(payload.get('rank'))
            except (TypeError, ValueError):
                return {'ok': False, 'error': 'A virtual close needs a rank'}
            px = _live_price(sym)
            if px <= 0:
                return {'ok': False, 'error': f'No current price for {sym} yet'}
            res = await virtual_order_simulator.close_open_manually(sym, rank, px)
            if res is None:
                return {'ok': False, 'error': f'{sym} has nothing open at rank {rank}'}
            logger.info(
                f"[{sym}] MANUAL CLOSE (virtual rank {rank}) from dashboard: "
                f"{res.get('preset_name')}")
            notifier.notify(
                'info', f"Manual close — {sym} (virtual)",
                (f"Closed by hand from the Trades page.\n"
                 f"Type:   virtual, rank {rank}\n"
                 f"Preset: {res.get('preset_name')}\n"
                 f"Price:  {px}"),
                'dashboard')
            _write_open_positions()
            return {'ok': True, 'error': None}

        return {'ok': False, 'error': f"Unknown kind '{kind}' — expected real or virtual"}

    async def on_stop_bot() -> None:
        current_symbols = symbol_registry.get_symbols()
        _restart_path = _PROJECT_ROOT / 'data' / f'restart_positions_{mode_manager.current_mode}.json'
        _close_on_stop = bool(risk_cfg.get('close_positions_on_stop', False))
        if not _close_on_stop:
            saved = order_executor.save_open_positions(_restart_path)
            if saved:
                logger.info(f"close_positions_on_stop=false — {saved} position(s) saved, skipping market close")
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    virtual_order_simulator.close_all_open(current_symbols, feed),
                    order_executor.close_all_orders_at_market() if _close_on_stop else asyncio.sleep(0),
                ),
                timeout=45.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Graceful shutdown timed out after 45s — forcing exit")
        _write_open_positions()
        notifier.notify("info", "Bot stopped", "Clean shutdown via dashboard", "main")
        sys.exit(0)

    # Register SIGTERM handler so `docker stop` / deploy triggers the same graceful
    # shutdown as the dashboard Stop button (closes virtual + real orders before exit).
    asyncio.get_running_loop().add_signal_handler(
        signal.SIGTERM,
        lambda: asyncio.create_task(on_stop_bot()),
    )

    # ── Task setup ─────────────────────────────────────────────────────── #

    _load_streak_state()

    # mode_manager DELETES the command file after reading it — consume-once. A Stop
    # taken by the virtual instance would report success while the trading bot kept
    # running, so only the trading bot polls the channel.
    _poll_task = None
    if not _virtual_only:
        _poll_task = asyncio.create_task(
            mode_manager.poll_loop(on_switch_mode=on_switch_mode, on_stop_bot=on_stop_bot,
                                   on_close_order=on_close_order)
        )
    _hb_task = asyncio.create_task(
        _heartbeat_loop(mode_manager, started_at, symbol_registry)
    )
    async def _balance_prefetch_loop() -> None:
        """Read the wallet mid-candle so the candle-close read is a cache hit.

        This is the only call that has been getting banned. Reading it 7.5 minutes into
        the candle instead of at the boundary takes it off the moment the shared edge is
        saturated, and costs nothing extra: it is the same one call per candle, just at a
        quieter time.
        """
        period = feed._timeframe_to_ms(timeframe) / 1000.0
        offset = period / 2.0          # 15m candle -> :07:30, :22:30, :37:30, :52:30
        while True:
            now = time.time()
            nxt = (now // period) * period + offset
            if nxt <= now:
                nxt += period
            await asyncio.sleep(max(1.0, nxt - now))
            try:
                bal = await order_executor.fetch_account_balance()
                if bal > 0:
                    _balance_cache_inner[0] = (bal, time.monotonic())
                    # INFO, not debug: the root logger runs at INFO, and a mitigation you
                    # cannot see working is one you cannot confirm. Once per candle, so
                    # 96 lines/day against the thousands the discard path already writes.
                    logger.info(f"Balance pre-fetched mid-candle: {bal:.2f} USDT")
                else:
                    # Banned or failed. The boundary read will fetch, as it used to — so
                    # this candle loses the mitigation and is worth seeing.
                    logger.warning(
                        "Mid-candle balance pre-fetch returned 0 — candle-close read will "
                        "go to the network"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"Mid-candle balance pre-fetch failed: {exc}")

    # Virtual-only has no credentials and sizes from the rank pools, not this balance.
    _balance_task = None
    if not _virtual_only:
        _balance_task = asyncio.create_task(_balance_prefetch_loop())

    def _subscribed_symbols() -> list[str]:
        """The registry roster, plus any symbol still holding an open real position.

        Removing a symbol from the registry must not orphan a live position. The
        exchange stop-loss would survive, but the websocket would stop delivering
        candles for it, so the bot would never see the fill close. Such a symbol stays
        on the socket until it is flat; the roster reconciliation in on_candle_close
        keeps its analyzer alive for exactly as long, and drops it once closed.
        """
        syms = symbol_registry.get_symbols()
        if _virtual_only:
            return syms
        try:
            held = [s for s in order_executor.get_open_orders() if s not in syms]
        except Exception:
            return syms
        return syms + held

    _watchdog_task = asyncio.create_task(
        feed.start_watchdog(
            get_symbols=_subscribed_symbols,
            timeframe=timeframe,
            on_candle_close=on_candle_close,
            on_price_update=on_price_update,
        )
    )
    # Telegram delivers each update exactly once. Two pollers on one token means your
    # commands land on a coin flip — and do_pause/do_resume/do_enable mutate the
    # shared symbol registry.
    _menu_task = None
    if not _virtual_only:
        _menu_task = asyncio.create_task(telegram_menu.run())

    # Only the mirror self-exits on a bot-mode change. The primary keeps its mode until
    # it is restarted deliberately — nothing may restart the bot that holds positions.
    _mirror_task = None
    if _virtual_only:
        _mirror_task = asyncio.create_task(_mirror_watch(mode_manager))

    try:
        await feed.stream_combined(
            get_symbols=_subscribed_symbols,
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
        # _menu_task is None on a virtual-only instance, which does not run the
        # Telegram menu — filter before cancelling or shutdown raises AttributeError.
        _tasks = [t for t in (_poll_task, _hb_task, _watchdog_task, _menu_task,
                              _mirror_task, _balance_task) if t is not None]
        for t in _tasks:
            t.cancel()
        for t in _tasks:
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
