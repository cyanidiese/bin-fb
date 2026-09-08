"""A closed WS candle must be stored with the same 12 fields REST returns.

Both WS handlers built 7 fields, so caches ended up holding two shapes -- measured on the
server: SOLUSDT_15m_test had 4935 rows of 12 and 65 of 7. Nothing reads index > 6 today
(the analyser uses [4]; data_feed uses [0] and [6]), so it was harmless, but it is a
latent IndexError for the first feature that wants trade count or taker volume, and it
discarded data the exchange already sent for free.

The WS->REST mapping was verified against the live stream on 2026-09-08: every field is
present and the types match (t/T/n int, the rest str).

See docs/specs/2026-09-08-watchdog-guard-and-full-ws-klines.md.
"""
import json
from unittest.mock import MagicMock

import pytest

from bot.data_feed import DataFeed, _KLINE_FIELDS


def _feed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = MagicMock()
    s.trading_mode = 'test'
    s.api_key = ''
    s.api_secret = ''
    s.kline_cache_limit = 5000
    monkeypatch.setattr('bot.data_feed.Client', MagicMock())
    return DataFeed(s)


# A real payload captured from the live stream, trimmed to the fields we map.
WS_K = {
    't': 1788861420000, 'T': 1788861479999, 's': 'BTCUSDT', 'i': '1m',
    'o': '78723.50', 'c': '78728.00', 'h': '78728.00', 'l': '78711.90',
    'v': '0.4264', 'n': 47, 'x': True,
    'q': '33564.816170', 'V': '0.1240', 'Q': '9762.232030', 'B': '0',
}

# A real REST row, for type comparison.
REST_ROW = [
    1784360700000, '74.9900', '75.0100', '74.8700', '74.9300', '29826.93',
    1784361599999, '2234802.866500', 3210, '12730.28', '953832.296400', '0',
]


class TestMapping:
    def test_the_row_has_all_twelve_fields(self):
        assert len(DataFeed._ws_kline_to_row(WS_K)) == _KLINE_FIELDS

    def test_every_field_type_matches_a_rest_row(self):
        row = DataFeed._ws_kline_to_row(WS_K)
        assert [type(v) for v in row] == [type(v) for v in REST_ROW]

    def test_the_values_land_on_the_right_indices(self):
        row = DataFeed._ws_kline_to_row(WS_K)
        assert row[0] == 1788861420000        # open time
        assert row[1] == '78723.50'           # open
        assert row[2] == '78728.00'           # high
        assert row[3] == '78711.90'           # low
        assert row[4] == '78728.00'           # close
        assert row[5] == '0.4264'             # base volume
        assert row[6] == 1788861479999        # close time
        assert row[7] == '33564.816170'       # quote volume
        assert row[8] == 47                   # trade count
        assert row[9] == '0.1240'             # taker buy base
        assert row[10] == '9762.232030'       # taker buy quote

    def test_close_is_still_index_4(self):
        """The analyser reads [4]; getting this wrong would corrupt every indicator."""
        assert DataFeed._ws_kline_to_row(WS_K)[4] == WS_K['c']

    def test_a_missing_optional_field_does_not_raise(self):
        """A schema change must degrade to a short row, never crash the stream loop."""
        k = {k_: v for k_, v in WS_K.items() if k_ not in ('q', 'V', 'Q', 'B', 'n')}
        row = DataFeed._ws_kline_to_row(k)
        assert row[:7] == [1788861420000, '78723.50', '78728.00', '78711.90',
                           '78728.00', '0.4264', 1788861479999]


class TestTwoVersionsOnDisk:
    """Legacy 7-field rows and new 12-field rows coexist until the cache rotates."""

    def test_short_rows_are_padded_on_read(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path, monkeypatch)
        path = tmp_path / 'data' / 'MIX_15m_test.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = [1, '2', '3', '4', '5', '6', 7]
        path.write_text(json.dumps([legacy, REST_ROW]))
        rows = feed._read_cache(path)
        assert all(len(r) == _KLINE_FIELDS for r in rows)

    def test_padding_is_none_not_zero(self, tmp_path, monkeypatch):
        """A consumer must tell 'predates full capture' from 'genuinely zero volume'.
        Zeros would let an average over these fields quietly return a wrong number."""
        feed = _feed(tmp_path, monkeypatch)
        path = tmp_path / 'data' / 'MIX_15m_test.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([[1, '2', '3', '4', '5', '6', 7]]))
        assert feed._read_cache(path)[0][7:] == [None] * 5

    def test_the_first_seven_fields_are_untouched(self, tmp_path, monkeypatch):
        """No consumer of index 0-6 may change behaviour."""
        feed = _feed(tmp_path, monkeypatch)
        path = tmp_path / 'data' / 'MIX_15m_test.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = [1, '2', '3', '4', '5', '6', 7]
        path.write_text(json.dumps([legacy]))
        assert feed._read_cache(path)[0][:7] == legacy

    def test_full_rows_pass_through_unchanged(self, tmp_path, monkeypatch):
        feed = _feed(tmp_path, monkeypatch)
        path = tmp_path / 'data' / 'MIX_15m_test.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([REST_ROW]))
        assert feed._read_cache(path)[0] == REST_ROW

    def test_a_ws_candle_round_trips_through_the_cache(self, tmp_path, monkeypatch):
        """append_kline writes it; _read_cache reads it back at full width."""
        feed = _feed(tmp_path, monkeypatch)
        feed.append_kline('BTCUSDT', '15m', DataFeed._ws_kline_to_row(WS_K))
        rows = feed._read_cache(feed._cache_path('BTCUSDT', '15m'))
        assert len(rows) == 1 and len(rows[0]) == _KLINE_FIELDS
        assert rows[0][8] == 47
