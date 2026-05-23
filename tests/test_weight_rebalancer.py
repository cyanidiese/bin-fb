import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestBlendWeights:
    def test_blend_moves_toward_score(self):
        r = _make_rebalancer()
        current = {"A": 0.5, "B": 0.5}
        scores = {"A": 1.0, "B": 0.0}
        result = r._blend_weights(current, scores, blend_rate=0.2, floor_ratio=0.0)
        assert result["A"] > 0.5
        assert result["B"] < 0.5

    def test_floor_prevents_low_weight(self):
        r = _make_rebalancer()
        current = {"A": 0.9, "B": 0.1}
        scores = {"A": 1.0, "B": 0.0}
        # Without floor B would blend to ~0.05; floor=0.3/2=0.15 lifts it before renorm.
        # After the final renorm B lands at ~0.136, which must exceed the no-floor value.
        result_with_floor = r._blend_weights(current, scores, blend_rate=0.5, floor_ratio=0.3)
        result_no_floor = r._blend_weights(current, scores, blend_rate=0.5, floor_ratio=0.0)
        assert result_with_floor["B"] > result_no_floor["B"]

    def test_weights_sum_to_one(self):
        r = _make_rebalancer()
        current = {"A": 0.4, "B": 0.3, "C": 0.3}
        scores = {"A": 0.9, "B": 0.05, "C": 0.05}
        result = r._blend_weights(current, scores, blend_rate=0.15, floor_ratio=0.3)
        assert sum(result.values()) == pytest.approx(1.0)

    def test_empty_scores_returns_current(self):
        r = _make_rebalancer()
        current = {"A": 0.6, "B": 0.4}
        result = r._blend_weights(current, {}, blend_rate=0.15, floor_ratio=0.3)
        assert result == current

    def test_absent_symbol_gets_equal_share_default(self):
        r = _make_rebalancer()
        # "A" not in current_weights — should get 1/n = 0.5 as starting point
        current = {}
        scores = {"A": 1.0, "B": 0.0}
        result = r._blend_weights(current, scores, blend_rate=0.2, floor_ratio=0.0)
        # A should end up with more than B
        assert result["A"] > result["B"]


class TestTrigger:
    def test_fires_after_n_candles(self):
        r = _make_rebalancer(rebalance_candles=3)
        r.enabled = True
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            for i in range(2):
                r.on_candle_close(1000 + i * 900_000)
            MockThread.assert_not_called()
            r.on_candle_close(1000 + 2 * 900_000)
            MockThread.assert_called_once()

    def test_skips_when_already_running(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = True
        r._running.set()
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()

    def test_no_op_when_disabled(self):
        r = _make_rebalancer(rebalance_candles=1)
        r.enabled = False
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            r.on_candle_close(1000)
            MockThread.assert_not_called()

    def test_same_candle_ts_counted_once(self):
        r = _make_rebalancer(rebalance_candles=2)
        r.enabled = True
        with patch("bot.weight_rebalancer.threading.Thread") as MockThread:
            # Call 3 times with same ts (simulating 3 symbols in same candle)
            for _ in range(3):
                r.on_candle_close(1000)
            MockThread.assert_not_called()  # still only 1 candle counted
            # Now a new candle ts — should trigger (counter hits 2)
            r.on_candle_close(1000 + 900_000)
            MockThread.assert_called_once()
