import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Registry file lives in the project root (one level up from config/).
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / 'symbol_registry.json'

load_dotenv()


@dataclass
class Settings:
    trading_mode: str
    api_key: str
    api_secret: str
    symbol: str
    timeframe: str
    kline_limit: int
    kline_cache_limit: int
    swing_neighbours: int
    timezone: str
    # Recommendation engine
    min_swing_points: int
    min_profit_pct: float
    min_profit_loss_ratio: float
    precision_similarity_threshold: float
    projection_lookback: int
    proximity_zone_pct: float
    partial_take_pct: float
    trailing_stop_pct: float
    # Conservative TP: multiply projected TP distance by this before evaluating
    # (e.g. 0.90 = target 90% of full TP, easier to hit, smaller win)
    tp_multiplier: float
    # SL distance filters (% of entry). 0.0 = disabled.
    min_sl_pct: float   # skip trades where SL is closer than this (too noisy)
    max_sl_pct: float   # skip trades where SL is farther than this (too risky)
    # ATR-based SL floor: skip trade if structural SL distance < min_sl_atr_mult × avg candle
    # range of the last atr_lookback candles. 0.0 = disabled. Symbol-agnostic — adapts to each
    # instrument's volatility. Useful for high-price instruments like XAUUSDT where a fixed
    # min_sl_pct threshold is too coarse.
    min_sl_atr_mult: float
    atr_lookback: int
    # When True: tighten SL to meet min_profit_loss_ratio instead of skipping the trade.
    sl_adjust_to_rr: bool
    # Max TP distance as % of entry. Trades with wider TP targets are skipped. 0.0 = disabled.
    max_profit_pct: float
    # Restrict max_profit_pct to specific trend levels. Empty = apply to every level.
    # The TP is projected from the swing structure, so how far it lands depends on which
    # level produced the signal; a cap that is right for one level can be wrong for another.
    max_profit_pct_levels: tuple[int, ...]
    # Correction quality bonus weight in precision scoring. 0.0 = disabled (no change to scoring).
    # When > 0, signals that follow a well-formed correction get a precision boost up to this value.
    correction_weight: float
    # Candle-based directional cooldown. 0 = disabled.
    # After loss_streak_max consecutive losses on one side, block that side for
    # loss_streak_cooldown_candles candles before allowing a new entry.
    loss_streak_max: int
    loss_streak_cooldown_candles: int
    # Global pause: if both BUY and SELL each lost within global_pause_trigger_candles of
    # each other, block ALL new entries for global_pause_candles candles.
    # 0 = disabled. Requires loss_streak_max > 0 to be meaningful.
    global_pause_trigger_candles: int
    global_pause_candles: int
    # When True: in a descending trend with last confirmed swing = LOW, fire a SELL
    # when price approaches the projected lower high from below (within proximity_zone_pct).
    # SL = last confirmed HIGH, TP = supposed_next_low. Default False so existing
    # presets are unaffected.
    lower_high_sell: bool
    # Mirror of lower_high_sell: in an ascending trend with last confirmed swing = HIGH,
    # fire a BUY when price approaches the projected higher low from above.
    # SL = last confirmed LOW, TP = supposed_next_high. Default False.
    higher_low_buy: bool
    # Duplicate-signal skip: if > 0 and an SL-hit order's direction/entry/sl/tp all match
    # the new signal within duplicate_skip_pct%, skip the new signal for this many candles.
    # 0 = disabled.
    duplicate_skip_candles: int
    duplicate_skip_pct: float
    # Early loss exit (0 = disabled)
    # max_losing_pct: close when adverse move reaches this % of SL distance from entry
    max_losing_pct: float
    # max_losing_amount_usdt: close when unrealized loss exceeds this USDT (live/virtual only)
    max_losing_amount_usdt: float
    # max_losing_candles: close after N consecutive candles whose close is on wrong side of entry
    max_losing_candles: int
    # Trailing stop activation gate: trail only fires after price has moved this % from entry.
    # 0.0 = disabled (trail fires on any positive gain, current behaviour).
    trail_activation_pct: float
    # Trailing stop minimum distance: trail distance = max(trail_pct × gained, entry × min_pct / 100).
    # Prevents the trail from sitting too close to price on small gains. 0.0 = disabled.
    trail_min_distance_pct: float
    # Minimum precision score to enter a trade. Candidates whose computed precision is below
    # this value are discarded by the recommendation engine. 0.0 = disabled.
    min_precision_score: float
    # Zone SL cooldown: block re-entry on a side after this many consecutive SL hits at the
    # same SL level (within duplicate_skip_pct% of the previous SL). 0 = disabled.
    zone_sl_max: int
    # Candles to block a side after zone_sl_max consecutive SL hits at the same zone.
    zone_sl_cooldown_candles: int
    # Range position gate for continuation signals (RISING_BELOW_LAST_HIGH / LOWERING_ABOVE_LAST_LOW).
    # BUY blocked if entry is above this fraction of the swing range (0 = low, 1 = high).
    # SELL blocked if entry is below (1 - value). 1.0 = disabled.
    range_position_max: float
    # Minimum number of confirmed swing highs and lows (each) required before
    # getSupposedNextPoints() computes a projection. Lowering to 2 allows
    # projection from a single amplitude observation (less stable, naturally
    # scored lower by _projection_reliability). Default 3 = current behavior.
    min_swing_points_projection: int
    # When True: allow continuation signals even when the parent trend explicitly
    # opposes the signal direction. The alignment penalty in precision scoring
    # still applies — this only disables the hard gate. Default False.
    ignore_parent_alignment: bool
    # Direction gate: 'buy', 'sell', or 'both'. When set to 'buy'/'sell' the engine
    # discards all recommendations for the opposite side before scoring. Lets the
    # virtual tracker back-test direction-specific presets so it can auto-select
    # the best one for the current market regime.
    signal_direction: str
    # When True: on every candle the engine checks whether the generating trend is
    # in a confirmed directional regime (N consecutive lower-highs + lower-lows =
    # 'descending'; higher-highs + higher-lows = 'ascending'). BUY signals are
    # blocked in descending regimes; SELL signals are blocked in ascending regimes.
    # Falls back to 'neutral' (both sides allowed) when data is insufficient.
    trend_regime_filter: bool
    # Number of consecutive swing highs (and lows) that must all be in the same
    # direction before getTrendRegime() declares a regime. Default 3.
    trend_regime_lookback: int
    # Escape hatch only — fetch klines from PRODUCTION while trading on testnet.
    # OFF by default and it should stay off: each mode must read the chart it trades on.
    # Test fills happen at testnet prices, so feeding the strategy production candles
    # would produce statistics that describe neither market, and the live candle stream
    # comes from the testnet WebSocket regardless — mixing the two sources in one cache
    # is incoherent. Kept only as a manual fallback if the testnet kline endpoint is
    # unusable for a sustained period. No effect in live mode (both are production).
    live_klines: bool
    # When True: re-enable the opposing-parent hard reject for continuation signals
    # EVEN IF ignore_parent_alignment is True. This decouples the two behaviours that
    # ignore_parent_alignment used to bundle — escaping post-BoS droughts (keep) vs
    # disabling the opposing-parent block (drop). _parent_is_opposing only fires on a
    # DEFINED, explicitly-opposing bigger trend, so undetermined/thin parents still
    # pass (no drought regression). Evidence: l2_bos_trend (alignment enforced) is
    # +$137.71/47%WR vs sibling l2_bos_entry (alignment ignored) −$100.91/17%WR.
    # Default False. Can also be forced globally via risk_config global_enforce_parent_alignment.
    enforce_parent_alignment_hard: bool = False
    # Mean-reversion overlay settings (session 61)
    enable_mean_reversion: bool = False
    mr_window: int = 48
    mr_min_touches: int = 2
    mr_touch_tol: float = 0.12
    mr_band_min: float = 0.02
    mr_band_max: float = 0.16
    mr_decile: float = 0.15
    mr_sl_buf: float = 0.5


