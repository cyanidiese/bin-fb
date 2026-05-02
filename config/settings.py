import os
from dataclasses import dataclass

from dotenv import load_dotenv

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
    # When True: tighten SL to meet min_profit_loss_ratio instead of skipping the trade.
    sl_adjust_to_rr: bool
    # Max TP distance as % of entry. Trades with wider TP targets are skipped. 0.0 = disabled.
    max_profit_pct: float
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


def load_settings() -> Settings:
    trading_mode = os.getenv('TRADING_MODE', 'testnet').lower()

    if trading_mode not in ('testnet', 'live'):
        raise RuntimeError(f"TRADING_MODE must be 'testnet' or 'live', got: '{trading_mode}'")

    if trading_mode == 'testnet':
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

    symbol = os.getenv('SYMBOL', '')
    if not symbol:
        missing.append('SYMBOL')

    if missing:
        raise RuntimeError(f"Missing required .env variables: {', '.join(missing)}")

    if trading_mode == 'live':
        confirmed = os.getenv('LIVE_MODE_CONFIRMED', '').strip().lower()
        if confirmed != 'yes':
            raise RuntimeError(
                "TRADING_MODE=live requires LIVE_MODE_CONFIRMED=yes in .env. "
                "Set this only after reviewing all risk parameters."
            )

    return Settings(
        trading_mode=trading_mode,
        api_key=api_key,
        api_secret=api_secret,
        symbol=symbol.upper(),
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
        sl_adjust_to_rr=os.getenv('SL_ADJUST_TO_RR', 'false').lower() in ('1', 'true', 'yes'),
        max_profit_pct=float(os.getenv('MAX_PROFIT_PCT', '0.0')),
        correction_weight=float(os.getenv('CORRECTION_WEIGHT', '0.0')),
        loss_streak_max=int(os.getenv('LOSS_STREAK_MAX', '0')),
        loss_streak_cooldown_candles=int(os.getenv('LOSS_STREAK_COOLDOWN_CANDLES', '5')),
        global_pause_trigger_candles=int(os.getenv('GLOBAL_PAUSE_TRIGGER_CANDLES', '0')),
        global_pause_candles=int(os.getenv('GLOBAL_PAUSE_CANDLES', '10')),
        lower_high_sell=os.getenv('LOWER_HIGH_SELL', 'false').lower() in ('1', 'true', 'yes'),
        higher_low_buy=os.getenv('HIGHER_LOW_BUY', 'false').lower() in ('1', 'true', 'yes'),
    )
