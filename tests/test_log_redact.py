import logging

from bot.log_redact import RedactingFormatter, redact

TOKEN_URL = "https://api.telegram.org/bot1234567890:AAFakeTokenForTestsOnly_abcdefghijk/getUpdates?offset=1"


def test_redact_telegram_token():
    out = redact(f"409 Client Error: Conflict for url: {TOKEN_URL}")
    assert "AAFake" not in out
    assert "api.telegram.org/bot<redacted>/getUpdates" in out


def test_formatter_redacts_exception_text():
    fmt = RedactingFormatter('%(message)s')
    try:
        raise RuntimeError(TOKEN_URL)
    except RuntimeError:
        import sys
        rec = logging.LogRecord('x', logging.WARNING, __file__, 1, 'poll error', None, sys.exc_info())
    out = fmt.format(rec)
    assert "AAFake" not in out and "bot<redacted>" in out


def test_ordinary_text_untouched():
    s = "[INJUSDT] SL algo order placed: algoId=1000000183550099 triggerPrice=4.05"
    assert redact(s) == s
