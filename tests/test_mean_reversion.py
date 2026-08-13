from bot.mean_reversion import MRConfig, Range, detect_range

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
    # only the high is ever tagged -> not a confirmed range
    cfg = MRConfig()
    kl = [_k(105, 110, 104, 106, i) for i in range(48)]
    assert detect_range(kl, cfg) is None

def test_detect_range_rejects_breakout_series():
    # steadily rising -> width blows out / boundaries not both re-tested
    cfg = MRConfig()
    kl = [_k(100 + i, 101 + i, 99 + i, 100 + i, i) for i in range(48)]
    assert detect_range(kl, cfg) is None

def test_detect_range_needs_full_window():
    cfg = MRConfig()
    assert detect_range(_oscillating(n=10), cfg) is None
