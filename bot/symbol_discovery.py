"""
Symbol Discovery — finds, backtests, and scores new candidate symbols.

The SymbolDiscovery class contains pure logic only (no daemon threads,
no side effects beyond reading backtest result files).  All I/O and
orchestration live in discover.py.
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from config.settings import load_settings
from config.risk_config import load_risk_config, _CONFIG_PATH as _RISK_CONFIG_PATH
from bot.data_feed import DataFeed
from bot.backtester import Backtester

logger = logging.getLogger(__name__)

FUTURES_SYMBOLS: list[str] = [
    # Major / Large Cap
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
    "LTCUSDT", "LINKUSDT", "UNIUSDT", "ATOMUSDT", "NEARUSDT",
    # Mid Cap / High Volume
    "AAVEUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
    "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT", "WLDUSDT",
    "JUPUSDT", "RENDERUSDT", "FETUSDT", "AGIXUSDT", "OCEANUSDT",
    # Meme / High Volatility
    "PEPEUSDT", "SHIBUSDT", "FLOKIUSDT", "BONKUSDT", "WIFUSDT",
    "MEMEUSDT", "TRUMPUSDT", "1000SHIBUSDT", "1000PEPEUSDT",
    # DeFi
    "ENAUSDT", "EIGENUSDT", "ETHFIUSDT", "REZUSDT",
    # Layer 1 / Layer 2
    "STXUSDT", "RUNEUSDT", "THETAUSDT", "ALGOUSDT", "FLOWUSDT",
    "ICPUSDT", "FTMUSDT", "HBARUSDT", "EGLDUSDT", "XTZUSDT",
]

_FAPI_BASE = "https://fapi.binance.com/fapi/v1"
_DASHBOARD_PUBLIC = Path("dashboard") / "public"


@dataclass
class CandidateResult:
    symbol: str
    efficiency_score: float
    total_net_profit: float
    total_order_count: int
    profit_factor: float
    best_preset_id: str
    best_preset_profit_pct: float
    win_rate: float
    max_drawdown: int
    sharpe_ratio: float
    baseline_efficiency: float
    vs_baseline_pct: float
    potential_gain_usdt: float

    def to_dict(self) -> dict:
        return asdict(self)


class SymbolDiscovery:
    def get_precandidates(self, active: list[str], min_volume: float) -> list[str]:
        """Return symbols from FUTURES_SYMBOLS that are listed, not active, and liquid."""
        try:
            info_resp = requests.get(f"{_FAPI_BASE}/exchangeInfo", timeout=10)
            ticker_resp = requests.get(f"{_FAPI_BASE}/ticker/24hr", timeout=10)
        except Exception as exc:
            raise RuntimeError(f"Exchange info fetch failed: {exc}") from exc

        listed = {
            s["symbol"]
            for s in info_resp.json().get("symbols", [])
            if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT"
        }
        volume = {
            t["symbol"]: float(t.get("quoteVolume", 0))
            for t in ticker_resp.json()
        }

        active_set = set(active)
        return sorted(
            sym for sym in FUTURES_SYMBOLS
            if sym in listed
            and sym not in active_set
            and volume.get(sym, 0.0) >= min_volume
        )

    def get_fast_presets(
        self,
        active: list[str],
        n: int,
        all_preset_names: list[str],
    ) -> list[str]:
        """Return top-N presets ranked by avg total_profit_pct across active symbols."""
        scores: dict[str, list[float]] = {}
        for sym in active:
            path = _DASHBOARD_PUBLIC / f"backtest_results_{sym}.json"
            try:
                data = json.loads(path.read_text())
                for name, pdata in data.get("presets", {}).items():
                    scores.setdefault(name, []).append(
                        float(pdata.get("total_profit_pct", 0.0))
                    )
            except Exception:
                continue

        if not scores:
            return all_preset_names[:n]

        avg = {name: sum(vals) / len(vals) for name, vals in scores.items()}
        ranked = sorted(avg, key=avg.__getitem__, reverse=True)
        return ranked[:n]

    def compute_baseline(self, active: list[str]) -> float:
        """Avg efficiency of the best preset across all active symbols with results."""
        efficiencies: list[float] = []
        for sym in active:
            path = _DASHBOARD_PUBLIC / f"backtest_results_{sym}.json"
            try:
                data = json.loads(path.read_text())
                presets = data.get("presets", {})
                if not presets:
                    continue
                best_eff = max(
                    p.get("total_profit_pct", 0.0) / max(1, p.get("total_trades", 1))
                    for p in presets.values()
                )
                efficiencies.append(best_eff)
            except Exception:
                continue

        return sum(efficiencies) / len(efficiencies) if efficiencies else 0.0

    def score_candidate(
        self,
        symbol: str,
        preset_subset: dict[str, dict],
        klines_count: int,
        baseline: float,
        baseline_ratio: float,
        min_floor: float,
        position_size: float,
        leverage: float,
    ) -> CandidateResult | None:
        """Backtest symbol against preset_subset, score it, return None if it fails filters."""
        settings = load_settings(symbol)
        suffix = "test" if settings.trading_mode == "testnet" else "live"
        cache_path = Path("data") / f"{symbol}_{settings.timeframe}_{suffix}.json"

        try:
            feed = DataFeed(settings)
            feed.refresh_klines(symbol, settings.timeframe, fetch_count=klines_count)
        except Exception as exc:
            logger.warning(f"[{symbol}] Kline fetch failed: {exc} — trying cache")

        if not cache_path.exists():
            logger.warning(f"[{symbol}] No kline cache found — skipping")
            return None

        klines = json.loads(cache_path.read_text())
        if klines_count and len(klines) > klines_count:
            klines = klines[-klines_count:]

        if len(klines) < 50:
            logger.warning(f"[{symbol}] Too few klines ({len(klines)}) — skipping")
            return None

        if not preset_subset:
            return None

        risk_cfg = load_risk_config(_RISK_CONFIG_PATH)
        backtester = Backtester(
            base_settings=settings,
            initial_balance=risk_cfg.get("backtest_initial_balance_usdt", 0.0),
        )
        results = backtester.run(klines, preset_subset)

        total_profit = sum(r.total_profit_pct() for r in results.values())
        total_orders = sum(r.total() for r in results.values())

        if total_orders == 0:
            return None

        efficiency_score = total_profit / total_orders

        if baseline > 0 and efficiency_score < baseline_ratio * baseline:
            logger.info(
                f"[{symbol}] efficiency={efficiency_score:.4f} below "
                f"threshold={baseline_ratio * baseline:.4f} — skipping"
            )
            return None
        if efficiency_score < min_floor:
            return None

        all_pcts = [
            t.profit_pct() or 0.0
            for r in results.values()
            for t in r.trades
        ]
        pos_sum = sum(p for p in all_pcts if p > 0)
        neg_sum = sum(abs(p) for p in all_pcts if p < 0)
        profit_factor = pos_sum / max(0.001, neg_sum)

        total_wins = sum(
            r.wins() + r.partials() + r.trails() for r in results.values()
        )
        win_rate = total_wins / total_orders

        max_drawdown = max(r.max_consecutive_losses() for r in results.values())

        if len(all_pcts) >= 2:
            try:
                sharpe = statistics.mean(all_pcts) / statistics.stdev(all_pcts)
            except statistics.StatisticsError:
                sharpe = 0.0
        else:
            sharpe = 0.0

        best_preset_id = max(results, key=lambda n: results[n].total_profit_pct())
        best_pct = results[best_preset_id].total_profit_pct()

        return CandidateResult(
            symbol=symbol,
            efficiency_score=round(efficiency_score, 4),
            total_net_profit=round(total_profit, 4),
            total_order_count=total_orders,
            profit_factor=round(profit_factor, 4),
            best_preset_id=best_preset_id,
            best_preset_profit_pct=round(best_pct, 4),
            win_rate=round(win_rate, 4),
            max_drawdown=max_drawdown,
            sharpe_ratio=round(sharpe, 4),
            baseline_efficiency=round(baseline, 4),
            vs_baseline_pct=round((efficiency_score / max(0.001, baseline) - 1) * 100, 2),
            potential_gain_usdt=round((best_pct / 100) * position_size * leverage, 2),
        )
