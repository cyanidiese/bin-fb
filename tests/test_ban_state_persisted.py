"""The ban expiry must survive a restart.

The guard held _blocked_until in memory only, so a restart during a ban started clean
and immediately fired its startup calls — kline loads, leverage brackets, balance —
into an active ban, each one extending it. Two restarts on 2026-09-07 (16:09, 16:35)
landed inside the 15:47 ban window and only escaped because Binance had lifted it early.

Persisted as WALL-CLOCK epoch seconds: monotonic time is meaningless across processes.
"""
import json
import time

from bot.rate_limit_guard import RateLimitGuard, _MAX_BLOCK_S


def _ban(secs):
    return (f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until "
            f"{int((time.time() + secs) * 1000)}.")


class TestSave:
    def test_an_armed_ban_is_written(self, tmp_path):
        g, p = RateLimitGuard(), tmp_path / 'rl.json'
        g.note_exception('testnet', Exception(_ban(900)))
        g.save_state(p)
        d = json.loads(p.read_text())
        assert d['testnet'] > time.time(), 'must be a future wall-clock time'

    def test_a_cleared_ban_is_removed(self, tmp_path):
        g, p = RateLimitGuard(), tmp_path / 'rl.json'
        g.note_exception('testnet', Exception(_ban(900)))
        g.save_state(p)
        g.reset('testnet')
        g.save_state(p)
        assert json.loads(p.read_text()) == {}, 'a stale expiry would suppress traffic'

    def test_saving_never_raises(self, tmp_path):
        """Called from the candle path — a disk problem must not stop trading."""
        g = RateLimitGuard()
        g.note_exception('testnet', Exception(_ban(900)))
        g.save_state(tmp_path / 'no' / 'such' / 'dir' / 'rl.json')


class TestLoad:
    def test_a_future_expiry_re_arms_the_guard(self, tmp_path):
        p = tmp_path / 'rl.json'
        p.write_text(json.dumps({'testnet': time.time() + 600}))
        g = RateLimitGuard()
        g.load_state(p)
        assert g.is_blocked('testnet'), 'a restart must not call into a known ban'

    def test_an_expired_ban_is_ignored(self, tmp_path):
        p = tmp_path / 'rl.json'
        p.write_text(json.dumps({'testnet': time.time() - 60}))
        g = RateLimitGuard()
        g.load_state(p)
        assert not g.is_blocked('testnet'), 'an old file must not suppress a clean start'

    def test_a_missing_or_corrupt_file_is_safe(self, tmp_path):
        g = RateLimitGuard()
        g.load_state(tmp_path / 'absent.json')
        assert not g.is_blocked('testnet')
        bad = tmp_path / 'bad.json'
        bad.write_text('{not json')
        g.load_state(bad)
        assert not g.is_blocked('testnet')

    def test_an_absurd_expiry_is_clamped(self, tmp_path):
        """A corrupt file must not disable the bot for a year."""
        p = tmp_path / 'rl.json'
        p.write_text(json.dumps({'testnet': time.time() + 86400 * 365}))
        g = RateLimitGuard()
        g.load_state(p)
        assert g.blocked_for('testnet') <= _MAX_BLOCK_S + 1

    def test_a_restored_ban_does_not_probe_immediately(self, tmp_path):
        """A restart must not reset the probe gate.

        Measured 2026-09-08: ban until 19:35:51, restart at 18:56:29, probe at 19:00:00
        with 36 minutes still to run — because load_state restored the expiry but not
        _probe_not_before, and an absent gate used to mean "probe now". That put the
        flap straight back after every restart during a ban.
        """
        p = tmp_path / 'rl.json'
        p.write_text(json.dumps({'testnet': time.time() + 600}))
        g = RateLimitGuard()
        g.load_state(p)
        g._next_probe['testnet'] = time.monotonic() - 1     # backoff satisfied
        assert g.blocked_for('testnet') > 0, 'probed straight after a restore'

    def test_a_restored_ban_still_probes_near_the_end(self, tmp_path):
        """Binance lifts bans early; a restored block must not be waited out blindly."""
        p = tmp_path / 'rl.json'
        p.write_text(json.dumps({'testnet': time.time() + 600}))
        g = RateLimitGuard()
        g.load_state(p)
        g._next_probe['testnet'] = time.monotonic() - 1
        g._probe_not_before['testnet'] = time.monotonic() - 1   # reached the gate
        assert g.blocked_for('testnet') == 0.0, 'no probe slot offered after a restore'

    def test_multiple_endpoints_round_trip(self, tmp_path):
        p = tmp_path / 'rl.json'
        g = RateLimitGuard()
        g.note_exception('testnet', Exception(_ban(600)))
        g.note_exception('production', Exception(_ban(900)))
        g.save_state(p)
        g2 = RateLimitGuard()
        g2.load_state(p)
        assert g2.is_blocked('testnet') and g2.is_blocked('production')


def test_main_restores_before_the_first_api_call():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'main.py').read_text()
    assert 'load_state' in src, 'main.py never restores the ban state'
    assert src.index('rl_guard.load_state') < src.index('feed.load_klines'), \
        'the ban state must be restored before the first kline fetch'


def test_placement_is_a_privileged_probe_not_a_gated_call():
    """Placement must stay ungated: a real order is the scarce resource (78 in 27 days,
    all 78 succeeded, and the real slot already fires at 86-100% of its virtual twin's
    rate). Refusing one on a possibly-stale ban flag would cost what we are protecting.
    But a rate-limited failure must arm the guard so the following reads back off."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'bot/order_executor.py').read_text()
    i = src.index('Order placement failed for')
    assert 'rl_guard.note_exception' in src[max(0, i - 1600):i], \
        'a rate-limited placement must arm the guard'
    j = src.index('async def place_order')
    assert 'blocked_for' not in src[j:i], \
        'placement must not be refused on a ban flag'


def test_the_read_paths_that_start_a_ban_are_all_guarded():
    """klines, balance and leverage brackets are the calls that discover a ban."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    feed = (root / 'bot/data_feed.py').read_text()
    oe = (root / 'bot/order_executor.py').read_text()
    assert 'rl_guard.blocked_for' in feed, 'kline fetch unguarded'
    for fn in ('fetch_account_balance', 'fetch_leverage_brackets'):
        i = oe.index(f'def {fn}')
        assert 'blocked_for' in oe[i:i + 1500], f'{fn} unguarded'


def test_a_first_run_with_no_state_file_still_persists_later_bans(tmp_path):
    """The bug a runtime check caught and the unit tests missed: load_state() returned
    early on a missing file, leaving _state_path unset, so on a fresh install no ban was
    ever written and the feature was dead on first run."""
    p = tmp_path / 'rate_limit_state.json'
    g = RateLimitGuard()
    g.load_state(p)                      # file does not exist yet
    assert not p.exists()
    g.note_exception('testnet', Exception(_ban(1800)))
    assert p.exists(), 'a ban after a clean start was not persisted'
    assert json.loads(p.read_text())['testnet'] > time.time()


def test_a_corrupt_state_file_still_allows_later_persistence(tmp_path):
    p = tmp_path / 'rate_limit_state.json'
    p.write_text('{not json')
    g = RateLimitGuard()
    g.load_state(p)
    g.note_exception('testnet', Exception(_ban(1800)))
    assert json.loads(p.read_text())['testnet'] > time.time()
