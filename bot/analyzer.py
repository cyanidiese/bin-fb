from typing import List, Optional

from bot.kline_processor import KlineProcessor
from bot.recommendation import Recommendation
from bot.recommendation_engine import RecommendationEngine
from bot.trend import Trend

_MAX_KLINES = 4000  # ~41 days of 15-minute candles; more than enough for ATR and swing detection


class Analyzer:
    def __init__(self, swing_neighbours: int = 2, engine: Optional[RecommendationEngine] = None):
        self._processor = KlineProcessor(swing_neighbours)
        self._engine = engine
        self._trend: Optional[Trend] = None
        self._klines: list = []
        self._current_price: float = 0.0
        # Permanent history of all detected swing points with level assignments.
        # Never cleared on BoS — the trend's removePointsUpTo() wipes the live
        # trend state but we want the dashboard to show the full historical picture.
        self._all_points: list = []
        self._best_recommendation: Optional[Recommendation] = None
        self._existing_bigger_points: set = set()

    def _capture_bigger_trends(self) -> None:
        """Snapshot any new L2+ points into the permanent history.
        Uses a persistent dedup set to avoid O(N²) rebuilding on every call."""
        current = (
            self._trend.getBiggerTrend()
            if (self._trend and self._trend.hasBiggerTrend())
            else None
        )
        while current is not None:
            level = current.getLevel()
            for pt in current.getHighPoints():
                key = (pt.getTime(), level, 'high')
                if key not in self._existing_bigger_points:
                    self._existing_bigger_points.add(key)
                    self._all_points.append({
                        'time': pt.getTime(), 'level': level,
                        'type': 'high', 'price': pt.getHighValue(),
                    })
            for pt in current.getLowPoints():
                key = (pt.getTime(), level, 'low')
                if key not in self._existing_bigger_points:
                    self._existing_bigger_points.add(key)
                    self._all_points.append({
                        'time': pt.getTime(), 'level': level,
                        'type': 'low', 'price': pt.getLowValue(),
                    })
            current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    def build_from_klines(self, klines: list) -> None:
        self._klines = list(klines)
        self._all_points = []
        self._existing_bigger_points = set()
        self._best_recommendation = None
        self._trend = Trend(1)
        for point_dict in self._processor.detect_points(klines):
            # Capture L1 point before the trend processes it — processing may
            # call removePointsUpTo() which wipes older L1 entries.
            ts = point_dict['time']
            is_h = point_dict['is_high']
            price = point_dict['value']['high'] if is_h else point_dict['value']['low']
            self._all_points.append({
                'time': ts, 'level': 1,
                'type': 'high' if is_h else 'low', 'price': price,
            })
            self._trend.checkPointObject(point_dict)
            self._capture_bigger_trends()

    def add_candle(self, kline: list) -> List[Recommendation]:
        if self._trend is None:
            return []

        self._klines.append(kline)
        if len(self._klines) > _MAX_KLINES:
            self._klines = self._klines[-_MAX_KLINES:]

        new_points = self._processor.check_last_confirmed(self._klines)
        for point_dict in new_points:
            ts = point_dict['time']
            is_h = point_dict['is_high']
            price = point_dict['value']['high'] if is_h else point_dict['value']['low']
            self._all_points.append({
                'time': ts, 'level': 1,
                'type': 'high' if is_h else 'low', 'price': price,
            })
            self._trend.checkPointObject(point_dict)
            self._capture_bigger_trends()

        # A1: refresh every candle so the best recommendation reflects current price proximity,
        # not just the candle where the last swing point was detected.
        # BUG-05: sync price to the closed candle's close before scoring so REST-refreshed
        # candles don't produce recommendations scored against a slightly lagged tick price.
        close_px = float(kline[4]) if kline and kline[4] else self._current_price
        if close_px > 0:
            self._current_price = close_px
        self._refresh_recommendations()

        pct = self._engine._s.proximity_zone_pct if self._engine else 10.0
        return self._trend.getRecommendations(
            entry_price=self._current_price,
            proximity_zone_pct=pct,
        )

    def _refresh_recommendations(self) -> None:
        """Run the scoring engine and cache the best recommendation."""
        if self._engine is None or self._trend is None:
            self._best_recommendation = None
            return
        self._best_recommendation = self._engine.generate(self._trend, self._current_price)

    def update_price(self, price: float) -> None:
        self._current_price = price

    def get_current_price(self) -> float:
        return self._current_price

    def get_recommendations(self) -> List[Recommendation]:
        """All per-level candidates (unfiltered, for display)."""
        if self._trend is None:
            return []
        pct = self._engine._s.proximity_zone_pct if self._engine else 10.0
        return self._trend.getRecommendations(
            entry_price=self._current_price,
            proximity_zone_pct=pct,
        )

    def get_scored_recommendations(self) -> List[Recommendation]:
        """All candidates that survive scoring filters, with precision/RR set."""
        if self._engine is None or self._trend is None:
            return []
        return self._engine.collect_all(self._trend, self._current_price)

    def get_best_recommendation(self) -> Optional[Recommendation]:
        """The single winner selected by the engine, or None."""
        return self._best_recommendation

    def get_recommendation_for_preset(self, overrides: dict):
        """Run the recommendation engine with preset-overridden settings. Returns None if no signal."""
        if self._trend is None or self._engine is None:
            return None
        import dataclasses
        s = dataclasses.replace(self._engine._s, **overrides)
        return RecommendationEngine(s).generate(self._trend, self._current_price)

    def get_all_points(self) -> list:
        # Determine which historical points are currently "active" (still present
        # in the live trend). Points wiped by removePointsUpTo() after a BoS
        # remain in history but are flagged active=False.
        active_keys: set = set()
        current = self._trend
        while current is not None:
            level = current.getLevel()
            for pt in current.getHighPoints():
                active_keys.add((pt.getTime(), level, 'high'))
            for pt in current.getLowPoints():
                active_keys.add((pt.getTime(), level, 'low'))
            current = current.getBiggerTrend() if current.hasBiggerTrend() else None

        return [
            {**p, 'active': (p['time'], p['level'], p['type']) in active_keys}
            for p in self._all_points
        ]

    def get_klines(self) -> list:
        return list(self._klines)

    def get_trend(self) -> Optional[Trend]:
        return self._trend
