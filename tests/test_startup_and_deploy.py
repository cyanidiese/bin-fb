"""Startup cost and deploy build order: what a restart spends before trading resumes.

Sections:
  startup_api_calls            TestKlineGapSkip, TestLeverageBracketBatch, TestExchangeInfoCache
  TestStartupBacktestOptional  the blocking startup backtest is opt-in
  TestDockerfileLayerOrder     a bot-only change must not rebuild the Next.js app
"""
import re
import time

from bot.data_feed import cache_is_current
from config.risk_config import DEFAULT_CONFIG
from tests.factories import src


# ────────────────────────── startup_api_calls ────────────────────────── #
# Startup made 35 API calls; most were avoidable.
#
# Measured budget for a 15-symbol restart before this change:
#
#     1  exchange_info          check_symbols_on_exchange
#     1  exchange_info          prefetch_lot_sizes -> _ensure_lot_size
#    15  leverageBracket        one per symbol
#     1  futures_account        balance seed
#    15  klines                 gap fetch, one per symbol
#     2  positionInformation    reconciliation
#    --
#    35
#
# This does NOT cause the bans — the mirror runs the identical startup against production
# and has taken 0 rejections across 4 restarts, while 3 of 4 testnet ban onsets had no
# restart anywhere near them. It is removed because it is free to remove.

CANDLE_MS = 15 * 60 * 1000


class TestKlineGapSkip:
    """15 calls -> 0 on a typical restart.

    load_klines reads 3419-5000 candles from disk and fetches only the gap since the
    cache's last candle. A deploy now takes ~114s, so the gap is usually zero candles
    and the fetch retrieves nothing at all.
    """

    def _now(self, ms_ago):
        return int(time.time() * 1000) - ms_ago

    def test_a_cache_with_no_missing_candle_needs_no_fetch(self):
        # last candle closed 30s ago: the next one has not formed yet
        assert cache_is_current(last_close_ms=self._now(30_000), candle_ms=CANDLE_MS) is True

    def test_a_cache_missing_a_full_candle_needs_a_fetch(self):
        assert cache_is_current(last_close_ms=self._now(2 * CANDLE_MS), candle_ms=CANDLE_MS) is False

    def test_the_boundary_is_one_candle(self):
        assert cache_is_current(last_close_ms=self._now(CANDLE_MS - 5000), candle_ms=CANDLE_MS) is True
        assert cache_is_current(last_close_ms=self._now(CANDLE_MS + 5000), candle_ms=CANDLE_MS) is False

    def test_an_empty_or_unknown_cache_always_fetches(self):
        assert cache_is_current(last_close_ms=0, candle_ms=CANDLE_MS) is False
        assert cache_is_current(last_close_ms=self._now(0), candle_ms=0) is False

    def test_a_future_timestamp_does_not_skip_forever(self):
        """A clock skew or bad cache must not make us stop fetching permanently."""
        assert cache_is_current(last_close_ms=self._now(-10 * CANDLE_MS), candle_ms=CANDLE_MS) is True


class TestLeverageBracketBatch:
    """15 calls -> 1. symbol is optional on /fapi/v1/leverageBracket, the weight is 1
    either way, and the response shape is the same array of {symbol, brackets}."""

    def test_it_asks_for_every_symbol_in_one_call(self):
        executor_src = src('bot/order_executor.py')
        i = executor_src.index('async def fetch_leverage_brackets')
        body = executor_src[i:i + 2600]
        assert 'for symbol in symbols' not in body.split('fallback')[0], \
            'the happy path must not loop one call per symbol'
        assert 'futures_leverage_bracket' in body

    def test_a_per_symbol_fallback_is_kept(self):
        """If the batch form ever fails, one bad symbol must not cost us all brackets."""
        executor_src = src('bot/order_executor.py')
        i = executor_src.index('async def fetch_leverage_brackets')
        body = executor_src[i:i + 2600]
        assert 'fallback' in body.lower(), 'no fallback if the batch call fails'


class TestExchangeInfoCache:
    """2 calls -> 1. Both startup callers pull the same ~735-symbol payload."""

    def test_there_is_a_cached_accessor(self):
        executor_src = src('bot/order_executor.py')
        assert '_exchange_info_cached' in executor_src

    def test_both_startup_callers_use_it(self):
        executor_src = src('bot/order_executor.py')
        for fn in ('check_symbols_on_exchange', '_ensure_lot_size'):
            i = executor_src.index(f'def {fn}')
            body = executor_src[i:i + 1800]
            assert '_exchange_info_cached' in body, f'{fn} still calls the API directly'

    def test_the_ttl_is_short_enough_to_stay_fresh(self):
        from bot.order_executor import _EXCHANGE_INFO_TTL_S
        assert 0 < _EXCHANGE_INFO_TTL_S <= 900, \
            'listings and lot filters change; this must not be cached for long'


# ────────────────────────── startup_backtest_optional ────────────────────────── #

MAIN = src('main.py')


