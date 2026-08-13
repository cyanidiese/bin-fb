from config.settings import load_settings
from bot.analyzer import Analyzer
from bot.recommendation_engine import RecommendationEngine
from bot.recommendation import RecommendationTypes
import dataclasses


def _k(o, h, l, c, t):
    return [t, o, h, l, c, 0]

def _oscillating_klines(n=60, lo=100.0, hi=110.0):
    # NOTE: deviates from the task brief's literal fixture in two deliberate ways
    # (see task-4-report.md "Fixture bugs found" for the full diagnosis):
    #   1. A tiny deterministic jitter is added to alternating peak/trough candles.
    #      KlineProcessor (swing_neighbours=2) confirms a swing high/low only when a
    #      candle's high/low is *strictly* greater/less than same-parity neighbours
    #      2 candles away. A perfectly repeating hi/lo/hi/lo pattern ties at that
    #      distance, so zero swing points are ever confirmed and
    #      Trend.getCurrentPoint() stays None forever -- the jitter breaks the tie
    #      without changing the window's true hi/lo (both are still touched exactly).
    #   2. The final "poke" candle's high is set to exactly `hi` (touching the
    #      boundary) instead of overshooting it (e.g. 110.2). detect_range()'s
    #      window includes this last candle, so an overshoot becomes the new
    #      window max and shifts hi/mid, breaking the mid=105.0 assertion below.
    #      Touching (not exceeding) hi still satisfies mr_signal's wick-rejection
    #      condition (h > rng.hi - 0.02*span) and fires the SELL fade.
    out = []
    for i in range(n):
        if i % 2 == 0:
            h = hi - 0.3 if (i // 2) % 2 == 0 else hi
            out.append(_k(105, h, 104, 106, i * 900_000))
        else:
            l = lo + 0.3 if ((i - 1) // 2) % 2 == 0 else lo
            out.append(_k(105, 106, l, 104, i * 900_000))
    # final candle: SELL fade (touch hi, close back inside near top)
    out.append(_k(109, hi, 108.5, 109.0, n * 900_000))
    return out

def test_engine_emits_mr_fade_when_enabled_and_range_confirmed():
    s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=True)
    engine = RecommendationEngine(s)
    analyzer = Analyzer(s.swing_neighbours, engine)
    kl = _oscillating_klines()
    analyzer.build_from_klines(kl)
    trend = analyzer.get_trend()
    rec = engine.generate(trend, 109.0, recent_klines=kl)
    assert rec is not None
    assert rec.getType() == RecommendationTypes.MEAN_REVERT_FADE
    assert rec.getSide() == 'SELL'
    assert abs(rec.getTarget() - 105.0) < 1e-6      # TP=mid
    assert rec.getStop() > 110.0                      # SL beyond boundary

def test_engine_ignores_mr_when_disabled():
    s = dataclasses.replace(load_settings('TIAUSDT'), enable_mean_reversion=False)
    engine = RecommendationEngine(s)
    analyzer = Analyzer(s.swing_neighbours, engine)
    kl = _oscillating_klines()
    analyzer.build_from_klines(kl)
    trend = analyzer.get_trend()
    rec = engine.generate(trend, 109.0, recent_klines=kl)
    # MR off -> no MR rec (trend engine may or may not fire, but never MR type)
    assert rec is None or rec.getType() != RecommendationTypes.MEAN_REVERT_FADE
