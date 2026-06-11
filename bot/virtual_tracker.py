from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from config.risk_config import load_risk_config

logger = logging.getLogger(__name__)


def _score(stats: dict, min_trades: int, window_size: int) -> tuple[int, float]:
    """Two-tier score with last-N window when warmed up.

    Tier 1: trade_count >= min_trades → ranked by last window_size trade profits
            when recent_trades has enough entries; falls back to cumulative while
            the window is still filling.
    Tier 0: trade_count <  min_trades → ranked by seeded_winning_usdt (backtest).
    Python tuple comparison ensures any (1, x) > any (0, y).
    """
    count = stats.get("trade_count", 0)
    if count >= min_trades:
        recent = stats.get("recent_trades", [])
        if len(recent) >= window_size:
            return (1, sum(recent[-window_size:]))
        # fallback to cumulative while window filling
        return (1, stats.get("total_winning_usdt", 0.0))
    return (0, stats.get("seeded_winning_usdt", 0.0))


_SENTINEL = object()  # distinguishes "never seen" from None in _last_best


class VirtualTracker:
    def __init__(
        self,
        mode: Literal["test", "live"],
        orders_path: Path,
        efficiency_path: Path,
        get_min_trades: Callable[[str], int] = lambda _: 3,
    ) -> None:
        self._mode = mode
        self._orders_path = orders_path
        self._efficiency_path = efficiency_path
        self._get_min_trades = get_min_trades
        self._efficiency: dict = self._load_efficiency()
        self._last_best: dict[str, str | None] = {}
        # Maps symbol → minimum trade_count the current best must reach before it
        # can be displaced (cooldown). In-memory only; resets on bot restart.
        self._cooldown_threshold: dict[str, int] = {}

    def clear_session_data(self, symbols: list[str]) -> None:
        """Wipe in-memory and on-disk efficiency so the new session starts clean."""
        self._efficiency = {}
        if self._efficiency_path.exists():
            try:
                self._efficiency_path.unlink()
            except Exception as exc:
                logger.warning(f"Could not delete efficiency file: {exc}")

    def seed_from_backtest(self, symbol: str, backtest_path: Path) -> None:
        """Seed efficiency scores from backtest results.

        Sets seeded_winning_usdt as a fallback score for preset selection but
        keeps trade_count at 0 so the UI never shows backtest history as if it
        were live virtual trades.
        """
        if not backtest_path.exists():
            logger.warning(f"No backtest file for {symbol}: {backtest_path}")
            return
        try:
            data = json.loads(backtest_path.read_text())
            _leverage_factor = float(load_risk_config().get("backtest_seed_leverage_factor", 1.0))
            for name, preset_data in data.get("presets", {}).items():
                balance_start = preset_data.get("balance_start", 1000.0)
                # Use net total_profit_pct (matches Backtest page Profit% column).
                # Falls back to summing all trade profits if preset-level field is absent.
                # Scale by backtest_seed_leverage_factor so seeded USD is comparable to
                # live PnL accumulated at actual leverage (default 1.0 = no change).
                if "total_profit_pct" in preset_data:
                    seeded = preset_data["total_profit_pct"] / 100.0 * balance_start * _leverage_factor
                else:
                    trades = preset_data.get("trades", [])
                    seeded = sum(t.get("profit_pct", 0.0) / 100.0 * balance_start for t in trades) * _leverage_factor
                # Preserve live-accumulated data across restarts — only refresh the
                # seeded fallback score so rankings improve as live trades accumulate.
                existing = self._efficiency.setdefault(symbol, {}).get(name, {})
                self._efficiency[symbol][name] = {
                    "total_winning_usdt": existing.get("total_winning_usdt", 0.0),
                    "trade_count":        existing.get("trade_count", 0),
                    "seeded_winning_usdt": seeded,
                    "recent_trades":      existing.get("recent_trades", []),
                }
            self._save_efficiency()
        except Exception as exc:
            logger.error(f"Failed to seed efficiency for {symbol}: {exc}")

    def best_preset(self, symbol: str) -> str | None:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return None

        cfg = load_risk_config()
        min_t = self._get_min_trades(symbol)
        window_size = int(cfg.get("ranking_window_size", 10))
        hysteresis_pct = float(cfg.get("preset_hysteresis_pct", 10.0)) / 100.0
        cooldown_trades = int(cfg.get("preset_cooldown_trades", 5))

        # Exclude blocklisted presets from selection so the best eligible preset
        # is returned rather than leaving the symbol deadlocked on a blocked winner.
        _blocklist = set(cfg.get("preset_blocklist", []))
        eligible_data = {n: v for n, v in symbol_data.items() if n not in _blocklist}
        if not eligible_data:
            self._last_best[symbol] = None
            return None

        # Best by score among eligible presets
        best_name = max(eligible_data, key=lambda n: _score(eligible_data[n], min_t, window_size))
        candidate = best_name if _score(eligible_data[best_name], min_t, window_size)[1] >= 0 else None

        prev = self._last_best.get(symbol, _SENTINEL)
        # If the previously-remembered best is now blocklisted, treat it as absent
        # so hysteresis/cooldown don't lock us into an ineligible preset.
        if prev is not _SENTINEL and prev is not None and prev in _blocklist:
            prev = _SENTINEL

        if prev is not _SENTINEL and prev != candidate and prev is not None and candidate is not None:
            prev_stats = symbol_data.get(prev, {})
            cand_stats = symbol_data.get(candidate, {})
            prev_tuple = _score(prev_stats, min_t, window_size)
            cand_tuple = _score(cand_stats, min_t, window_size)

            if cand_tuple[0] == prev_tuple[0]:
                # Same tier — apply hysteresis: candidate must beat prev by ≥ hysteresis_pct
                prev_score = prev_tuple[1]
                cand_score = cand_tuple[1]
                min_improvement = max(abs(prev_score) * hysteresis_pct, 0.50)
                if cand_score < prev_score + min_improvement:
                    logger.debug(
                        f"[{symbol}] Hysteresis blocked swap {prev!r}->{candidate!r}: "
                        f"score {cand_score:.2f} needs >{prev_score + min_improvement:.2f}"
                    )
                    candidate = prev

            # Cooldown check: prev must have enough trades since it became best
            if candidate != prev:
                threshold = self._cooldown_threshold.get(symbol, 0)
                prev_count = prev_stats.get("trade_count", 0)
                if prev_count < threshold:
                    logger.debug(
                        f"[{symbol}] Cooldown blocked swap {prev!r}->{candidate!r}: "
                        f"trades {prev_count} < threshold {threshold}"
                    )
                    candidate = prev

        # A swap is happening — set cooldown for the new best
        if prev is not _SENTINEL and prev != candidate and candidate is not None:
            new_count = symbol_data.get(candidate, {}).get("trade_count", 0)
            self._cooldown_threshold[symbol] = new_count + cooldown_trades

        result = candidate

        if prev is not _SENTINEL and prev != result:
            prev_stats = symbol_data.get(prev or '', {})
            new_stats = symbol_data.get(result or '', {})
            logger.info(
                f"[{symbol}] Best preset changed: {prev!r} -> {result!r} | "
                f"prev(cnt={prev_stats.get('trade_count', 0)}, "
                f"seeded={prev_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={prev_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(prev_stats, min_t, window_size)}) | "
                f"new(cnt={new_stats.get('trade_count', 0)}, "
                f"seeded={new_stats.get('seeded_winning_usdt', 0.0):.2f}, "
                f"live={new_stats.get('total_winning_usdt', 0.0):.2f}, "
                f"score={_score(new_stats, min_t, window_size)})"
            )
        self._last_best[symbol] = result
        return result

    def get_efficiency(self, symbol: str, preset: str) -> dict:
        return self._efficiency.get(symbol, {}).get(preset, {"total_winning_usdt": 0.0, "trade_count": 0, "recent_trades": []})

    def get_efficiency_score(self, symbol: str) -> float:
        symbol_data = self._efficiency.get(symbol, {})
        if not symbol_data:
            return 0.0
        cfg = load_risk_config()
        min_t = self._get_min_trades(symbol)
        window_size = int(cfg.get("ranking_window_size", 10))
        best_tuple = max(_score(stats, min_t, window_size) for stats in symbol_data.values())
        return best_tuple[1]

    def get_preset_efficiency(self, symbol: str, preset_name: str) -> float:
        stats = self._efficiency.get(symbol, {}).get(preset_name, {})
        cfg = load_risk_config()
        window_size = int(cfg.get("ranking_window_size", 10))
        return _score(stats, self._get_min_trades(symbol), window_size)[1]

    def record_closed_trade(self, symbol: str, preset: str, profit_usdt: float) -> None:
        eff = self.get_efficiency(symbol, preset)
        cfg = load_risk_config()
        window_size = int(cfg.get("ranking_window_size", 10))
        recent_trades = list(eff.get("recent_trades", []))
        recent_trades.append(profit_usdt)
        recent_trades = recent_trades[-window_size:]
        self._set_efficiency(
            symbol, preset,
            total_winning=eff["total_winning_usdt"] + profit_usdt,
            count=eff["trade_count"] + 1,
            recent_trades=recent_trades,
        )

    def is_tats_eligible(self, symbol: str, locked_preset: str | None = None) -> bool:
        """True if the symbol may place real orders under the TATS scenario.

        Tier-0 (seed phase, trade_count < min_trades_for_ranking): always eligible — BGF fallback.
        Tier-1: eligible only if recent-window score >= tats_min_profit_usdt (Part A) AND
                the second half of the recent window has not dropped >tats_degradation_max_drop_pct%
                relative to the first half (Part B).

        For locked symbols, evaluates the locked preset's stats rather than the best-overall,
        because only the locked preset actually trades.
        """
        cfg = load_risk_config()
        min_trades = self._get_min_trades(symbol)
        window_size = int(cfg.get("ranking_window_size", 10))
        min_profit = float(cfg.get("tats_min_profit_usdt", 0.0))
        max_drop_pct = float(cfg.get("tats_degradation_max_drop_pct", 50.0))

        presets = self._efficiency.get(symbol, {})
        if not presets:
            return True  # no data → BGF fallback

        if locked_preset and locked_preset in presets:
            check_stats = presets[locked_preset]
            tier, score = _score(check_stats, min_trades, window_size)
        else:
            best_name = max(presets, key=lambda n: _score(presets[n], min_trades, window_size))
            check_stats = presets[best_name]
            tier, score = _score(check_stats, min_trades, window_size)

        if tier == 0:
            return True  # seed phase → BGF fallback

        # Part A: recent window sum must be profitable enough
        if score < min_profit:
            return False

        # Part B: degradation check — second half must not drop >max_drop_pct% vs first half
        if max_drop_pct > 0:
            recent = check_stats.get("recent_trades", [])[-window_size:]
            if len(recent) >= 4:
                mid = len(recent) // 2
                first_half = sum(recent[:mid])
                second_half = sum(recent[mid:])
                allowed_floor = first_half - abs(first_half) * max_drop_pct / 100.0
                if second_half < allowed_floor:
                    return False

        return True

    def is_virtual_only(self, symbol: str) -> bool:
        """Return True if the symbol's best preset score is below virtual_only_floor.
        Only activates once the best preset has enough live trades (>= min_trades_for_ranking).
        """
        cfg = load_risk_config()
        floor = float(cfg.get("virtual_only_floor", -20.0))
        min_trades = int(cfg.get("min_trades_for_ranking", 3))

        presets = self._efficiency.get(symbol, {})
        if not presets:
            return False

        best_score = None
        best_count = 0
        window_size = int(cfg.get("ranking_window_size", 10))
        for stats in presets.values():
            tier, score = _score(stats, min_trades, window_size)
            if tier > 0:  # only live-ranked presets trigger the floor
                best_count = max(best_count, stats.get("trade_count", 0))
                if best_score is None or score > best_score:
                    best_score = score

        # Gate only activates once we have real live data
        if best_count < min_trades or best_score is None:
            return False

        return best_score < floor

    def _set_efficiency(self, symbol: str, preset: str, total_winning: float, count: int, recent_trades: list | None = None) -> None:
        existing = self._efficiency.get(symbol, {}).get(preset, {})
        if recent_trades is None:
            recent_trades = existing.get("recent_trades", [])
        self._efficiency.setdefault(symbol, {})[preset] = {
            "total_winning_usdt": total_winning,
            "trade_count": count,
            "seeded_winning_usdt": existing.get("seeded_winning_usdt", 0.0),
            "recent_trades": recent_trades,
        }
        self._save_efficiency()

    def _load_efficiency(self) -> dict:
        if self._efficiency_path.exists():
            try:
                data = json.loads(self._efficiency_path.read_text())
                for symbol_presets in data.values():
                    for record in symbol_presets.values():
                        record.setdefault("recent_trades", [])
                return data
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return {}

    def _save_efficiency(self) -> None:
        self._efficiency_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._efficiency_path.with_name(f"{self._efficiency_path.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self._efficiency, indent=2))
        tmp.replace(self._efficiency_path)
