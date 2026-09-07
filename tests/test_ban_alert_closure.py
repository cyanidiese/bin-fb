"""A restart during a ban must not leave a dangling "ban started" alert.

Guard state is in-memory. On 2026-09-07 the bot was restarted at 14:56 while a block was
armed, so `_clear()` never ran and no "API ban ended" notification was sent. The last
thing Telegram said was "API ban started" at 14:30, for a ban that expired at ~14:48 —
so it read as a 40-minute outage that was not happening. Every deploy during a ban
reproduces this.
"""
from bot.rate_limit_guard import unresolved_ban_endpoints


def _e(title, ts='2026-09-07T10:00:00'):
    return {'timestamp': ts, 'title': title, 'source': 'rate_limit_guard'}


def test_a_started_with_no_ended_is_unresolved():
    entries = [_e('API ban started — testnet', '2026-09-07T14:30:05')]
    assert unresolved_ban_endpoints(entries) == ['testnet']


def test_a_matched_pair_is_resolved():
    entries = [
        _e('API ban started — testnet', '2026-09-07T13:00:00'),
        _e('API ban ended — testnet', '2026-09-07T13:15:04'),
    ]
    assert unresolved_ban_endpoints(entries) == []


def test_only_the_most_recent_state_counts():
    """The real 2026-09-07 sequence: many pairs, then a trailing 'started'."""
    entries = [
        _e('API ban started — testnet', '2026-09-07T13:00:00'),
        _e('API ban ended — testnet', '2026-09-07T13:15:04'),
        _e('API ban started — testnet', '2026-09-07T13:15:05'),
        _e('API ban ended — testnet', '2026-09-07T14:15:04'),
        _e('API ban started — testnet', '2026-09-07T14:30:05'),
    ]
    assert unresolved_ban_endpoints(entries) == ['testnet']


def test_endpoints_are_tracked_independently():
    entries = [
        _e('API ban started — testnet', '2026-09-07T14:00:00'),
        _e('API ban started — production', '2026-09-07T14:05:00'),
        _e('API ban ended — production', '2026-09-07T14:10:00'),
    ]
    assert unresolved_ban_endpoints(entries) == ['testnet']


def test_unrelated_entries_are_ignored():
    entries = [
        _e('Bot stopped'),
        _e('Running obligatory backtest'),
        _e('Low balance warning'),
        _e('BTCUSDT BUY — Win'),
    ]
    assert unresolved_ban_endpoints(entries) == []


def test_entries_out_of_order_are_handled():
    """system_log is append-ordered, but do not depend on it."""
    entries = [
        _e('API ban ended — testnet', '2026-09-07T14:15:04'),
        _e('API ban started — testnet', '2026-09-07T13:15:05'),
    ]
    assert unresolved_ban_endpoints(entries) == []


def test_empty_and_malformed_input_is_safe():
    assert unresolved_ban_endpoints([]) == []
    assert unresolved_ban_endpoints([{}, {'title': None}, {'title': 42}]) == []


def test_main_sends_the_closing_notice_on_startup():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'unresolved_ban_endpoints(' in src, \
        'startup must close a dangling ban alert or the reader is left misinformed'
    # The CALL has to come after the notifier is wired, or nothing is sent. Compare
    # against the call site, not the import, which necessarily appears first.
    assert src.index('rl_guard.set_notifier') < src.index('unresolved_ban_endpoints(')
