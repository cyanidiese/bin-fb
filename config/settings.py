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
    swing_neighbours: int
    timezone: str


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
        swing_neighbours=int(os.getenv('SWING_NEIGHBOURS', '2')),
        timezone=os.getenv('TIMEZONE', 'UTC'),
    )
