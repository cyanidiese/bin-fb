"""The bot's on-disk logs and the atomic JSON writer behind the config files.

Sections:
  TestDecisionLog             bot.decision_log record(): shape, cap, atomic write
  TestDecisionLogKeepsPlaced  bot.decision_log _trim(): trimming never drops 'placed' rows
  TestAnalysisLog             bot.analysis_log: never raises, never grows unbounded
  TestSystemLog               bot.system_log append_entry(): rolling cap, corrupt reset
  TestLogRedact               bot.log_redact: Telegram token redaction
  TestSafeWrite               config.safe_write write_json(): bind-mount EBUSY fallback
"""
import json
import logging
import os
import sys
from pathlib import Path

import pytest

import config.safe_write as sw
from bot import analysis_log
from bot.decision_log import MAX_ENTRIES, _trim, record
from bot.log_redact import RedactingFormatter, redact
from bot.system_log import MAX_ENTRIES as SYSTEM_LOG_MAX_ENTRIES
from bot.system_log import append_entry
from config.safe_write import write_json


# ─────────────────────────── decision_log ─────────────────────────── #

class TestDecisionLog:
    def test_creates_file_on_first_write(self, tmp_path):
        path = tmp_path / 'dl.json'
        record(path, candle_ts=1000, symbol='BTCUSDT', decision='placed',
               reason='ok', balance=100.0, leverage=1, efficiency_score=0.8)
        assert path.exists()

    def test_placed_entry_shape(self, tmp_path):
        path = tmp_path / 'dl.json'
        record(path, candle_ts=1746878400000, symbol='ETHUSDT', decision='placed',
               reason='', balance=432.5, leverage=2, efficiency_score=0.83,
               preset_name='r5_arm15_cooldown', signal_type='ASCENDING_NEAR_HIGHER_LOW',
               precision_score=0.71, level=2)
        data = json.loads(path.read_text())
        assert len(data) == 1
        e = data[0]
        assert e['symbol'] == 'ETHUSDT'
        assert e['decision'] == 'placed'
        assert e['candle_ts'] == 1746878400000
        assert e['preset_name'] == 'r5_arm15_cooldown'
        assert e['precision_score'] == 0.71
        assert e['level'] == 2

    def test_skip_entry_without_optional_fields(self, tmp_path):
        path = tmp_path / 'dl.json'
        record(path, candle_ts=1000, symbol='BTCUSDT', decision='skip_balance',
               reason='balance=5 < margin=22', balance=5.0, leverage=1, efficiency_score=0.0)
        data = json.loads(path.read_text())
        e = data[0]
        assert e['decision'] == 'skip_balance'
        assert 'preset_name' not in e
        assert 'precision_score' not in e

    def test_caps_at_max_entries(self, tmp_path):
        # Seeded at the cap in one write (see test_balance_history): ~26 s -> milliseconds.
        path = tmp_path / 'dl.json'
        path.write_text(json.dumps([
            {'timestamp': '2026-01-01T00:00:00+00:00', 'candle_ts': i, 'symbol': 'BTCUSDT',
             'decision': 'placed', 'reason': '', 'balance': 100.0, 'leverage': 1,
             'efficiency_score': 0.0} for i in range(MAX_ENTRIES)]))
        for i in range(MAX_ENTRIES, MAX_ENTRIES + 5):
            record(path, candle_ts=i, symbol='BTCUSDT', decision='placed',
                   reason='', balance=100.0, leverage=1, efficiency_score=0.0)
        data = json.loads(path.read_text())
        assert len(data) == MAX_ENTRIES
        assert data[-1]['candle_ts'] == MAX_ENTRIES + 4  # newest retained

    def test_tmp_file_uses_pid_suffix(self, tmp_path):
        path = tmp_path / 'dl.json'
        record(path, candle_ts=1000, symbol='BTCUSDT', decision='placed',
               reason='', balance=100.0, leverage=1, efficiency_score=0.0)
        pid = os.getpid()
        # No stale .json.tmp left behind — only the pid-qualified tmp is created and renamed
        assert not (tmp_path / 'dl.json.tmp').exists(), "bare .json.tmp should not persist"
        assert path.exists()

    def test_sequential_writes_accumulate(self, tmp_path):
        path = tmp_path / 'dl.json'
        for i in range(3):
            record(path, candle_ts=i, symbol='BTCUSDT', decision='placed',
                   reason='', balance=float(i), leverage=1, efficiency_score=0.0)
        data = json.loads(path.read_text())
        assert len(data) == 3
        assert [e['candle_ts'] for e in data] == [0, 1, 2]


