"""The rank-1 stand-in must stay empty while the real slot is occupied.

The gate was candle-scoped -- `_placed_this_candle.get(symbol) == candle_ts` -- so it only
suppressed rank 1 on the candle the order was placed. From the next candle onwards the
stand-in opened alongside a live real trade on the same preset. Observed on the server:

    SOLUSDT  l2_trend_buy  REAL     entry 102.97  (08:45 UTC, still open)
    SOLUSDT  l2_trend_buy  Rank #1  entry 103.63  (10:15 UTC, also open)

Two correlated samples of one move, feeding one preset's statistics -- and on a trade we
could never have taken, because only one position per symbol is possible. The same
`get_state(sym) != IDLE` condition already excludes the symbol from real-order candidates.

Renamed to `real_slot_busy`: the old name described the narrow condition and is what made
the wider one easy to miss.
"""
import inspect
import sys
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from bot.virtual_order_simulator import VirtualOrderSimulator

# Reuse the existing simulator harness rather than duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_virtual_order_simulator import (  # noqa: E402
    make_simulator, make_analyzer, make_rec, make_preset_settings,
)

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()


class TestTheGateItself:
    def test_the_parameter_is_named_for_the_slot_not_the_placement(self):
        sig = inspect.signature(VirtualOrderSimulator.on_candle_close)
        assert 'real_slot_busy' in sig.parameters
        assert 'real_order_placed' not in sig.parameters

    def test_main_considers_an_open_position_not_just_this_candle(self):
        i = MAIN.index('_real_slot_busy = (')
        expr = MAIN[i:i + 400]
        assert '_placed_this_candle' in expr, 'lost the same-candle half'
        assert 'OrderState.IDLE' in expr, 'an open position no longer blocks rank 1'
        assert ' or ' in expr, 'the two conditions must be OR-ed'

    def test_it_is_passed_through(self):
        assert 'real_slot_busy=_real_slot_busy' in MAIN


async def _candle(sim, symbol='BTCUSDT', **kw):
    """One candle close, driven the way the existing simulator tests do it.

    Awaited rather than asyncio.run(): run() closes the loop and leaves no current one,
    which made test_telegram_menu fail later in the same process.
    """
    rec = make_rec()
    with patch('bot.virtual_order_simulator.RecommendationEngine') as MockEng, \
         patch('bot.virtual_order_simulator.dataclasses') as mock_dc:
        MockEng.return_value.generate.return_value = rec
        mock_dc.replace.return_value = make_preset_settings()
        await sim.on_candle_close(symbol, make_analyzer(), 'preset_a', MagicMock(), **kw)


@pytest.mark.asyncio
class TestBehaviour:
    async def test_rank_1_opens_when_the_slot_is_free(self, tmp_path):
        """Baseline — without this the suppression tests below prove nothing."""
        sim = make_simulator(tmp_path)
        await _candle(sim, real_slot_busy=False)
        assert 'BTCUSDT' in sim._rank_open[1], 'rank 1 never opens; the rest is vacuous'

    async def test_rank_1_does_not_open_while_the_slot_is_busy(self, tmp_path):
        sim = make_simulator(tmp_path)
        await _candle(sim, real_slot_busy=True)
        assert 'BTCUSDT' not in sim._rank_open[1]

    async def test_an_open_rank_1_position_is_evicted_when_the_slot_becomes_busy(self, tmp_path):
        """This is the observed bug: the stand-in kept running beside a real trade."""
        sim = make_simulator(tmp_path)
        await _candle(sim, real_slot_busy=False)
        assert 'BTCUSDT' in sim._rank_open[1]
        await _candle(sim, real_slot_busy=True)
        assert 'BTCUSDT' not in sim._rank_open[1], 'stand-in ran alongside a real trade'

    async def test_the_other_ranks_are_unaffected(self, tmp_path):
        """Only rank 1 stands in for the real slot; ranks 2+ must keep running."""
        sim = make_simulator(tmp_path, rank_max=4)
        await _candle(sim, real_slot_busy=True)
        assert 'BTCUSDT' in sim._rank_open[2]

    async def test_a_disabled_symbol_still_leaves_rank_1_empty(self, tmp_path):
        """virtual_only symbols put index 0 at rank 2; rank 1 must stay unused."""
        sim = make_simulator(tmp_path)
        await _candle(sim, virtual_only=True)
        assert 'BTCUSDT' not in sim._rank_open[1]
