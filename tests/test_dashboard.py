"""Dashboard (Next.js) checks, plus the replay API the dashboard calls.

There is no TypeScript test runner, so most sections assert that load-bearing snippets
are present in the dashboard sources (read via tests.factories.src).

Sections:
  manual_order_close                TestTheClosePaths ... TestTheUi (7 classes)
  order_duration_and_real_widget    TestTheDurationHelpers, TestTheMainTable, TestTheWidget, TestTheApi
  TestLockPresetTargetsViewedInstance  locks apply to the instance on screen
  TestBookkeepingTooltip            non-strategy closes are explained in a tooltip
  TestWinPctExcludesBookkeeping     win rate is a share of decided outcomes
  TestReplayApi                     replay_api.replay()
"""
import inspect
import json
import re
from pathlib import Path

import pytest

from bot.mode_manager import ModeManager
from bot.order_executor import OrderExecutor
from bot.virtual_order_simulator import VirtualOrderSimulator
from tests.factories import src

MAIN = src('main.py')
PAGE = src('dashboard/app/trades/page.tsx')


# ────────────────────────── manual_order_close ────────────────────────── #
# Closing one position by hand from the Trades page.
#
# The button market-closes a live position, so the parts that matter are: it is recorded
# distinctly from a strategy exit, a second press is a no-op rather than a second close,
# and a request aimed at the other instance is refused instead of being applied to this
# instance's position of the same name.
#
# Spec: docs/specs/2026-09-08-manual-order-close.md

CLOSE_ROUTE = src('dashboard/app/api/orders/close/route.ts')


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
    @pytest.mark.parametrize('snippet', [
        pytest.param('only be closed by that instance', id='it_refuses_the_wrong_instance'),
        pytest.param("kind must be 'real' or 'virtual'", id='it_validates_kind'),
        pytest.param('rank required', id='a_virtual_close_requires_a_rank'),
    ])
    def test_the_route_contains(self, snippet):
        assert snippet in CLOSE_ROUTE

    def test_a_timeout_is_not_reported_as_success(self):
        """It may have closed; claiming failure or success would both be wrong."""
        assert 'pending: true' in CLOSE_ROUTE and 'did not answer in time' in CLOSE_ROUTE


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

    @pytest.mark.parametrize('snippet', [
        pytest.param('cannot be undone', id='there_is_small_explanatory_text'),
        pytest.param('closableHere', id='the_button_is_disabled_for_the_other_instance'),
        pytest.param('closeError', id='a_failed_close_is_shown'),
    ])
    def test_the_page_contains(self, snippet):
        assert snippet in PAGE

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


# ────────────────────────── order_duration_and_real_widget ────────────────────────── #
# Order duration everywhere, and a cross-symbol real-orders widget.
#
# Duration answers a question the page could not: SOLUSDT sat 16 candles with its trail
# never arming, and nothing on screen said how long it had been there. It is shown for
# open and closed orders alike, so the two read the same way.
#
# The widget is deliberately cross-symbol. The main table is scoped to the selected
# symbol, and real orders are few (79 in a month) while being the only ones that move
# money — so the useful overview is every symbol at once, not a filtered copy of the table
# below it.

WIDGET = src('dashboard/components/RealOrdersWidget.tsx')
REAL_ROUTE = src('dashboard/app/api/trades/real/route.ts')
DT = src('dashboard/lib/datetime.ts')


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
    @pytest.mark.parametrize('snippet', [
        pytest.param('registeredSymbols()', id='it_spans_every_symbol'),
        pytest.param('duration_s', id='it_computes_duration_server_side'),
        pytest.param('MAX_LIMIT', id='the_limit_is_bounded'),
        pytest.param("requested === 'test' || requested === 'live'",
                     id='the_mode_parameter_picks_the_instance'),
    ])
    def test_the_route_contains(self, snippet):
        assert snippet in REAL_ROUTE

    def test_it_includes_positions_open_now(self):
        """Those live in the snapshot — the per-symbol files are only written on close."""
        assert 'open_positions_' in REAL_ROUTE

    def test_open_orders_sort_first(self):
        i = REAL_ROUTE.index('rows.sort(')
        assert 'is_open' in REAL_ROUTE[i:i + 300]

    def test_totals_cover_the_whole_book_not_just_the_page(self):
        i = REAL_ROUTE.index('net_pnl_usdt:')
        assert 'closed.reduce' in REAL_ROUTE[i:i + 120]


