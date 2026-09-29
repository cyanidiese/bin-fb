"""Shared test helpers — imported by the suites, never collected (no test_ prefix).

ROOT / src(): the repository root and a cached reader for source files, for the checks
on code that has no runnable harness (dashboard TypeScript, wiring in main.py).
"""
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot.order_executor import OrderExecutor
from bot.virtual_order_simulator import VirtualOrderSimulator

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def src(rel: str) -> str:
    """Text of a repository file, read once per session."""
    return (ROOT / rel).read_text()


def make_executor(with_feed=False):
    settings = MagicMock()
    settings.partial_take_pct = 0.0
    settings.trailing_stop_pct = 0.0
    risk_manager = MagicMock()
    notifier = MagicMock()
    feed = None
    if with_feed:
        feed = MagicMock()
        feed.client = MagicMock()
    with patch('bot.order_executor.load_risk_config', return_value={'consecutive_failure_threshold': 3}):
        return OrderExecutor('test', settings, risk_manager, notifier, data_feed=feed)


def make_vt_with_scores(scores: dict):
    """Return a VirtualTracker mock that returns per-preset efficiency scores."""
    vt = MagicMock()
    vt.get_preset_efficiency.side_effect = lambda symbol, name: scores.get(name, 0.0)
    # Ranking uses the tier-aware key; tier 1 for all keeps value as the tiebreaker.
    vt.get_preset_rank_key.side_effect = lambda symbol, name: (1, scores.get(name, 0.0))
    return vt


def make_simulator(tmp_path, initial_balance=1000.0, rank_max=4, scores=None):
    """
    3 presets ranked preset_a > preset_b > preset_c by default.
    rank_max=4 means we track ranks 2, 3, 4 (rank 1 = real, not tracked here).
    """
    if scores is None:
        scores = {'preset_a': 3.0, 'preset_b': 2.0, 'preset_c': 1.0}
    vt = make_vt_with_scores(scores)
    return VirtualOrderSimulator(
        mode='test',
        all_presets={'preset_a': {}, 'preset_b': {}, 'preset_c': {}},
        project_root=tmp_path,
        get_leverage=lambda sym: 1,
        initial_balance=initial_balance,
        virtual_tracker=vt,
        min_notionals={'BTCUSDT': 5.0},
        rank_max=rank_max,
    )


def make_rec(side='BUY', entry=50000.0, tp=55000.0, sl=48000.0):
    rec = MagicMock()
    rec.getSide.return_value = side
    rec.getEntryPrice.return_value = entry
    rec.getTarget.return_value = tp
    rec.getStop.return_value = sl
    rec.getLevel.return_value = 1
    rec.getType.return_value = MagicMock(value='test_signal')
    return rec


def make_analyzer(price=50000.0):
    a = MagicMock()
    a.get_current_price.return_value = price
    a.get_trend.return_value = MagicMock()
    a.get_klines.return_value = []
    return a


def make_preset_settings():
    """Return a MagicMock preset settings with all filter thresholds disabled (0 = off)."""
    s = MagicMock()
    s.tp_multiplier = 1.0
    s.max_profit_pct = 0.0
    s.min_sl_pct = 0.0
    s.max_sl_pct = 0.0
    s.min_sl_atr_mult = 0.0
    s.atr_lookback = 0
    s.min_profit_loss_ratio = 0.0
    s.sl_adjust_to_rr = False
    s.duplicate_skip_candles = 0
    s.partial_take_pct = 0.0
    s.trailing_stop_pct = 0.0
    s.max_losing_pct = 0.0
    s.max_losing_candles = 0
    s.max_losing_amount_usdt = 0.0
    return s
