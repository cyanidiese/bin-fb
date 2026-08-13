from bot.mean_reversion import MRConfig, Range, detect_range, MRSignal, mr_signal

def _k(o, h, l, c, t=0):
    return [t, o, h, l, c, 0]

def _oscillating(n=48, lo=100.0, hi=110.0):
    # alternately tags the low and the high -> both boundaries tested
    out = []
    for i in range(n):
        if i % 2 == 0:
            out.append(_k(105, hi, 104, 106, i))   # tags high
        else:
            out.append(_k(105, 106, lo, 104, i))   # tags low
    return out

def test_detect_range_qualifies_on_oscillating_window():
    cfg = MRConfig()
    rng = detect_range(_oscillating(), cfg)
    assert rng is not None
    assert abs(rng.hi - 110.0) < 1e-9 and abs(rng.lo - 100.0) < 1e-9
    assert abs(rng.mid - 105.0) < 1e-9

def test_detect_range_rejects_one_sided_window():
    # Genuine one-sided window: the top is tagged every candle, but the low is
    # reached only ONCE (below min_touches=2) -> not a confirmed oscillating range.
    cfg = MRConfig()
    kl = []
    for i in range(48):
        if i == 0:
            kl.append(_k(105, 110, 100, 109, i))   # the ONLY candle reaching the low
        else:
            kl.append(_k(109, 110, 108, 109, i))   # hugs the top only
    assert detect_range(kl, cfg) is None

def test_detect_range_rejects_breakout_series():
    # steadily rising -> width blows out / boundaries not both re-tested
    cfg = MRConfig()
    kl = [_k(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(48)]
    assert detect_range(kl, cfg) is None

def test_detect_range_needs_full_window():
    cfg = MRConfig()
    assert detect_range(_oscillating(n=10), cfg) is None

def _rng():
    return Range(hi=110.0, lo=100.0, mid=105.0, width=10.0 / 105.0)

def test_mr_signal_sell_on_top_wick_rejection():
    cfg = MRConfig()
    # last candle: pokes above hi (110.2) but closes back inside near top (109.0)
    kl = _oscillating() + [_k(109, 110.2, 108.5, 109.0, 99)]
    sig = mr_signal(kl, _rng(), cfg)
    assert sig is not None and sig.side == 'SELL'
    assert abs(sig.tp - 105.0) < 1e-9            # TP = mid
    assert sig.sl > 110.0                         # SL beyond hi boundary
    assert abs(sig.entry - 109.0) < 1e-9          # entry = close

def test_mr_signal_buy_on_bottom_wick_rejection():
    cfg = MRConfig()
    kl = _oscillating() + [_k(101, 101.5, 99.8, 101.0, 99)]  # pokes below lo, closes inside
    sig = mr_signal(kl, _rng(), cfg)
    assert sig is not None and sig.side == 'BUY'
    assert abs(sig.tp - 105.0) < 1e-9
    assert sig.sl < 100.0

def test_mr_signal_none_when_mid_range():
    cfg = MRConfig()
    kl = _oscillating() + [_k(105, 105.5, 104.5, 105.0, 99)]  # close at mid, no fade
    assert mr_signal(kl, _rng(), cfg) is None

def test_mr_signal_none_without_wick_rejection():
    cfg = MRConfig()
    # closes ABOVE hi (breakout, no rejection back inside) -> no fade
    kl = _oscillating() + [_k(109, 111.0, 108.5, 110.8, 99)]
    assert mr_signal(kl, _rng(), cfg) is None