# ────────────────────────── lock_preset_targets_viewed_instance ────────────────────────── #

LOCK_ROUTE = src('dashboard/app/api/risk/lock-preset/route.ts')
# Mode validation and the bot-mode fallback live in the shared per-mode config helper
# since the risk config split per mode (docs/specs/2026-09-26-per-mode-risk-config.md).
HELPER = src('dashboard/app/api/_risk-config.ts')


class TestLockPresetTargetsViewedInstance:
    """Locking a preset must apply to the instance whose table is on screen.

    The Trades page reads locks for the VIEWED instance (Primary/Shadow toggle) but the
    route wrote to whichever mode the bot happens to be running. So clicking the padlock in
    the Shadow view silently locked the preset for test — the wrong instance — and the icon
    never updated, because the page re-read live.

    Ranks are already fully independent between instances: only 1 of 15 symbols shares a #1
    preset between test and live. Locks have to follow the same separation.
    """

    def test_the_route_accepts_a_mode_from_the_caller(self):
        assert 'requestedMode' in LOCK_ROUTE

    def test_the_route_validates_the_mode(self):
        """An arbitrary string would create a junk key in locked_presets."""
        assert 'modeOr(requestedMode)' in LOCK_ROUTE
        assert "m === 'test' || m === 'live'" in HELPER

    def test_the_route_falls_back_to_the_bot_mode(self):
        """Callers that do not specify an instance must keep working."""
        assert 'isMode(requested) ? requested : botMode()' in HELPER

    def test_the_page_sends_the_viewed_instance(self):
        i = PAGE.index("'/api/risk/lock-preset'")
        body = PAGE[i:i + 700]
        assert 'mode: dataMode' in body, 'the lock would land on the wrong instance'

    def test_the_page_reads_and_writes_the_same_instance(self):
        """Read and write must agree, or the icon lies about what happened."""
        assert 'lockedPresetsFor(config, dataMode)' in PAGE
        i = PAGE.index("'/api/risk/lock-preset'")
        assert 'dataMode' in PAGE[i:i + 700]


# ────────────────────────── bookkeeping_tooltip ────────────────────────── #

SIM = src('bot/virtual_order_simulator.py')

STRATEGY = {'win', 'partial', 'trail', 'loss'}


def _dashboard_labels() -> set:
    """Keys of the BOOKKEEPING_LABELS map in the page."""
    block = PAGE.split('BOOKKEEPING_LABELS: Record<string, string> = {', 1)[1]
    block = block.split('}', 1)[0]
    return set(re.findall(r'^\s*([a-z_]+):', block, re.M))


def _emitted_reasons() -> set:
    """Every close reason the simulator can write, read from the code that writes it."""
    evicted = set(re.findall(r"_evict\([^)]*?'([a-z_]+)'\s*\)", SIM))
    direct = set(re.findall(r"'result':\s*'([a-z_]+)'", SIM))
    return {r for r in evicted | direct if r not in STRATEGY}


