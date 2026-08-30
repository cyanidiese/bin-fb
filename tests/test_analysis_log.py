"""The analysis log runs inside the live trading loop, so its hard requirement is
that it never raises and never grows without bound."""
import json
from pathlib import Path

from bot import analysis_log


def _read(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_records_one_json_object_per_line(tmp_path):
    p = tmp_path / 'analysis.jsonl'
    analysis_log.configure(p)
    analysis_log.record('virtual_open', symbol='INJUSDT', preset='oscillating_zone', rank=1)
    analysis_log.record('virtual_close', symbol='INJUSDT', pnl=12.5)
    rows = _read(p)
    assert len(rows) == 2
    assert rows[0]['event'] == 'virtual_open'
    assert rows[0]['symbol'] == 'INJUSDT'
    assert rows[1]['pnl'] == 12.5


def test_every_record_is_timestamped(tmp_path):
    p = tmp_path / 'a.jsonl'
    analysis_log.configure(p)
    analysis_log.record('x')
    assert 'ts' in _read(p)[0]


def test_disabled_writes_nothing(tmp_path):
    p = tmp_path / 'a.jsonl'
    analysis_log.configure(p, enabled=False)
    analysis_log.record('virtual_open', symbol='X')
    assert not p.exists()
    assert analysis_log.is_enabled() is False


def test_never_raises_on_unserialisable_value(tmp_path):
    """A stray object in a field must not take down the trading loop."""
    p = tmp_path / 'a.jsonl'
    analysis_log.configure(p)
    analysis_log.record('weird', obj=object(), fn=lambda: 1)   # must not raise
    rows = _read(p)
    assert len(rows) == 1          # default=str coerced it rather than failing


def test_never_raises_when_unconfigured():
    analysis_log._handler = None
    analysis_log._enabled = False
    analysis_log.record('anything', a=1)          # must be a no-op, not an error


def test_bad_path_disables_rather_than_raising(tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('not a directory')
    analysis_log.configure(blocker / 'sub' / 'a.jsonl')   # parent is a file
    assert analysis_log.is_enabled() is False
    analysis_log.record('x')                               # still safe


def test_rotation_caps_total_size(tmp_path):
    """Size ceiling is the reason this is safe to leave on permanently."""
    p = tmp_path / 'a.jsonl'
    analysis_log.configure(p, max_bytes=2048, backups=2)
    for i in range(500):
        analysis_log.record('virtual_open', symbol='INJUSDT', preset='x' * 50, i=i)
    files = list(tmp_path.glob('a.jsonl*'))
    assert len(files) <= 3                                  # active + 2 backups
    assert sum(f.stat().st_size for f in files) < 2048 * 4  # bounded, not unbounded


def test_reconfigure_switches_target(tmp_path):
    a, b = tmp_path / 'a.jsonl', tmp_path / 'b.jsonl'
    analysis_log.configure(a)
    analysis_log.record('one')
    analysis_log.configure(b)
    analysis_log.record('two')
    assert _read(a)[0]['event'] == 'one'
    assert _read(b)[0]['event'] == 'two'
