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


@dataclass(frozen=True)
class MRSignal:
    side: str      # 'BUY' | 'SELL'
    entry: float
    tp: float
    sl: float


def mr_signal(klines: list, rng: Range, cfg: MRConfig) -> Optional[MRSignal]:
    """Fade the extreme of a confirmed range on the last candle.
    Requires a wick-rejection: the candle pokes past the boundary (high near/above
    hi, or low near/below lo) and closes well below its own high (SELL) / above its
    own low (BUY). Matches the validated probe /tmp/s60/mr_refine.py exactly — no
    'closed back inside the range' requirement. TP = range mid,
    SL = boundary +/- sl_buf * range_width."""
    if not klines:
        return None
    _o, h, l, c = _ohlc(klines[-1])
    span = rng.hi - rng.lo
    if span <= 0:
        return None
    pos = (c - rng.lo) / span
    body_tol = 0.15 * (h - l + 1e-12)
    # SELL: near top, poked above hi and closed well below its own high
    if pos >= 1 - cfg.decile:
        if h > rng.hi - 0.02 * span and c < h - body_tol:
            return MRSignal(side='SELL', entry=c, tp=rng.mid, sl=rng.hi + cfg.sl_buf * span)
    # BUY: near bottom, poked below lo and closed well above its own low
    if pos <= cfg.decile:
        if l < rng.lo + 0.02 * span and c > l + body_tol:
            return MRSignal(side='BUY', entry=c, tp=rng.mid, sl=rng.lo - cfg.sl_buf * span)
    return None
