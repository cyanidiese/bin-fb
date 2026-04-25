from typing import List, Optional

from bot.kline_processor import KlineProcessor
from bot.recommendation import Recommendation
from bot.trend import Trend


class Analyzer:
    def __init__(self, swing_neighbours: int = 2):
        self._processor = KlineProcessor(swing_neighbours)
        self._trend: Optional[Trend] = None
        self._klines: list = []
        self._current_price: float = 0.0
        # Permanent history of all detected swing points with level assignments.
        # Never cleared on BoS — the trend's removePointsUpTo() wipes the live
        # trend state but we want the dashboard to show the full historical picture.
        self._all_points: list = []

    def _capture_bigger_trends(self) -> None:
        """Snapshot any new L2+ points into the permanent history.
        Called after each checkPointObject so points are captured before a
        subsequent BoS could wipe them from the trend."""
        existing = {(p['time'], p['level'], p['type']) for p in self._all_points}
        current = (
            self._trend.getBiggerTrend()
            if (self._trend and self._trend.hasBiggerTrend())
            else None
        )
        while current is not None:
            level = current.getLevel()
            for pt in current.getHighPoints():
                key = (pt.getTime(), level, 'high')
                if key not in existing:
                    existing.add(key)
                    self._all_points.append({
                        'time': pt.getTime(), 'level': level,
                        'type': 'high', 'price': pt.getHighValue(),
                    })
            for pt in current.getLowPoints():
                key = (pt.getTime(), level, 'low')
                if key not in existing:
                    existing.add(key)
                    self._all_points.append({
                        'time': pt.getTime(), 'level': level,
                        'type': 'low', 'price': pt.getLowValue(),
                    })
            current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    def build_from_klines(self, klines: list) -> None:
        self._klines = list(klines)
        self._all_points = []
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

        return self._trend.getRecommendations() if new_points else []

    def update_price(self, price: float) -> None:
        self._current_price = price

    def get_current_price(self) -> float:
        return self._current_price

    def get_recommendations(self) -> List[Recommendation]:
        if self._trend is None:
            return []
        return self._trend.getRecommendations()

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
