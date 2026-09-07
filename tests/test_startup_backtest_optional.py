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
import re
from pathlib import Path

from config.risk_config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / 'main.py').read_text()


def test_the_key_exists_and_defaults_to_off():
    assert 'startup_backtest' in DEFAULT_CONFIG
    assert DEFAULT_CONFIG['startup_backtest'] is False, \
        'a deploy must not cost 9 minutes of blind time by default'


def test_startup_reads_the_flag_from_risk_config():
    assert "startup_backtest" in MAIN, 'main.py does not consult the flag'


def _startup_block() -> str:
    i = MAIN.index('startup backtest')
    return MAIN[i - 400:i + 2200]


def test_the_subprocess_is_gated_by_the_flag():
    """The blocking call must sit inside the conditional, not before it."""
    block = _startup_block()
    flag_at = block.index('startup_backtest')
    run_at = block.index('subprocess.run')
    assert flag_at < run_at, 'the flag must be read before the subprocess is launched'


def test_skipping_still_seeds_from_the_existing_results():
    """backtest_results_*.json persist, so the preset seeds and RiskManager's leverage
    inputs survive a skipped backtest — they are just older."""
    assert 'seed_from_backtest' in MAIN
    seed_at = MAIN.index('seed_from_backtest')
    bt_at = MAIN.index('subprocess.run')
    assert bt_at < seed_at, 'seeding must still run after the (optional) backtest'


def test_the_skip_is_logged_with_the_age_of_the_data():
    """Silently reusing month-old backtest data would be a trap — the log has to say
    how stale the seeds are."""
    block = _startup_block()
    assert re.search(r'age|stale|old|days|hours', block, re.I), \
        'skipping must report how old the existing backtest results are'


def test_the_hard_fail_is_kept_when_the_backtest_is_requested():
    """If someone deliberately asks for a fresh backtest and it fails, that must stay
    loud — fail-closed behaviour is unchanged when the flag is on."""
    block = _startup_block()
    assert 'sys.exit(1)' in block, 'an explicitly requested backtest must still fail loudly'
