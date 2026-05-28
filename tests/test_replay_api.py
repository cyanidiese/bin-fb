"""Tests for replay_api.replay()."""
import json
import pytest
from pathlib import Path


def _make_klines(n: int, base_price: float = 100.0) -> list:
    """n synthetic 15-minute klines in dashboard JSON format."""
    base_ts = 1_700_000_000  # Unix seconds
    klines = []
    p = base_price
    for i in range(n):
        klines.append({
            'time': base_ts + i * 900,
            'open': round(p, 4),
            'high': round(p + 1.0, 4),
            'low':  round(p - 1.0, 4),
            'close': round(p + 0.5, 4),
        })
        p += 0.1
    return klines


def _write_results(path: Path, symbol: str, klines: list) -> None:
    data = {
        'symbol': symbol,
        'timeframe': '15m',
        'mode': 'testnet',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'current_price': klines[-1]['close'] if klines else 0.0,
        'trend_levels': [],
        'all_points': [],
        'klines': klines,
        'signals': [],
        'best_signal': None,
    }
    (path / f'results_{symbol}.json').write_text(json.dumps(data))


def test_replay_returns_correct_shape(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(60))

    result = replay_api.replay('TESTUSDT', 59)

    assert 'trend_levels' in result
    assert 'all_points' in result
    assert 'signals' in result
    assert isinstance(result['trend_levels'], list)
    assert isinstance(result['all_points'], list)
    assert isinstance(result['signals'], list)


def test_replay_respects_candle_index(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(200))

    result_small = replay_api.replay('TESTUSDT', 5)
    result_large = replay_api.replay('TESTUSDT', 199)

    assert len(result_large['all_points']) >= len(result_small['all_points'])


def test_replay_candle_index_beyond_length_uses_all(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(50))

    result = replay_api.replay('TESTUSDT', 9999)
    assert 'trend_levels' in result


def test_replay_zero_candles_returns_empty(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)
    _write_results(tmp_path, 'TESTUSDT', _make_klines(50))

    assert replay_api.replay('TESTUSDT', -1) == {'trend_levels': [], 'all_points': [], 'signals': []}
    assert replay_api.replay('TESTUSDT', -2) == {'trend_levels': [], 'all_points': [], 'signals': []}


def test_replay_missing_file_raises(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)

    with pytest.raises(FileNotFoundError):
        replay_api.replay('NONEXISTENT', 10)


def test_replay_invalid_symbol_raises(tmp_path, monkeypatch):
    import replay_api
    monkeypatch.setattr(replay_api, 'RESULTS_DIR', tmp_path)

    with pytest.raises(ValueError):
        replay_api.replay('../etc', 10)