class TestBookkeepingTooltip:
    """Closes that were not strategy exits must be explained, not silently dropped.

    A preset showing "3v" with nothing in Wins/Part/Trail/Losses is not a display bug: those
    three closes were bookkeeping — the rank table reshuffled, or a restart force-closed them.
    The trade count includes them; no outcome column does. The tooltip says which.

    A column was rejected in favour of a tooltip: these reasons are diagnostic, and a seventh
    numeric column would compete with the outcomes that actually matter.
    """

    def test_the_bot_emits_reasons_the_dashboard_can_name(self):
        """The whole point is naming them; an unlabelled reason renders as a raw enum."""
        missing = _emitted_reasons() - _dashboard_labels()
        assert not missing, f'no dashboard label for: {sorted(missing)}'

    def test_the_known_reasons_are_actually_covered(self):
        """Guards the regexes above from silently matching nothing and passing."""
        assert {'max_age', 'closed_early'} <= _emitted_reasons()

    def test_strategy_results_are_exactly_the_four_outcomes(self):
        block = PAGE.split('STRATEGY_RESULTS = [', 1)[1].split(']', 1)[0]
        assert set(re.findall(r"'([a-z]+)'", block)) == STRATEGY

    def test_counting_excludes_strategy_results(self):
        """Otherwise wins would be double-reported as bookkeeping."""
        assert 'STRATEGY_RESULTS.includes(r)' in PAGE

    def test_the_count_is_derived_not_enumerated(self):
        """A reason added to the bot must still appear, labelled or not."""
        assert 'bookkeeping[r] = (bookkeeping[r] ?? 0) + 1' in PAGE

    def test_the_trades_cell_carries_the_tooltip(self):
        i = PAGE.index('{tradesLabel}')
        assert 'title={bookTip}' in PAGE[i - 500:i]

    def test_rows_without_bookkeeping_get_no_tooltip(self):
        """An empty title renders an empty grey box on hover."""
        fn = PAGE.split('function bookkeepingTooltip', 1)[1].split('\nfunction ', 1)[0]
        assert 'return undefined' in fn


# ────────────────────────── winpct_excludes_bookkeeping ────────────────────────── #

def _winpct_line() -> str:
    for ln in PAGE.splitlines():
        if 'const winPct' in ln:
            return ln
    raise AssertionError('winPct is gone')


class TestWinPctExcludesBookkeeping:
    """Win rate must be a share of decided outcomes, not of every close.

    The denominator was realCount + virtualCount, which counts bookkeeping closes -- a
    reshuffle or a restart force-close. A preset with 40 reshuffles and 10 real exits read
    about 4x worse than it performed, and unevenly: reshuffle counts vary hugely by rank, so
    two presets with identical strategy records could show very different win rates.

    Display only -- Python's preset_efficiency drives selection -- but it is the number a
    human reads when deciding what to lock.
    """

    def test_the_denominator_is_decided_outcomes(self):
        assert 'decided' in _winpct_line()

    def test_the_denominator_is_not_the_raw_trade_count(self):
        """totalTrades includes bookkeeping closes."""
        assert 'totalTrades' not in _winpct_line()

    def test_decided_sums_exactly_the_four_strategy_outcomes(self):
        line = next(ln for ln in PAGE.splitlines() if 'const decided' in ln)
        assert set(re.findall(r'\b(wins|partials|trails|losses)\b', line)) == {
            'wins', 'partials', 'trails', 'losses'}

    def test_the_numerator_still_excludes_losses(self):
        m = re.search(r'const winPct\s*=.*?\(\((.*?)\)\s*/', _winpct_line())
        assert m and 'losses' not in m.group(1)

    def test_no_division_by_zero(self):
        """A preset with only bookkeeping closes has no win rate to show."""
        assert 'decided > 0' in _winpct_line()

    def test_the_trade_count_column_is_unchanged(self):
        """Only the rate is corrected; the count still shows every close, with the tooltip."""
        # The count is rendered from the row, so the Trades column still includes every close.
        assert 'const totalCount = row.realCount + row.virtualCount' in PAGE


# ────────────────────────── replay_api ────────────────────────── #

def _make_klines(n: int, base_price: float = 100.0) -> list:
    """n synthetic 15-minute klines in dashboard JSON format."""
    base_ts = 1_700_000_000  # Unix seconds
    klines = []
    p = base_price
    for i in range(n):
        klines.append({
            'time': base_ts + i * 900,
            'open': round(p, 4),
            'high': round(p + 1.0, 4),
            'low':  round(p - 1.0, 4),
            'close': round(p + 0.5, 4),
        })
        p += 0.1
    return klines


