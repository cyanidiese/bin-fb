"""Closing one position by hand from the Trades page.

The button market-closes a live position, so the parts that matter are: it is recorded
distinctly from a strategy exit, a second press is a no-op rather than a second close,
and a request aimed at the other instance is refused instead of being applied to this
instance's position of the same name.

Spec: docs/specs/2026-09-08-manual-order-close.md
"""
import inspect
import re
from pathlib import Path

import pytest

from bot.order_executor import OrderExecutor
from bot.virtual_order_simulator import VirtualOrderSimulator
from bot.mode_manager import ModeManager

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'main.py').read_text()
PAGE = (ROOT / 'dashboard/app/trades/page.tsx').read_text()
ROUTE = (ROOT / 'dashboard/app/api/orders/close/route.ts').read_text()


class TestTheClosePaths:
    def test_close_order_takes_a_reason(self):
        sig = inspect.signature(OrderExecutor.close_order)
        assert 'reason' in sig.parameters

    def test_the_default_reason_is_unchanged_for_existing_callers(self):
        """close_all_orders_at_market and the stop path must keep recording market_close."""
        assert inspect.signature(OrderExecutor.close_order).parameters['reason'].default \
            == 'market_close'

    def test_the_recorded_result_is_the_reason(self):
        src = inspect.getsource(OrderExecutor.close_order)
        assert '_record_real_order_close(symbol, order, close_price, reason, pnl)' in src, \
            'a hardcoded result would make a manual close indistinguishable'

    def test_the_virtual_path_exists_and_is_public(self):
        assert hasattr(VirtualOrderSimulator, 'close_open_manually')

    def test_the_virtual_path_records_manual_close(self):
        src = inspect.getsource(VirtualOrderSimulator.close_open_manually)
        assert "'manual_close'" in src

    def test_the_virtual_path_returns_none_when_nothing_is_open(self):
        """So a second press reads as a no-op instead of a phantom success."""
        src = inspect.getsource(VirtualOrderSimulator.close_open_manually)
        assert 'return None' in src


class TestTheCommandChannel:
    def test_poll_loop_accepts_a_close_handler(self):
        assert 'on_close_order' in inspect.signature(ModeManager.poll_loop).parameters

    def test_the_handler_is_optional_so_the_mirror_still_starts(self):
        assert inspect.signature(ModeManager.poll_loop).parameters['on_close_order'].default is None

    def test_the_command_type_is_dispatched(self):
        src = inspect.getsource(ModeManager.poll_loop)
        assert 'close_order' in src

    def test_an_instance_without_a_handler_refuses_rather_than_crashing(self):
        src = inspect.getsource(ModeManager.poll_loop)
        assert 'cannot close orders' in src


class TestTheHandler:
    def _handler(self) -> str:
        i = MAIN.index('async def on_close_order')
        j = MAIN.index('async def on_stop_bot')
        return MAIN[i:j]

    def test_it_is_wired_into_the_poll_loop(self):
        assert 'on_close_order=on_close_order' in MAIN

    def test_a_real_close_passes_manual_close(self):
        assert "close_order(sym, reason='manual_close')" in self._handler()

    def test_it_refuses_a_request_for_the_other_instance(self):
        """The command file is shared and only the primary polls it, so a Shadow-view
        request must not be applied to the primary's position of the same name."""
        h = self._handler()
        assert 'want_mode' in h and 'mode_manager.current_mode' in h

    def test_a_missing_position_is_reported_not_raised(self):
        h = self._handler()
        assert 'no open real position' in h and 'nothing open at rank' in h

    def test_a_virtual_close_needs_a_price(self):
        h = self._handler()
        assert '_live_price(sym)' in h and 'No current price' in h

    def test_every_manual_close_is_logged_three_ways(self):
        h = self._handler()
        assert 'MANUAL CLOSE' in h, 'nothing in bot.log'
        assert 'notifier.notify(' in h, 'nothing in the system log'
        assert "'manual_close'" in h or "reason='manual_close'" in h, 'not in the record'

    def test_the_snapshot_is_rewritten_after_a_close(self):
        """Otherwise the row lingers until the next candle and invites a second press."""
        assert self._handler().count('_write_open_positions()') >= 2


