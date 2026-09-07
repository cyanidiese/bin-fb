"""A gap refresh suppressed by the guard's settling window must be retried, not lost.

After a ban blackout every symbol has a kline gap, so on_candle_close creates a
_refresh_klines_bg task per symbol (main.py:1267) — and those land within a second of
the candle close, right inside the 3s settling window that follows a successful probe.
Without a retry the gaps would stay unfilled for a whole candle.

The retry is safe by construction: it goes back through the guard, so if the ban is
still real it is suppressed again and no request is made.
"""
import re
from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / 'main.py').read_text()


def _body() -> str:
    start = MAIN.index('async def _refresh_klines_bg')
    end = MAIN.index('async def on_candle_close')
    return MAIN[start:end]


def test_rate_limited_is_caught_separately_from_other_errors():
    """A generic `except Exception` would retry real failures too — a 500 or a bad
    symbol should not be retried, only a guard suppression."""
    body = _body()
    assert 'except RateLimited' in body, 'RateLimited must be handled on its own'
    assert body.index('except RateLimited') < body.index('except Exception'), \
        'the specific handler must come first or it is unreachable'


def test_it_retries_at_most_once():
    body = _body()
    assert 'for _attempt in (1, 2)' in body, 'retry must be bounded'
    assert '_attempt == 2' in body, 'the last attempt must give up rather than loop'


def test_the_retry_waits_past_the_settling_window():
    """Retrying inside the window would just be suppressed again."""
    import main
    from bot.rate_limit_guard import _SETTLE_S
    assert main._KLINE_RETRY_AFTER_S > _SETTLE_S, \
        'the retry would land inside settling and be suppressed again'


def test_a_successful_refresh_returns_immediately():
    body = _body()
    first = body.index('await asyncio.to_thread(feed.refresh_klines')
    assert 'return' in body[first:first + 120], \
        'a successful refresh must not fall through into a second attempt'


def test_other_exceptions_do_not_retry():
    body = _body()
    generic = body.index('except Exception')
    assert 'return' in body[generic:generic + 220], \
        'a non-rate-limit failure must not be retried'
