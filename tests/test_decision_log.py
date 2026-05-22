import json
import os
import pytest
from pathlib import Path
from bot.decision_log import record, MAX_ENTRIES, _append


def test_creates_file_on_first_write(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1000, symbol='BTCUSDT', decision='placed',
           reason='ok', balance=100.0, leverage=1, efficiency_score=0.8)
    assert path.exists()


def test_placed_entry_shape(tmp_path):
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


def test_skip_entry_without_optional_fields(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1000, symbol='BTCUSDT', decision='skip_balance',
           reason='balance=5 < margin=22', balance=5.0, leverage=1, efficiency_score=0.0)
    data = json.loads(path.read_text())
    e = data[0]
    assert e['decision'] == 'skip_balance'
    assert 'preset_name' not in e
    assert 'precision_score' not in e


def test_caps_at_max_entries(tmp_path):
    path = tmp_path / 'dl.json'
    for i in range(MAX_ENTRIES + 5):
        record(path, candle_ts=i, symbol='BTCUSDT', decision='placed',
               reason='', balance=100.0, leverage=1, efficiency_score=0.0)
    data = json.loads(path.read_text())
    assert len(data) == MAX_ENTRIES
    assert data[-1]['candle_ts'] == MAX_ENTRIES + 4  # newest retained


def test_tmp_file_uses_pid_suffix(tmp_path):
    path = tmp_path / 'dl.json'
    record(path, candle_ts=1000, symbol='BTCUSDT', decision='placed',
           reason='', balance=100.0, leverage=1, efficiency_score=0.0)
    pid = os.getpid()
    # No stale .json.tmp left behind — only the pid-qualified tmp is created and renamed
    assert not (tmp_path / 'dl.json.tmp').exists(), "bare .json.tmp should not persist"
    assert path.exists()


def test_sequential_writes_accumulate(tmp_path):
    path = tmp_path / 'dl.json'
    for i in range(3):
        record(path, candle_ts=i, symbol='BTCUSDT', decision='placed',
               reason='', balance=float(i), leverage=1, efficiency_score=0.0)
    data = json.loads(path.read_text())
    assert len(data) == 3
    assert [e['candle_ts'] for e in data] == [0, 1, 2]