def _write_results(path: Path, symbol: str, klines: list) -> None:
    data = {
        'symbol': symbol,
        'timeframe': '15m',
        'mode': 'testnet',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'current_price': klines[-1]['close'] if klines else 0.0,
        'trend_levels': [],
        'all_points': [],
        'klines': klines,
        'signals': [],
        'best_signal': None,
    }
    (path / f'results_{symbol}.json').write_text(json.dumps(data))


class TestReplayApi:
    """Tests for replay_api.replay()."""

    def test_replay_returns_correct_shape(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
        _write_results(tmp_path, 'TESTUSDT', _make_klines(60))

        result = replay_api.replay('TESTUSDT', 59)

        assert 'trend_levels' in result
        assert 'all_points' in result
        assert 'signals' in result
        assert isinstance(result['trend_levels'], list)
        assert isinstance(result['all_points'], list)
        assert isinstance(result['signals'], list)

    def test_replay_respects_candle_index(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
        _write_results(tmp_path, 'TESTUSDT', _make_klines(200))

        result_small = replay_api.replay('TESTUSDT', 5)
        result_large = replay_api.replay('TESTUSDT', 199)

        assert len(result_large['all_points']) >= len(result_small['all_points'])

    def test_replay_candle_index_beyond_length_uses_all(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
        _write_results(tmp_path, 'TESTUSDT', _make_klines(50))

        result = replay_api.replay('TESTUSDT', 9999)
        assert 'trend_levels' in result

    def test_replay_zero_candles_returns_empty(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
        _write_results(tmp_path, 'TESTUSDT', _make_klines(50))

        assert replay_api.replay('TESTUSDT', -1) == {'trend_levels': [], 'all_points': [], 'signals': []}
        assert replay_api.replay('TESTUSDT', -2) == {'trend_levels': [], 'all_points': [], 'signals': []}

    def test_replay_missing_file_raises(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)

        with pytest.raises(FileNotFoundError):
            replay_api.replay('NONEXISTENT', 10)

    def test_replay_invalid_symbol_raises(self, tmp_path, monkeypatch):
        import replay_api
        monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)

        with pytest.raises(ValueError):
            replay_api.replay('../etc', 10)


class TestRealOrdersSwitch:
    """Risk page switch that stops NEW real orders without touching weights (2026-09-29):
    risk_config real_orders_enabled, per mode, saved immediately, applied at the next candle."""

    def test_default_is_on_and_per_mode(self):
        from config.risk_config import DEFAULT_CONFIG, SHARED_KEYS
        assert DEFAULT_CONFIG['real_orders_enabled'] is True
        assert 'real_orders_enabled' not in SHARED_KEYS

    def test_the_bot_empties_the_placement_pass_when_off(self):
        main = src('main.py')
        i = main.index("_real_on = bool(risk_cfg.get('real_orders_enabled', True))")
        block = main[i:i + 1400]
        assert 'if not _real_on:\n            _placement_symbols = []' in block
        assert 'notifier.notify(' in block, 'a change must be announced'
        assert main.index('_real_on = bool(') < main.index('for sym in _placement_symbols:')

    def test_the_switch_saves_the_mode_key_immediately(self):
        comp = src('dashboard/components/risk/RealOrdersSwitch.tsx')
        assert "JSON.stringify({ real_orders_enabled: next })" in comp
        assert '/api/risk?mode=${mode}' in comp and 'role="switch"' in comp and 'aria-checked' in comp

    def test_the_risk_page_shows_it_and_keeps_save_all_consistent(self):
        page = src('dashboard/app/risk/page.tsx')
        assert '<RealOrdersSwitch' in page
        assert 'setLoaded(l => l ? { ...l, real_orders_enabled: v } : l)' in page
