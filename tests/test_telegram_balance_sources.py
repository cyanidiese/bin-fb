"""Wallet figures in Telegram must not cost a request they do not need.

Two different figures with two different requirements, which the code already
distinguishes and which must not be collapsed:

  "Before" — read at placement. on_candle_close() has already called
             _get_fresh_balance() moments earlier in the same handler, so the TTL cache
             holds a genuinely pre-trade figure. An extra uncached weight-5 call bought
             nothing.

  "After"  — must reflect the settled close. The TTL cache may hold the pre-close figure
             from the placement pass, which is exactly the 2026-08-19 bug where a close
             reported the balance from before it settled. So this stays an uncached
             read — but when that read fails (an API ban returns 0.0) the message showed
             "n/a", and the bot can compute before + net PnL instead, labelled so it is
             never mistaken for an exchange-confirmed figure.
"""
import inspect
from pathlib import Path

from bot.notifier import Notifier

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()


def test_the_before_figure_uses_the_cached_balance():
    i = MAIN.index('wallet_before =')
    line = MAIN[i:MAIN.index('\n', i)]
    assert '_get_fresh_balance' in line, \
        'placement already refreshed the cache this candle — an uncached call is waste'
    assert '_read_wallet_now' not in line


def test_the_after_figure_still_uses_an_uncached_read():
    """Collapsing this to the cache reintroduces the 2026-08-19 pre-close bug."""
    assert MAIN.count('_read_wallet_now()') >= 2, \
        'the post-close reads must stay uncached'


def test_notify_trade_close_can_mark_a_computed_balance():
    sig = inspect.signature(Notifier.notify_trade_close)
    assert 'balance_estimated' in sig.parameters
    assert sig.parameters['balance_estimated'].default is False, \
        'a figure must be treated as exchange-read unless explicitly stated otherwise'


def test_a_computed_after_balance_is_labelled(monkeypatch, tmp_path):
    sent = []
    n = Notifier(log_path=tmp_path / 'l.json', alert_path=tmp_path / 'a.json',
                 telegram_token='t', telegram_chat_id='c', min_interval_s=0.0)
    monkeypatch.setattr(n, '_send_telegram', lambda text, mention=False: sent.append(text))
    n.notify_trade_close(symbol='TIAUSDT', side='BUY', pnl_usdt=12.0,
                         entry_price=1.0, close_price=1.1, preset_name='p',
                         balance_before=100.0, balance_after=112.0,
                         fee_usdt=0.5, balance_estimated=True)
    assert sent, 'nothing was sent'
    assert 'computed' in sent[0].lower() or 'estimated' in sent[0].lower(), \
        'a computed figure must say so'
    assert '112' in sent[0]


def test_a_read_after_balance_is_not_labelled(monkeypatch, tmp_path):
    sent = []
    n = Notifier(log_path=tmp_path / 'l.json', alert_path=tmp_path / 'a.json',
                 telegram_token='t', telegram_chat_id='c', min_interval_s=0.0)
    monkeypatch.setattr(n, '_send_telegram', lambda text, mention=False: sent.append(text))
    n.notify_trade_close(symbol='TIAUSDT', side='BUY', pnl_usdt=12.0,
                         entry_price=1.0, close_price=1.1, preset_name='p',
                         balance_before=100.0, balance_after=112.0, fee_usdt=0.5)
    assert 'computed' not in sent[0].lower()


def test_main_computes_the_fallback_from_before_plus_pnl():
    assert 'balance_estimated' in MAIN, 'main.py never passes the flag'
    i = MAIN.index('balance_estimated')
    near = MAIN[max(0, i - 900):i + 300]
    assert 'pnl' in near.lower(), 'the fallback must be computed from the trade PnL'