class TestScoringIsNotPolluted:
    def test_manual_close_does_not_feed_preset_efficiency(self):
        """A human pressing a button says nothing about whether the preset works."""
        i = MAIN.index("if vc.get('result') in (")
        assert 'manual_close' in MAIN[i:i + 160]

    def test_the_existing_exclusions_are_still_there(self):
        i = MAIN.index("if vc.get('result') in (")
        blk = MAIN[i:i + 160]
        assert 'promoted_to_real' in blk and 'max_age' in blk


class TestTheSnapshotCarriesTheLiveResult:
    def test_the_bot_computes_the_unrealised_figure(self):
        assert 'def _unrealized(' in MAIN

    def test_both_real_and_virtual_positions_get_it(self):
        i = MAIN.index('def _write_open_positions')
        block = MAIN[i:i + 2600]
        assert block.count('_unrealized(') >= 2

    def test_a_missing_price_yields_null_not_zero(self):
        """A zero would render as a real break-even result."""
        i = MAIN.index('def _unrealized(')
        assert "'current_price': None" in MAIN[i:i + 700]

    def test_the_pct_is_on_margin_not_notional(self):
        i = MAIN.index('def _unrealized(')
        blk = MAIN[i:i + 900]
        assert 'margin' in blk and 'lev' in blk


class TestTheApiRoute:
    def test_it_refuses_the_wrong_instance(self):
        assert 'only be closed by that instance' in ROUTE

    def test_it_validates_kind(self):
        assert "kind must be 'real' or 'virtual'" in ROUTE

    def test_a_virtual_close_requires_a_rank(self):
        assert 'rank required' in ROUTE

    def test_a_timeout_is_not_reported_as_success(self):
        """It may have closed; claiming failure or success would both be wrong."""
        assert 'pending: true' in ROUTE and 'did not answer in time' in ROUTE


class TestTheUi:
    def test_the_badge_says_now(self):
        assert '>\n                    NOW\n' in PAGE or 'NOW\n' in PAGE

    def test_no_live_badge_remains_in_the_orders_table(self):
        assert 'tracking-wide">LIVE<' not in PAGE

    def test_the_badge_carries_the_tooltip(self):
        assert 'title={openPositionTooltip(order, data.open_updated_at)}' in PAGE

    def test_the_tooltip_reports_the_result_so_far(self):
        i = PAGE.index('function openPositionTooltip')
        blk = PAGE[i:i + 2600]
        assert 'WINNING' in blk and 'LOSING' in blk

    def test_the_tooltip_admits_its_age(self):
        """It is written once per candle; a stale figure next to a close button must say so."""
        i = PAGE.index('function openPositionTooltip')
        assert 'as of' in PAGE[i:i + 2600]

    def test_closing_needs_confirmation(self):
        assert 'Close?' in PAGE and 'pendingClose' in PAGE

    def test_only_one_row_can_be_armed_at_a_time(self):
        assert re.search(r'pendingClose[,\]].*useState<string \| null>', PAGE, re.S) \
            or 'useState<string | null>(null)' in PAGE

    def test_there_is_small_explanatory_text(self):
        assert 'cannot be undone' in PAGE

    def test_the_button_is_disabled_for_the_other_instance(self):
        assert 'closableHere' in PAGE

    def test_a_failed_close_is_shown(self):
        assert 'closeError' in PAGE

    def test_the_reload_does_not_reset_the_view(self):
        """Wiping the selected preset and sort order after a close would be a surprise.

        Slices the real function rather than a fixed number of characters — a fixed
        window ran past the closing brace into the effect below, which legitimately does
        reset those, and failed a correct implementation.
        """
        assert 'function loadTrades' in PAGE
        lines = PAGE.splitlines()
        start = next(i for i, l in enumerate(lines) if 'function loadTrades' in l)
        indent = len(lines[start]) - len(lines[start].lstrip())
        end = next(i for i in range(start + 1, len(lines))
                   if lines[i].strip() == '}' and
                   (len(lines[i]) - len(lines[i].lstrip())) == indent)
        body = '\n'.join(lines[start:end + 1])
        assert 'setData' in body, 'premise: it is the loader'
        assert 'setSelectedPreset' not in body
        assert 'setSortKey' not in body
