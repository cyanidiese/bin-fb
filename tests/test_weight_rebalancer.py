import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from bot.weight_rebalancer import WeightRebalancer


def _make_rebalancer(**cfg_overrides):
    """Build a WeightRebalancer with all deps mocked."""
    cfg = {
        "enabled": True,
        "rebalance_candles": 4,
        "backtest_window_candles": 4,
        "real_pnl_alpha": 0.5,
        "blend_rate": 0.2,
        "weight_floor_ratio": 0.3,
        **cfg_overrides,
    }
    sym_reg = MagicMock()
    risk_mgr = MagicMock()
    settings = MagicMock()
    return WeightRebalancer(
        symbol_registry=sym_reg,
        risk_manager=risk_mgr,
        settings=settings,
        get_klines_fn=lambda s: [],
        candle_duration_ms=900_000,
        mode="test",
        risk_config_path=Path("/tmp/rc.json"),
        data_dir=Path("/tmp/data"),
        cfg=cfg,
    )


class TestRankNormalize:
    def test_three_values_ranked(self):
        r = _make_rebalancer()
        values = {"A": 10.0, "B": 5.0, "C": 1.0}
        result = r._rank_normalize(values)
        assert result["A"] == pytest.approx(1.0)
        assert result["C"] == pytest.approx(0.0)
        assert result["B"] == pytest.approx(0.5)

    def test_all_equal_scores_midpoint(self):
        r = _make_rebalancer()
        values = {"A": 3.0, "B": 3.0, "C": 3.0}
        result = r._rank_normalize(values)
        for v in result.values():
            assert v == pytest.approx(0.5)

    def test_single_symbol_returns_one(self):
        r = _make_rebalancer()
        result = r._rank_normalize({"X": 7.5})
        assert result["X"] == pytest.approx(1.0)

    def test_empty_returns_empty(self):
        r = _make_rebalancer()
        assert r._rank_normalize({}) == {}


class TestFilterRealOrders:
    def _write_orders(self, tmp_path: Path, symbol: str, orders: list) -> None:
        path = tmp_path / f"real_orders_{symbol}_test.json"
        path.write_text(json.dumps(orders))

    def test_returns_orders_in_window(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        recent_ct = datetime.fromtimestamp((now_ms - 1000) / 1000, tz=timezone.utc).isoformat()
        old_ct = datetime.fromtimestamp((now_ms - 99999999) / 1000, tz=timezone.utc).isoformat()
        self._write_orders(tmp_path, "BTCUSDT", [
            {"close_time": recent_ct, "pnl_usdt": 5.0},
            {"close_time": old_ct, "pnl_usdt": -2.0},
        ])
        result = r._filter_real_orders("BTCUSDT", window_start_ms=now_ms - 10000)
        assert len(result) == 1
        assert result[0]["pnl_usdt"] == 5.0

    def test_missing_file_returns_empty(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        result = r._filter_real_orders("XYZUSDT", window_start_ms=0)
        assert result == []

    def test_all_old_orders_excluded(self, tmp_path):
        r = _make_rebalancer()
        r._data_dir = tmp_path
        old_ct = "2020-01-01T00:00:00+00:00"
        self._write_orders(tmp_path, "ETHUSDT", [
            {"close_time": old_ct, "pnl_usdt": 10.0},
        ])
        result = r._filter_real_orders("ETHUSDT", window_start_ms=int(datetime.now(timezone.utc).timestamp() * 1000))
        assert result == []


class TestCalcScores:
    def test_equal_signals_equal_scores(self):
        r = _make_rebalancer()
        bt = {"A": 2.0, "B": 2.0}
        pnl = {"A": 0.0, "B": 0.0}
        scores = r._calc_scores(bt, pnl, alpha=0.5)
        assert scores["A"] == pytest.approx(scores["B"])

    def test_better_backtest_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 5.0, "B": 1.0}
        pnl = {"A": 0.0, "B": 0.0}
        scores = r._calc_scores(bt, pnl, alpha=0.0)  # backtest only
        assert scores["A"] > scores["B"]

    def test_better_pnl_gets_higher_score(self):
        r = _make_rebalancer()
        bt = {"A": 0.0, "B": 0.0}
        pnl = {"A": 10.0, "B": -3.0}
        scores = r._calc_scores(bt, pnl, alpha=1.0)  # real P&L only
        assert scores["A"] > scores["B"]
