from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config.risk_config import load_risk_config, save_risk_config
from config.settings import Settings

logger = logging.getLogger(__name__)

_LOG_MAX = 50


class WeightRebalancer:
    def __init__(
        self,
        symbol_registry,
        risk_manager,
        settings: Settings,
        get_klines_fn: Callable[[str], list],
        candle_duration_ms: int,
        mode: str,
        risk_config_path: Path,
        data_dir: Path,
        cfg: dict,
    ) -> None:
        self._registry = symbol_registry
        self._risk = risk_manager
        self._settings = settings
        self._get_klines = get_klines_fn
        self._candle_ms = candle_duration_ms
        self._mode = mode
        self._config_path = risk_config_path
        self._data_dir = data_dir
        self._cfg = cfg
        self._counter = 0
        self._running = threading.Event()  # set while a rebalance is in progress
        self.enabled: bool = bool(cfg.get("enabled", False))

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def on_candle_close(self, candle_ts: int) -> None:
        if not self.enabled:
            return
        self._counter += 1
        n = int(self._cfg.get("rebalance_candles", 96))
        if self._counter % n != 0:
            return
        if self._running.is_set():
            logger.warning("WeightRebalancer: previous rebalance still running — skipping")
            return
        self._running.set()
        t = threading.Thread(target=self._run, args=(candle_ts,), daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    # Pure helpers (no I/O, easily unit-tested)                           #
    # ------------------------------------------------------------------ #

    def _rank_normalize(self, values: dict[str, float]) -> dict[str, float]:
        """Map values to [0, 1] by rank. Best → 1.0, worst → 0.0. Ties share midpoint rank."""
        if not values:
            return {}
        n = len(values)
        if n == 1:
            return {k: 1.0 for k in values}
        sorted_keys = sorted(values, key=lambda k: values[k], reverse=True)
        result: dict[str, float] = {}
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[sorted_keys[j]] == values[sorted_keys[j + 1]]:
                j += 1
            mid_rank_score = 1.0 - (i + j) / 2.0 / (n - 1)
            for k in sorted_keys[i:j + 1]:
                result[k] = mid_rank_score
            i = j + 1
        return result

    def _filter_real_orders(self, symbol: str, window_start_ms: int) -> list[dict]:
        """Load real_orders file for symbol; return only orders closed within the window."""
        path = self._data_dir / f"real_orders_{symbol}_{self._mode}.json"
        if not path.exists():
            return []
        try:
            records: list[dict] = json.loads(path.read_text())
        except Exception:
            return []
        result = []
        for r in records:
            ct = r.get("close_time")
            if not ct:
                continue
            try:
                close_ms = int(datetime.fromisoformat(ct).timestamp() * 1000)
            except Exception:
                continue
            if close_ms >= window_start_ms:
                result.append(r)
        return result

    def _calc_scores(
        self,
        backtest_pcts: dict[str, float],
        real_pnls: dict[str, float],
        alpha: float,
    ) -> dict[str, float]:
        """Combine rank-normalized backtest and real-P&L signals into a single score."""
        bt_norm = self._rank_normalize(backtest_pcts)
        pnl_norm = self._rank_normalize(real_pnls)
        symbols = set(backtest_pcts) | set(real_pnls)
        return {
            s: alpha * pnl_norm.get(s, 0.5) + (1.0 - alpha) * bt_norm.get(s, 0.5)
            for s in symbols
        }

    def _blend_weights(
        self,
        current_weights: dict[str, float],
        scores: dict[str, float],
        blend_rate: float,
        floor_ratio: float,
    ) -> dict[str, float]:
        """Soft-blend current weights toward scores, clamp floor, renormalize."""
        symbols = list(scores)
        n = len(symbols)
        if n == 0:
            return current_weights
        floor = floor_ratio / n
        new_w: dict[str, float] = {}
        for s in symbols:
            old = current_weights.get(s, 1.0 / n)
            new_w[s] = (1.0 - blend_rate) * old + blend_rate * scores[s]
        total = sum(new_w.values()) or 1.0
        new_w = {s: w / total for s, w in new_w.items()}
        clamped = {s: max(floor, w) for s, w in new_w.items()}
        total2 = sum(clamped.values()) or 1.0
        return {s: w / total2 for s, w in clamped.items()}

    # ------------------------------------------------------------------ #
    # Background execution (implemented in Task 4)                        #
    # ------------------------------------------------------------------ #

    def _run(self, trigger_ts: int) -> None:
        try:
            self._do_rebalance(trigger_ts)
        except Exception:
            logger.exception("WeightRebalancer: unhandled error during rebalance")
        finally:
            self._running.clear()

    def _do_rebalance(self, trigger_ts: int) -> None:
        pass  # implemented in Task 4

    def _score_symbol(self, symbol: str, window_candles: int, window_start_ms: int, presets: dict) -> tuple[float, float]:
        return 0.0, 0.0  # implemented in Task 4

    def _append_log(self, trigger_ts, symbols, backtest_pcts, real_pnls, scores, old_weights, new_weights) -> None:
        pass  # implemented in Task 4
