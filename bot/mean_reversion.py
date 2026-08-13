"""Mean-reversion overlay geometry. PURE functions — no I/O, no global state,
no Trend dependency. Validated by /tmp/s60/mr_refine.py (session 61):
confirmed-range fade, mid config, 7/8 symbols OOS-positive."""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MRConfig:
    window: int = 48
    min_touches: int = 2
    touch_tol: float = 0.12
    band_min: float = 0.02
    band_max: float = 0.16
    decile: float = 0.15
    sl_buf: float = 0.5


@dataclass(frozen=True)
class Range:
    hi: float
    lo: float
    mid: float
    width: float   # (hi - lo) / mid


def _ohlc(k):
    return float(k[1]), float(k[2]), float(k[3]), float(k[4])


def detect_range(klines: list, cfg: MRConfig) -> Optional[Range]:
    """Return a Range iff the trailing `window` candles form a CONFIRMED
    oscillating range: both boundaries tested >= min_touches, width within band."""
    if len(klines) < cfg.window:
        return None
    win = klines[-cfg.window:]
    highs = [float(k[2]) for k in win]
    lows = [float(k[3]) for k in win]
    hi, lo = max(highs), min(lows)
    rng = hi - lo
    if rng <= 0:
        return None
    mid = (hi + lo) / 2.0
    width = rng / mid
    if not (cfg.band_min <= width <= cfg.band_max):
        return None
    top_band = hi - cfg.touch_tol * rng
    bot_band = lo + cfg.touch_tol * rng
    top_touches = sum(1 for h in highs if h >= top_band)
    bot_touches = sum(1 for l in lows if l <= bot_band)
    if top_touches >= cfg.min_touches and bot_touches >= cfg.min_touches:
        return Range(hi=hi, lo=lo, mid=mid, width=width)
    return None