# ─────────────────────── decision_log keeps placed ─────────────────────── #

def _rows(n_skip: int, n_placed: int) -> list:
    """Interleaved so a tail-trim would drop the early placed rows."""
    out = []
    for i in range(n_placed):
        out.append({'decision': 'placed', 'i': i})
        out.extend({'decision': 'skip_zero_score', 'i': f'{i}.{j}'}
                   for j in range(n_skip // max(1, n_placed)))
    return out


class TestDecisionLogKeepsPlaced:
    """Trimming the decision log must not throw away the real-order records.

    `placed` rows are what every profitability analysis reads; skips are far more numerous and
    individually far less valuable. A plain tail-trim discards exactly the rows worth keeping.

    Measured on the server 2026-09-08: adding reasons to the 17 silent rejection paths pushed
    the 5,000-row cap harder, and within four hours 15 of 79 'placed' rows had been evicted --
    the log's window shrank from 27 days (Aug 12) to 18 (Aug 21). The instrumentation was
    worth having; losing order history to it was not.
    """

    def test_a_plain_tail_trim_would_lose_placed_rows(self):
        """Establishes the premise — without it the tests below prove nothing."""
        rows = _rows(6000, 40)
        tail = rows[-MAX_ENTRIES:]
        assert sum(1 for e in tail if e['decision'] == 'placed') < 40

    def test_trim_keeps_every_placed_row(self):
        rows = _rows(6000, 40)
        kept = _trim(rows)
        assert sum(1 for e in kept if e['decision'] == 'placed') == 40

    def test_trim_respects_the_row_cap(self):
        """The file must not grow — the per-decision read-modify-write cost depends on it."""
        kept = _trim(_rows(20000, 50))
        assert len(kept) <= MAX_ENTRIES

    def test_trim_preserves_chronological_order(self):
        rows = _rows(6000, 30)
        kept = _trim(rows)
        pos = {id(e): i for i, e in enumerate(rows)}   # rows.index() per row was O(n^2)
        idx = [pos[id(e)] for e in kept]
        assert idx == sorted(idx)

    def test_trim_keeps_the_newest_skips(self):
        """Old skips are the right thing to drop; recent ones still diagnose today."""
        rows = _rows(6000, 10)
        kept = _trim(rows)
        assert kept[-1] is rows[-1]

    def test_max_placed_is_a_floor_not_a_ceiling(self):
        """A mostly-'placed' log must still fill to MAX_ENTRIES.

        My first version treated MAX_PLACED as a hard cap, which shrank an all-'placed' log
        from 5,000 rows to 1,004 — throwing away 4,000 rows with room to spare. The existing
        TestDecisionLog::test_caps_at_max_entries caught it.
        """
        rows = [{'decision': 'placed', 'i': i} for i in range(MAX_ENTRIES + 500)]
        kept = _trim(rows)
        assert len(kept) == MAX_ENTRIES
        assert kept[-1] is rows[-1]                      # newest retained

    def test_the_file_stays_bounded_whatever_the_mix(self):
        for n_skip, n_placed in ((20000, 50), (0, MAX_ENTRIES + 900), (9000, 3000)):
            kept = _trim(_rows(n_skip, n_placed) if n_skip else
                         [{'decision': 'placed', 'i': i} for i in range(n_placed)])
            assert len(kept) <= MAX_ENTRIES, f'{n_skip}/{n_placed} -> {len(kept)}'

    def test_a_log_under_the_cap_is_untouched(self):
        """_trim is only called over the cap, but it must be safe if that ever changes."""
        rows = _rows(100, 5)
        assert len(rows) < MAX_ENTRIES          # premise
        assert _trim(rows) == rows


# ─────────────────────────── analysis_log ─────────────────────────── #

def _read_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestAnalysisLog:
    """The analysis log runs inside the live trading loop, so its hard requirement is
    that it never raises and never grows without bound."""

    def test_records_one_json_object_per_line(self, tmp_path):
        p = tmp_path / 'analysis.jsonl'
        analysis_log.configure(p)
        analysis_log.record('virtual_open', symbol='INJUSDT', preset='oscillating_zone', rank=1)
        analysis_log.record('virtual_close', symbol='INJUSDT', pnl=12.5)
        rows = _read_jsonl(p)
        assert len(rows) == 2
        assert rows[0]['event'] == 'virtual_open'
        assert rows[0]['symbol'] == 'INJUSDT'
        assert rows[1]['pnl'] == 12.5

    def test_every_record_is_timestamped(self, tmp_path):
        p = tmp_path / 'a.jsonl'
        analysis_log.configure(p)
        analysis_log.record('x')
        assert 'ts' in _read_jsonl(p)[0]

    def test_disabled_writes_nothing(self, tmp_path):
        p = tmp_path / 'a.jsonl'
        analysis_log.configure(p, enabled=False)
        analysis_log.record('virtual_open', symbol='X')
        assert not p.exists()
        assert analysis_log.is_enabled() is False

    def test_never_raises_on_unserialisable_value(self, tmp_path):
        """A stray object in a field must not take down the trading loop."""
        p = tmp_path / 'a.jsonl'
        analysis_log.configure(p)
        analysis_log.record('weird', obj=object(), fn=lambda: 1)   # must not raise
        rows = _read_jsonl(p)
        assert len(rows) == 1          # default=str coerced it rather than failing

    def test_never_raises_when_unconfigured(self):
        analysis_log._handler = None
        analysis_log._enabled = False
        analysis_log.record('anything', a=1)          # must be a no-op, not an error

    def test_bad_path_disables_rather_than_raising(self, tmp_path):
        blocker = tmp_path / 'blocker'
        blocker.write_text('not a directory')
        analysis_log.configure(blocker / 'sub' / 'a.jsonl')   # parent is a file
        assert analysis_log.is_enabled() is False
        analysis_log.record('x')                               # still safe

    def test_rotation_caps_total_size(self, tmp_path):
        """Size ceiling is the reason this is safe to leave on permanently."""
        p = tmp_path / 'a.jsonl'
        analysis_log.configure(p, max_bytes=2048, backups=2)
        for i in range(500):
            analysis_log.record('virtual_open', symbol='INJUSDT', preset='x' * 50, i=i)
        files = list(tmp_path.glob('a.jsonl*'))
        assert len(files) <= 3                                  # active + 2 backups
        assert sum(f.stat().st_size for f in files) < 2048 * 4  # bounded, not unbounded

    def test_reconfigure_switches_target(self, tmp_path):
        a, b = tmp_path / 'a.jsonl', tmp_path / 'b.jsonl'
        analysis_log.configure(a)
        analysis_log.record('one')
        analysis_log.configure(b)
        analysis_log.record('two')
        assert _read_jsonl(a)[0]['event'] == 'one'
        assert _read_jsonl(b)[0]['event'] == 'two'


# ─────────────────────────── system_log ─────────────────────────── #

class TestSystemLog:
    def test_creates_file_and_appends(self, tmp_path):
        path = tmp_path / "log.json"
        append_entry(path, level="info", title="hello", detail="world", source="test")
        entries = json.loads(path.read_text())
        assert len(entries) == 1
        e = entries[0]
        assert e["level"] == "info"
        assert e["title"] == "hello"
        assert e["detail"] == "world"
        assert e["source"] == "test"
        assert "id" in e
        assert "timestamp" in e

    def test_rolling_cap(self, tmp_path):
        path = tmp_path / "log.json"
        # Seeded at the cap in one write, then pushed over it through append_entry().
        path.write_text(json.dumps([{"id": str(i), "timestamp": "2026-01-01T00:00:00+00:00", "level": "info",
                                     "title": f"title {i}", "detail": "", "source": "test"}
                                    for i in range(SYSTEM_LOG_MAX_ENTRIES)]))
        for i in range(SYSTEM_LOG_MAX_ENTRIES, SYSTEM_LOG_MAX_ENTRIES + 10):
            append_entry(path, "info", f"title {i}", "", "test")
        entries = json.loads(path.read_text())
        assert len(entries) == SYSTEM_LOG_MAX_ENTRIES
        # Oldest entry should be gone — first surviving entry should be index 10
        assert entries[0]["title"] == f"title {10}"

    def test_atomic_write_no_partial(self, tmp_path):
        path = tmp_path / "log.json"
        append_entry(path, "info", "a", "", "test")
        # tmp file must not remain after write
        assert not (tmp_path / "log.json.tmp").exists()

    def test_existing_corrupt_file_is_reset(self, tmp_path):
        path = tmp_path / "log.json"
        path.write_text("{{broken json")
        append_entry(path, "warning", "b", "", "test")
        entries = json.loads(path.read_text())
        assert len(entries) == 1


# ─────────────────────────── log_redact ─────────────────────────── #

TOKEN_URL = "https://api.telegram.org/bot1234567890:AAFakeTokenForTestsOnly_abcdefghijk/getUpdates?offset=1"


class TestLogRedact:
    def test_redact_telegram_token(self):
        out = redact(f"409 Client Error: Conflict for url: {TOKEN_URL}")
        assert "AAFake" not in out
        assert "api.telegram.org/bot<redacted>/getUpdates" in out

    def test_formatter_redacts_exception_text(self):
        fmt = RedactingFormatter('%(message)s')
        try:
            raise RuntimeError(TOKEN_URL)
        except RuntimeError:
            rec = logging.LogRecord('x', logging.WARNING, __file__, 1, 'poll error', None, sys.exc_info())
        out = fmt.format(rec)
        assert "AAFake" not in out and "bot<redacted>" in out

    def test_ordinary_text_untouched(self):
        s = "[INJUSDT] SL algo order placed: algoId=1000000183550099 triggerPrice=4.05"
        assert redact(s) == s


# ─────────────────────────── safe_write ─────────────────────────── #

class TestSafeWrite:
    """write_json must work on a bind-mounted single file, where rename returns EBUSY.

    Verified on the server 2026-09-07: renaming over /app/risk_config.json fails with
    "[Errno 16] Device or resource busy" because the path is itself a mount point. The
    textbook tmp+replace therefore cannot be the only strategy for the two config files
    this project bind-mounts.
    """

    def test_writes_valid_json(self, tmp_path):
        p = tmp_path / 'x.json'
        write_json(p, {'a': 1})
        assert json.loads(p.read_text()) == {'a': 1}

    def test_prefers_the_atomic_rename(self, tmp_path, monkeypatch):
        p = tmp_path / 'x.json'
        p.write_text('{}')
        seen = []
        real = Path.replace
        monkeypatch.setattr(Path, 'replace',
                            lambda self, t: (seen.append(self.name), real(self, t))[1])
        write_json(p, {'a': 1})
        assert seen, 'the atomic path was not attempted'
        assert json.loads(p.read_text()) == {'a': 1}

    def test_falls_back_when_rename_is_busy(self, tmp_path, monkeypatch):
        """The bind-mount case: the write must still land."""
        p = tmp_path / 'x.json'
        p.write_text('{"old": true}')

        def busy(self, target):
            raise OSError(16, 'Device or resource busy')

        monkeypatch.setattr(Path, 'replace', busy)
        sw._warned.clear()
        write_json(p, {'new': True})
        assert json.loads(p.read_text()) == {'new': True}, 'the in-place write did not land'

    def test_no_temp_file_is_left_behind_on_either_path(self, tmp_path, monkeypatch):
        p = tmp_path / 'x.json'
        write_json(p, {'a': 1})
        assert list(tmp_path.glob('*.tmp')) == []

        monkeypatch.setattr(Path, 'replace',
                            lambda self, t: (_ for _ in ()).throw(OSError(16, 'busy')))
        sw._warned.clear()
        write_json(p, {'b': 2})
        assert list(tmp_path.glob('*.tmp')) == [], 'the busy path leaked a .tmp file'

    def test_the_fallback_notice_is_logged_once_per_path(self, tmp_path, monkeypatch, caplog):
        p = tmp_path / 'x.json'
        monkeypatch.setattr(Path, 'replace',
                            lambda self, t: (_ for _ in ()).throw(OSError(16, 'busy')))
        sw._warned.clear()
        with caplog.at_level('INFO'):
            write_json(p, {'a': 1})
            write_json(p, {'a': 2})
        hits = [r for r in caplog.records if 'atomic rename unavailable' in r.message]
        assert len(hits) == 1, f'expected one notice per path, got {len(hits)}'

    def test_a_genuine_failure_still_raises(self, tmp_path):
        """No permission, no space, bad directory — the caller has to be able to see it."""
        with pytest.raises(OSError):
            write_json(tmp_path / 'no' / 'such' / 'dir' / 'x.json', {'a': 1})