def load_settings(symbol: str | None = None) -> Settings:
    import logging as _logging
    _raw_mode = os.getenv('TRADING_MODE', 'test').lower()
    trading_mode = 'test' if _raw_mode == 'testnet' else _raw_mode
    if trading_mode not in ('test', 'live'):
        raise RuntimeError(f"TRADING_MODE must be 'test' or 'live', got: '{_raw_mode}'")
    if _raw_mode == 'testnet':
        _logging.getLogger(__name__).warning(
            "TRADING_MODE=testnet is deprecated — treating as 'test'. Update your .env."
        )

    if trading_mode == 'test':
        api_key = os.getenv('TESTNET_API_KEY', '')
        api_secret = os.getenv('TESTNET_API_SECRET', '')
        key_names = ('TESTNET_API_KEY', 'TESTNET_API_SECRET')
    else:
        api_key = os.getenv('API_KEY', '')
        api_secret = os.getenv('API_SECRET', '')
        key_names = ('API_KEY', 'API_SECRET')

    missing = []
    if not api_key:
        missing.append(key_names[0])
    if not api_secret:
        missing.append(key_names[1])

    resolved_symbol = symbol.upper() if symbol else os.getenv('SYMBOL', '').upper()
    if not resolved_symbol:
        missing.append('SYMBOL')

    if missing:
        raise RuntimeError(f"Missing required .env variables: {', '.join(missing)}")

    base = Settings(
        trading_mode=trading_mode,
        api_key=api_key,
        api_secret=api_secret,
        symbol=resolved_symbol,
        timeframe=os.getenv('TIMEFRAME', '15m'),
        kline_limit=int(os.getenv('KLINE_LIMIT', '1000')),
        kline_cache_limit=int(os.getenv('KLINE_CACHE_LIMIT', '5000')),
        swing_neighbours=int(os.getenv('SWING_NEIGHBOURS', '2')),
        timezone=os.getenv('TIMEZONE', 'UTC'),
        min_swing_points=int(os.getenv('MIN_SWING_POINTS', '3')),
        min_profit_pct=float(os.getenv('MIN_PROFIT_PCT', '0.5')),
        min_profit_loss_ratio=float(os.getenv('MIN_PROFIT_LOSS_RATIO', '1.5')),
        precision_similarity_threshold=float(os.getenv('PRECISION_SIMILARITY_THRESHOLD', '0.10')),
        projection_lookback=int(os.getenv('PROJECTION_LOOKBACK', '4')),
        proximity_zone_pct=float(os.getenv('PROXIMITY_ZONE_PCT', '10.0')),
        partial_take_pct=float(os.getenv('PARTIAL_TAKE_PCT', '0.0')),
        trailing_stop_pct=float(os.getenv('TRAILING_STOP_PCT', '0.0')),
        tp_multiplier=float(os.getenv('TP_MULTIPLIER', '1.0')),
        min_sl_pct=float(os.getenv('MIN_SL_PCT', '0.0')),
        max_sl_pct=float(os.getenv('MAX_SL_PCT', '0.0')),
        min_sl_atr_mult=float(os.getenv('MIN_SL_ATR_MULT', '0.0')),
        atr_lookback=int(os.getenv('ATR_LOOKBACK', '20')),
        sl_adjust_to_rr=os.getenv('SL_ADJUST_TO_RR', 'false').lower() in ('1', 'true', 'yes'),
        max_profit_pct=float(os.getenv('MAX_PROFIT_PCT', '0.0')),
        max_profit_pct_levels=tuple(
            int(x) for x in os.getenv('MAX_PROFIT_PCT_LEVELS', '').replace(' ', '').split(',') if x
        ),
        correction_weight=float(os.getenv('CORRECTION_WEIGHT', '0.0')),
        loss_streak_max=int(os.getenv('LOSS_STREAK_MAX', '0')),
        loss_streak_cooldown_candles=int(os.getenv('LOSS_STREAK_COOLDOWN_CANDLES', '5')),
        global_pause_trigger_candles=int(os.getenv('GLOBAL_PAUSE_TRIGGER_CANDLES', '0')),
        global_pause_candles=int(os.getenv('GLOBAL_PAUSE_CANDLES', '10')),
        lower_high_sell=os.getenv('LOWER_HIGH_SELL', 'false').lower() in ('1', 'true', 'yes'),
        higher_low_buy=os.getenv('HIGHER_LOW_BUY', 'false').lower() in ('1', 'true', 'yes'),
        duplicate_skip_candles=int(os.getenv('DUPLICATE_SKIP_CANDLES', '0')),
        duplicate_skip_pct=float(os.getenv('DUPLICATE_SKIP_PCT', '2.0')),
        max_losing_pct=float(os.getenv('MAX_LOSING_PCT', '0.0')),
        max_losing_amount_usdt=float(os.getenv('MAX_LOSING_AMOUNT_USDT', '0.0')),
        max_losing_candles=int(os.getenv('MAX_LOSING_CANDLES', '0')),
        trail_activation_pct=float(os.getenv('TRAIL_ACTIVATION_PCT', '0.0')),
        trail_min_distance_pct=float(os.getenv('TRAIL_MIN_DISTANCE_PCT', '0.0')),
        min_precision_score=float(os.getenv('MIN_PRECISION_SCORE', '0.0')),
        zone_sl_max=int(os.getenv('ZONE_SL_MAX', '0')),
        zone_sl_cooldown_candles=int(os.getenv('ZONE_SL_COOLDOWN_CANDLES', '16')),
        range_position_max=float(os.getenv('RANGE_POSITION_MAX', '1.0')),
        min_swing_points_projection=int(os.getenv('MIN_SWING_POINTS_PROJECTION', '3')),
        ignore_parent_alignment=os.getenv('IGNORE_PARENT_ALIGNMENT', 'false').lower() in ('1', 'true', 'yes'),
        signal_direction=os.getenv('SIGNAL_DIRECTION', 'both').lower(),
        trend_regime_filter=os.getenv('TREND_REGIME_FILTER', 'false').lower() in ('1', 'true', 'yes'),
        trend_regime_lookback=int(os.getenv('TREND_REGIME_LOOKBACK', '3')),
        live_klines=os.getenv('LIVE_KLINES', 'false').lower() in ('1', 'true', 'yes'),
        enforce_parent_alignment_hard=os.getenv('ENFORCE_PARENT_ALIGNMENT_HARD', 'false').lower() in ('1', 'true', 'yes'),
        enable_mean_reversion=os.getenv('ENABLE_MEAN_REVERSION', 'false').lower() in ('1', 'true', 'yes'),
        mr_window=int(os.getenv('MR_WINDOW', '48')),
        mr_min_touches=int(os.getenv('MR_MIN_TOUCHES', '2')),
        mr_touch_tol=float(os.getenv('MR_TOUCH_TOL', '0.12')),
        mr_band_min=float(os.getenv('MR_BAND_MIN', '0.02')),
        mr_band_max=float(os.getenv('MR_BAND_MAX', '0.16')),
        mr_decile=float(os.getenv('MR_DECILE', '0.15')),
        mr_sl_buf=float(os.getenv('MR_SL_BUF', '0.5')),
    )

    if symbol is not None:
        base = _apply_symbol_overrides(base, symbol)

    return base


