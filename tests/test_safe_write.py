"""write_json must work on a bind-mounted single file, where rename returns EBUSY.

Verified on the server 2026-09-07: renaming over /app/risk_config.json fails with
"[Errno 16] Device or resource busy" because the path is itself a mount point. The
textbook tmp+replace therefore cannot be the only strategy for the two config files
this project bind-mounts.
"""
import json
from pathlib import Path

import pytest

from config.safe_write import write_json
import config.safe_write as sw


def test_writes_valid_json(tmp_path):
    p = tmp_path / 'x.json'
    write_json(p, {'a': 1})
    assert json.loads(p.read_text()) == {'a': 1}


def test_prefers_the_atomic_rename(tmp_path, monkeypatch):
    p = tmp_path / 'x.json'
    p.write_text('{}')
    seen = []
    real = Path.replace
    monkeypatch.setattr(Path, 'replace',
                        lambda self, t: (seen.append(self.name), real(self, t))[1])
    write_json(p, {'a': 1})
    assert seen, 'the atomic path was not attempted'
    assert json.loads(p.read_text()) == {'a': 1}


def test_falls_back_when_rename_is_busy(tmp_path, monkeypatch):
    """The bind-mount case: the write must still land."""
    p = tmp_path / 'x.json'
    p.write_text('{"old": true}')

    def busy(self, target):
        raise OSError(16, 'Device or resource busy')

    monkeypatch.setattr(Path, 'replace', busy)
    sw._warned.clear()
    write_json(p, {'new': True})
    assert json.loads(p.read_text()) == {'new': True}, 'the in-place write did not land'


def test_no_temp_file_is_left_behind_on_either_path(tmp_path, monkeypatch):
    p = tmp_path / 'x.json'
    write_json(p, {'a': 1})
    assert list(tmp_path.glob('*.tmp')) == []

    monkeypatch.setattr(Path, 'replace',
                        lambda self, t: (_ for _ in ()).throw(OSError(16, 'busy')))
    sw._warned.clear()
    write_json(p, {'b': 2})
    assert list(tmp_path.glob('*.tmp')) == [], 'the busy path leaked a .tmp file'


def test_the_fallback_notice_is_logged_once_per_path(tmp_path, monkeypatch, caplog):
    p = tmp_path / 'x.json'
    monkeypatch.setattr(Path, 'replace',
                        lambda self, t: (_ for _ in ()).throw(OSError(16, 'busy')))
    sw._warned.clear()
    with caplog.at_level('INFO'):
        write_json(p, {'a': 1})
        write_json(p, {'a': 2})
    hits = [r for r in caplog.records if 'atomic rename unavailable' in r.message]
    assert len(hits) == 1, f'expected one notice per path, got {len(hits)}'


def test_a_genuine_failure_still_raises(tmp_path):
    """No permission, no space, bad directory — the caller has to be able to see it."""
    with pytest.raises(OSError):
        write_json(tmp_path / 'no' / 'such' / 'dir' / 'x.json', {'a': 1})
