import json
import pytest
from pathlib import Path
from bot.balance_history import record, MAX_ENTRIES


def test_creates_file_on_first_write(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=1000.0, trigger='startup')
    assert path.exists()


def test_startup_entry_shape(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=500.0, trigger='startup')
    data = json.loads(path.read_text())
    assert len(data) == 1
    e = data[0]
    assert e['balance'] == 500.0
    assert e['trigger'] == 'startup'
    assert 'timestamp' in e
    assert 'symbol' not in e   # optional field absent when not provided


def test_order_close_entry_includes_pnl(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=1010.0, trigger='order_close',
           symbol='BTCUSDT', leverage=2, pnl_usdt=10.0)
    data = json.loads(path.read_text())
    e = data[0]
    assert e['trigger'] == 'order_close'
    assert e['symbol'] == 'BTCUSDT'
    assert e['leverage'] == 2
    assert e['pnl_usdt'] == 10.0


def test_appends_multiple_entries(tmp_path):
    path = tmp_path / 'bh.json'
    record(path, balance=100.0, trigger='startup')
    record(path, balance=110.0, trigger='order_close')
    data = json.loads(path.read_text())
    assert len(data) == 2


def test_caps_at_max_entries(tmp_path):
    path = tmp_path / 'bh.json'
    for i in range(MAX_ENTRIES + 5):
        record(path, balance=float(i), trigger='startup')
    data = json.loads(path.read_text())
    assert len(data) == MAX_ENTRIES
    assert data[-1]['balance'] == float(MAX_ENTRIES + 4)  # newest is last