def load_symbols() -> list[str]:
    # Registry file is the authority when it exists and is readable.
    if _REGISTRY_PATH.exists():
        try:
            data = json.loads(_REGISTRY_PATH.read_text())
            symbols = [s.strip().upper() for s in data.get('symbols', []) if s.strip()]
            if symbols:
                return symbols
        except Exception:
            pass  # fall through to .env

    # Fall back to .env
    raw = os.getenv('SYMBOLS', '').strip()
    if raw:
        return [s.strip().upper() for s in raw.split(',') if s.strip()]
    fallback = os.getenv('SYMBOL', '').strip()
    if fallback:
        return [fallback.upper()]
    raise RuntimeError("Neither SYMBOLS nor SYMBOL is set in .env")


def _apply_symbol_overrides(settings: Settings, symbol: str) -> Settings:
    prefix = symbol.upper() + '_'
    field_names = {f.name for f in dataclasses.fields(Settings)}
    overrides: dict = {}
    for env_key, env_val in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        field_name = env_key[len(prefix):].lower()
        if field_name not in field_names:
            continue
        current = getattr(settings, field_name)
        try:
            if isinstance(current, bool):
                overrides[field_name] = env_val.lower() in ('1', 'true', 'yes')
            elif isinstance(current, int):
                overrides[field_name] = int(env_val)
            elif isinstance(current, float):
                overrides[field_name] = float(env_val)
            else:
                overrides[field_name] = env_val
        except (ValueError, TypeError):
            pass
    return dataclasses.replace(settings, **overrides) if overrides else settings


def max_profit_cap_applies(settings: "Settings", level: "int | None") -> bool:
    """Whether the max_profit_pct cap governs a signal at this trend level.

    An empty ``max_profit_pct_levels`` means the cap applies at every level, which
    keeps every existing preset behaving exactly as before. When specific levels are
    listed, a signal whose level is unknown is left UNCAPPED rather than guessed at —
    the cap is scoped to a level we could not confirm, so it is not ours to apply.

    Shared by the live path, the virtual simulator and the backtester so the three
    cannot drift apart.
    """
    if settings.max_profit_pct <= 0:
        return False
    levels = settings.max_profit_pct_levels
    if not levels:
        return True
    return level is not None and level in levels
