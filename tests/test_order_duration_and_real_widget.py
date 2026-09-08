"""Order duration everywhere, and a cross-symbol real-orders widget.

Duration answers a question the page could not: SOLUSDT sat 16 candles with its trail
never arming, and nothing on screen said how long it had been there. It is shown for
open and closed orders alike, so the two read the same way.

The widget is deliberately cross-symbol. The main table is scoped to the selected
symbol, and real orders are few (79 in a month) while being the only ones that move
money — so the useful overview is every symbol at once, not a filtered copy of the table
below it.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / 'dashboard/app/trades/page.tsx').read_text()
WIDGET = (ROOT / 'dashboard/components/RealOrdersWidget.tsx').read_text()
ROUTE = (ROOT / 'dashboard/app/api/trades/real/route.ts').read_text()
DT = (ROOT / 'dashboard/lib/datetime.ts').read_text()


class TestTheDurationHelpers:
    def test_they_are_shared_not_duplicated(self):
        """One implementation, so the widget and the table can never disagree."""
        assert 'export function fmtDuration' in DT
        assert 'export function durationSeconds' in DT

    def test_an_open_order_measures_to_now(self):
        i = DT.index('export function durationSeconds')
        assert 'Date.now()' in DT[i:i + 700]

    def test_missing_input_is_not_zero(self):
        """A zero duration would read as "just opened" on an order with no timestamp."""
        i = DT.index('export function fmtDuration')
        assert 'return \'—\'' in DT[i:i + 400]

    def test_it_scales_past_a_day(self):
        i = DT.index('export function fmtDuration')
        blk = DT[i:i + 700]
        assert all(u in blk for u in ('s`', 'm`', 'h ', 'd '))


class TestTheMainTable:
    """The ORDERS table specifically. The page has more than one table, so everything
    here is anchored on the orders header rather than the first <tbody> on the page —
    an earlier version sliced the presets table and reported a phantom mismatch."""

    def _header(self) -> str:
        i = PAGE.index('<th className="py-2 pr-3">Preset</th>')
        return PAGE[i:PAGE.index('</thead>', i)]

    def _tbody(self) -> str:
        i = PAGE.index('<th className="py-2 pr-3">Preset</th>')
        start = PAGE.index('<tbody>', PAGE.index('</thead>', i))
        return PAGE[start:PAGE.index('</tbody>', start)]

    def test_there_is_a_duration_column(self):
        assert '>Duration</th>' in self._header()

    def test_the_column_count_still_matches_every_row(self):
        """levelCell() renders one <td> that is not literal in the row markup, so the
        expected literal count is one less than the header count."""
        head = self._header()
        n_th = head.count('<th')
        blocks = re.split(r'\{/\* ── ', self._tbody())[1:]
        assert blocks, 'row blocks not found'
        for b in blocks:
            label = b.split('──')[0].strip()
            total = b.count('<td') + b.count('levelCell(')
            assert total == n_th, f'{label}: {total} cells vs {n_th} headers'

    def test_open_and_closed_rows_both_show_it(self):
        assert self._tbody().count('fmtDuration(') == 3, 'expected one per row type'

    def test_a_closed_order_measures_to_its_close(self):
        assert 'durationSeconds(order.open_time, closedAt)' in PAGE

    def test_an_open_order_measures_to_now(self):
        assert 'durationSeconds(order.open_time)' in PAGE


class TestTheWidget:
    def test_it_sits_under_the_symbol_selector(self):
        i = PAGE.index('<SymbolPicker {...pickerProps} />', PAGE.index('return ('))
        j = PAGE.index('<RealOrdersWidget', i)
        between = PAGE[i:j]
        assert '<div className="flex flex-wrap items-center gap-3">' not in between, \
            'the widget should come before the page heading row'

    def test_it_follows_the_viewed_instance(self):
        assert 'mode={dataMode}' in PAGE

    def test_it_shows_duration(self):
        assert 'fmtDuration(o.duration_s)' in WIDGET

    def test_it_marks_open_orders(self):
        assert 'NOW' in WIDGET and 'is_open' in WIDGET

    def test_an_open_order_shows_its_unrealised_result(self):
        assert 'unrealized_pnl_usdt' in WIDGET

    def test_clicking_a_row_selects_that_symbol(self):
        assert 'onSelectSymbol' in WIDGET and 'onSelectSymbol={setSymbol}' in PAGE

    def test_it_says_when_the_open_figures_were_written(self):
        """They come from a once-per-candle snapshot; implying they are live would mislead."""
        assert 'open_updated_at' in WIDGET and 'once per candle' in WIDGET

    def test_empty_and_error_states_exist(self):
        assert 'No real orders' in WIDGET and 'unavailable' in WIDGET


class TestTheApi:
    def test_it_spans_every_symbol(self):
        assert 'registeredSymbols()' in ROUTE

    def test_it_includes_positions_open_now(self):
        """Those live in the snapshot — the per-symbol files are only written on close."""
        assert 'open_positions_' in ROUTE

    def test_it_computes_duration_server_side(self):
        assert 'duration_s' in ROUTE

    def test_open_orders_sort_first(self):
        i = ROUTE.index('rows.sort(')
        assert 'is_open' in ROUTE[i:i + 300]

    def test_totals_cover_the_whole_book_not_just_the_page(self):
        i = ROUTE.index('net_pnl_usdt:')
        assert 'closed.reduce' in ROUTE[i:i + 120]

    def test_the_limit_is_bounded(self):
        assert 'MAX_LIMIT' in ROUTE

    def test_the_mode_parameter_picks_the_instance(self):
        assert "requested === 'test' || requested === 'live'" in ROUTE
