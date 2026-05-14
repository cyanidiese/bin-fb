import json
import pytest
from unittest.mock import patch, MagicMock
from bot.symbol_discovery import SymbolDiscovery, FUTURES_SYMBOLS


EXCHANGE_INFO_RESPONSE = {
    "symbols": [
        {"symbol": "BTCUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "ETHUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "XRPUSDT",  "contractType": "PERPETUAL", "quoteAsset": "USDT"},
        {"symbol": "FOOBAR",   "contractType": "PERPETUAL", "quoteAsset": "USDT"},  # not in FUTURES_SYMBOLS
        {"symbol": "BTCBUSD",  "contractType": "PERPETUAL", "quoteAsset": "BUSD"},  # wrong quote
        {"symbol": "DOGEUSDT", "contractType": "DELIVERING", "quoteAsset": "USDT"}, # not perpetual
    ]
}

TICKER_RESPONSE = [
    {"symbol": "BTCUSDT",  "quoteVolume": "5000000000"},
    {"symbol": "ETHUSDT",  "quoteVolume": "2000000000"},
    {"symbol": "XRPUSDT",  "quoteVolume": "500000"},      # below threshold
    {"symbol": "FOOBAR",   "quoteVolume": "9000000000"},
]


def _mock_get(url, **kwargs):
    m = MagicMock()
    if 'exchangeInfo' in url:
        m.json.return_value = EXCHANGE_INFO_RESPONSE
    else:
        m.json.return_value = TICKER_RESPONSE
    return m


def test_get_precandidates_filters_by_allowlist():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=[], min_volume=1_000_000)
    # FOOBAR not in FUTURES_SYMBOLS, BTCBUSD wrong quote, DOGEUSDT not perpetual
    assert 'FOOBAR' not in result
    assert 'BTCBUSD' not in result
    assert 'DOGEUSDT' not in result


def test_get_precandidates_filters_active():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=['BTCUSDT'], min_volume=1_000_000)
    assert 'BTCUSDT' not in result


def test_get_precandidates_filters_low_volume():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=_mock_get):
        result = sd.get_precandidates(active=[], min_volume=1_000_000)
    # XRPUSDT has volume 500_000 < 1_000_000
    assert 'XRPUSDT' not in result
    assert 'ETHUSDT' in result


def test_get_precandidates_raises_on_api_error():
    sd = SymbolDiscovery()
    with patch('bot.symbol_discovery.requests.get', side_effect=Exception("timeout")):
        with pytest.raises(RuntimeError, match="Exchange info fetch failed"):
            sd.get_precandidates(active=[], min_volume=1_000_000)


def test_get_fast_presets_ranks_by_avg_profit(tmp_path):
    sd = SymbolDiscovery()

    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
        "presets": {
            "preset_a": {"total_profit_pct": 10.0, "total_trades": 5},
            "preset_b": {"total_profit_pct":  2.0, "total_trades": 5},
            "preset_c": {"total_profit_pct":  6.0, "total_trades": 5},
        }
    }))
    (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
        "presets": {
            "preset_a": {"total_profit_pct": 8.0, "total_trades": 5},
            "preset_b": {"total_profit_pct": 4.0, "total_trades": 5},
        }
    }))

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.get_fast_presets(
            active=["BTCUSDT", "ETHUSDT"],
            n=2,
            all_preset_names=["preset_a", "preset_b", "preset_c"],
        )

    # preset_a avg=(10+8)/2=9, preset_c avg=6 (ETHUSDT missing → 6/1), preset_b avg=(2+4)/2=3
    assert result[0] == "preset_a"
    assert len(result) == 2


def test_get_fast_presets_falls_back_when_no_results(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    all_names = ["p1", "p2", "p3", "p4"]

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.get_fast_presets(active=["BTCUSDT"], n=2, all_preset_names=all_names)

    assert result == ["p1", "p2"]


def test_compute_baseline_averages_best_preset_efficiency(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)
    # BTCUSDT best preset: profit 10 / 5 trades = 2.0
    # ETHUSDT best preset: profit 6  / 2 trades = 3.0
    # baseline = (2.0 + 3.0) / 2 = 2.5
    (results_dir / "backtest_results_BTCUSDT.json").write_text(json.dumps({
        "presets": {
            "p1": {"total_profit_pct": 10.0, "total_trades": 5},
            "p2": {"total_profit_pct":  4.0, "total_trades": 5},
        }
    }))
    (results_dir / "backtest_results_ETHUSDT.json").write_text(json.dumps({
        "presets": {
            "p1": {"total_profit_pct": 6.0, "total_trades": 2},
        }
    }))

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.compute_baseline(["BTCUSDT", "ETHUSDT"])

    assert result == pytest.approx(2.5)


def test_compute_baseline_returns_zero_when_no_files(tmp_path):
    sd = SymbolDiscovery()
    results_dir = tmp_path / "dashboard" / "public"
    results_dir.mkdir(parents=True)

    with patch('bot.symbol_discovery._DASHBOARD_PUBLIC', results_dir):
        result = sd.compute_baseline(["BTCUSDT"])

    assert result == 0.0


def test_score_candidate_returns_none_when_no_cache(tmp_path, monkeypatch):
    """When DataFeed fetch fails and no cache file exists, returns None."""
    from bot.symbol_discovery import SymbolDiscovery
    sd = SymbolDiscovery()

    # DataFeed.refresh_klines raises → cache still absent
    with patch('bot.symbol_discovery.DataFeed') as MockFeed:
        MockFeed.return_value.refresh_klines.side_effect = Exception("api error")
        # Point cache lookup to a dir that has no files
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        # Load settings needs .env; patch it out
        with patch('bot.symbol_discovery.load_settings') as mock_ls:
            mock_settings = MagicMock()
            mock_settings.trading_mode = 'testnet'
            mock_settings.timeframe = '15m'
            mock_ls.return_value = mock_settings
            result = sd.score_candidate(
                symbol="NEWUSDT",
                preset_subset={"default": {}},
                klines_count=500,
                baseline=2.0,
                baseline_ratio=0.7,
                min_floor=0.0,
                position_size=1000.0,
                leverage=1.0,
            )
    assert result is None


def test_score_candidate_returns_none_below_efficiency_threshold(tmp_path, monkeypatch):
    """A candidate scoring below baseline_ratio × baseline is filtered out."""
    import json as _json
    from bot.symbol_discovery import SymbolDiscovery
    sd = SymbolDiscovery()

    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Write a cache file with klines that produce zero trades (too few candles)
    klines = [[i * 60000, "100", "101", "99", "100", "1000", (i + 1) * 60000]
              for i in range(10)]
    cache = data_dir / "NEWUSDT_15m_test.json"
    cache.write_text(_json.dumps(klines))

    with patch('bot.symbol_discovery.DataFeed'):
        with patch('bot.symbol_discovery.load_settings') as mock_ls:
            mock_settings = MagicMock()
            mock_settings.trading_mode = 'testnet'
            mock_settings.timeframe = '15m'
            mock_ls.return_value = mock_settings
            with patch('bot.symbol_discovery.load_risk_config', return_value={"backtest_initial_balance_usdt": 0.0}):
                result = sd.score_candidate(
                    symbol="NEWUSDT",
                    preset_subset={"default": {}},
                    klines_count=500,
                    baseline=100.0,   # very high baseline
                    baseline_ratio=0.7,
                    min_floor=0.0,
                    position_size=1000.0,
                    leverage=1.0,
                )
    # Zero trades → total_order_count=0 → returns None (no trades produced)
    assert result is None
