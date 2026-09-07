"""A statistics-only mirror instance sends no Telegram at all.

It has no trades to report, and duplicate ban alerts from two bots are worse than none.
Notifier guards sending on `if self._token and self._chat_id`, so an empty token disables
sending while local logging continues.

Two levels of test: that main.py wires the token conditionally, and that an empty token
genuinely reaches no network call — the second is the property we actually depend on.
"""
import re
from pathlib import Path
from unittest.mock import patch

from bot.notifier import Notifier

MAIN = Path(__file__).resolve().parents[1] / 'main.py'


def _token_arg() -> str:
    """The telegram_token argument, however many lines it spans.

    Captured up to the next argument rather than to end-of-line, so wrapping the
    expression across lines does not silently empty this test.
    """
    m = re.search(r'telegram_token\s*=(.*?)telegram_chat_id\s*=',
                  MAIN.read_text(), re.DOTALL)
    assert m, 'telegram_token argument not found'
    return m.group(1)


def test_token_is_blanked_for_virtual_only():
    assert 'virtual_only' in _token_arg(), \
        'virtual_only must blank the token so no message is ever sent'


def test_the_real_token_still_reaches_the_trading_bot():
    """The guard must be conditional, not a blanket disable."""
    assert 'token' in _token_arg().replace('telegram_token', '')


def test_settings_are_loaded_before_the_notifier_is_built():
    """The guard reads _base_settings, so it must already exist at that point."""
    src = MAIN.read_text()
    assert src.index('_base_settings = load_settings()') < src.index('notifier = Notifier('), \
        '_base_settings must be loaded above the Notifier construction'


def _notifier(tmp_path, token, chat_id):
    return Notifier(
        log_path=tmp_path / 'system_log.json',
        alert_path=tmp_path / 'alert_state.json',
        telegram_token=token,
        telegram_chat_id=chat_id,
        min_interval_s=0.0,
    )


def test_empty_token_sends_nothing(tmp_path):
    n = _notifier(tmp_path, '', '12345')
    with patch('requests.post') as post:
        n.notify('emergency', 'should not be sent', 'body', 'test')
    assert post.call_count == 0, 'a blank token must not reach the network'


def test_a_real_token_does_send(tmp_path):
    """Proves the previous test measures the token, not some unrelated suppression."""
    n = _notifier(tmp_path, 'tok', '12345')
    with patch('requests.post') as post:
        n.notify('emergency', 'should be sent', 'body', 'test')
    assert post.call_count == 1, 'a configured notifier must still send'