def _startup_block() -> str:
    i = MAIN.index('startup backtest')
    return MAIN[i - 400:i + 2200]


class TestStartupBacktestOptional:
    """The obligatory startup backtest is opt-in.

    Measured on the real log: every restart spent 256-567s (median ~265s, worst 9m11s) in a
    blocking `subprocess.run(backtest.py)` with capture_output=True — 551 seconds of
    completely silent log between "Bot starting" and the feed being built, roughly 96% of
    startup. During that window there is no WebSocket, no candle processing and no position
    monitoring in our process; on 2026-09-07 it happened with a real TIAUSDT position open
    (the exchange-side stop-loss is what actually protected it).

    Deploys are frequent and are the main source of restarts, so this now defaults to OFF
    and is turned on from the dashboard Settings page when a fresh backtest is actually
    wanted. The results files persist, so skipping reuses the previous run's data rather
    than losing it.
    """

    def test_the_key_exists_and_defaults_to_off(self):
        assert 'startup_backtest' in DEFAULT_CONFIG
        assert DEFAULT_CONFIG['startup_backtest'] is False, \
            'a deploy must not cost 9 minutes of blind time by default'

    def test_startup_reads_the_flag_from_risk_config(self):
        assert "startup_backtest" in MAIN, 'main.py does not consult the flag'

    def test_the_subprocess_is_gated_by_the_flag(self):
        """The blocking call must sit inside the conditional, not before it."""
        block = _startup_block()
        flag_at = block.index('startup_backtest')
        run_at = block.index('subprocess.run')
        assert flag_at < run_at, 'the flag must be read before the subprocess is launched'

    def test_skipping_still_seeds_from_the_existing_results(self):
        """backtest_results_*.json persist, so the preset seeds and RiskManager's leverage
        inputs survive a skipped backtest — they are just older."""
        assert 'seed_from_backtest' in MAIN
        seed_at = MAIN.index('seed_from_backtest')
        bt_at = MAIN.index('subprocess.run')
        assert bt_at < seed_at, 'seeding must still run after the (optional) backtest'

    def test_the_skip_is_logged_with_the_age_of_the_data(self):
        """Silently reusing month-old backtest data would be a trap — the log has to say
        how stale the seeds are."""
        block = _startup_block()
        assert re.search(r'age|stale|old|days|hours', block, re.I), \
            'skipping must report how old the existing backtest results are'

    def test_the_hard_fail_is_kept_when_the_backtest_is_requested(self):
        """If someone deliberately asks for a fresh backtest and it fails, that must stay
        loud — fail-closed behaviour is unchanged when the flag is on."""
        block = _startup_block()
        assert 'sys.exit(1)' in block, 'an explicitly requested backtest must still fail loudly'


# ────────────────────────── dockerfile_layer_order ────────────────────────── #

RAW = src('Dockerfile')
INSTRUCTIONS = [
    ln.strip() for ln in RAW.splitlines()
    if ln.strip() and not ln.strip().startswith('#')
]


def _line_of(fragment: str) -> int:
    for i, ln in enumerate(INSTRUCTIONS):
        if fragment in ln:
            return i
    raise AssertionError(f'no Dockerfile instruction contains {fragment!r}')


class TestDockerfileLayerOrder:
    """A bot-only change must not rebuild the Next.js app.

    `COPY . .` sat above `RUN npm run build`, so editing main.py invalidated the build layer
    and every bot-only deploy paid ~40s rebuilding a dashboard the `bot` service does not
    serve. Docker caches layers in file order, so the fix is ordering: dashboard sources and
    their build first, Python source last.

    Comments are stripped before matching — an earlier version of this test found these
    strings inside the explanatory comments and reported the opposite of the truth.
    """

    def test_the_next_build_comes_before_the_python_source_copy(self):
        assert _line_of('npm run build') < _line_of('COPY . .'), \
            'COPY . . above npm run build makes every Python change rebuild the dashboard'

    def test_the_dashboard_source_is_copied_before_its_build(self):
        assert _line_of('COPY dashboard/ ') < _line_of('npm run build'), \
            'the build needs its sources'

    def test_npm_ci_still_precedes_the_build(self):
        """Dependency install stays in its own earlier layer, or it reinstalls too."""
        assert _line_of('npm ci') < _line_of('npm run build')

    def test_the_python_venv_layer_is_still_cached_separately(self):
        """requirements.txt must be copied before the source, or a code change reinstalls
        every Python dependency."""
        assert _line_of('COPY requirements.txt') < _line_of('COPY . .')

    def test_the_final_copy_still_brings_the_python_source(self):
        """Reordering must not drop the Python source — the bot would ship without main.py."""
        assert any(ln.startswith('COPY . .') for ln in INSTRUCTIONS)

    def test_the_dashboard_is_copied_before_the_broad_copy_so_its_layer_is_reusable(self):
        """Both COPY steps bring dashboard/ in, which is harmless — what matters is that
        the narrow one comes first so the build layer keys only on dashboard sources."""
        assert _line_of('COPY dashboard/ ') < _line_of('COPY . .')
