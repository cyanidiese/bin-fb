from typing import List, Optional, Tuple

from binance.client import Client

from bot.point import Point
from bot.recommendation import Recommendation, RecommendationTypes
from bot.utils import Utils


class Trend:
    """
    In-memory multi-level trend tracker.

    Each Trend instance represents one level of the hierarchy.
    A Trend at level N can lazily create a Trend at level N+1 (bigger/higher timeframe).
    Tracks higher highs / lower lows and a Break of Structure — the price level that,
    if crossed, signals a trend reversal.
    """

    def __init__(self, level: int, smaller: Optional['Trend'] = None):
        self._level = level
        self._bigger_trend: Optional['Trend'] = None
        self._smaller_trend = smaller
        self._counter = 0
        self._highs: List[Point] = []
        self._lows: List[Point] = []
        self._ascending: Optional[bool] = None
        self._break_of_structure: Optional[float] = None
        self._break_of_structure_time: Optional[int] = None
        self._trend_change_time: int = 0
        self._current_point: Optional[Point] = None
        self._correction_end_info: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # Bigger / smaller trend access                                        #
    # ------------------------------------------------------------------ #

    def hasBiggerTrend(self) -> bool:
        return self._bigger_trend is not None

    def getBiggerTrend(self) -> 'Trend':
        if self._bigger_trend is None:
            self._bigger_trend = Trend(self._level + 1, self)
        return self._bigger_trend

    def hasSmallerTrend(self) -> bool:
        return self._smaller_trend is not None

    def getSmallerTrend(self) -> Optional['Trend']:
        return self._smaller_trend

    # ------------------------------------------------------------------ #
    # Break of Structure                                                   #
    # ------------------------------------------------------------------ #

    def hasBreakOfStructure(self) -> bool:
        return self._break_of_structure is not None

    def getBreakOfStructure(self) -> Optional[float]:
        return self._break_of_structure

    def getBreakOfStructureTime(self) -> Optional[int]:
        return self._break_of_structure_time

    def setBreakOfStructure(self, value: float, time: Optional[int] = None) -> None:
        self._break_of_structure = value
        self._break_of_structure_time = time

    def isHigherThanBreakOfStructure(self, value: float) -> bool:
        return self._break_of_structure < value

    def isLowerThanBreakOfStructure(self, value: float) -> bool:
        return self._break_of_structure > value

    def get_correction_info(self) -> Optional[dict]:
        """
        Returns metrics for the correction in progress, or None if this trend
        is not currently moving counter to its parent.
        """
        if not self.hasBiggerTrend() or not self.hasDefinedTrend():
            return None
        bigger = self.getBiggerTrend()
        if not bigger.hasDefinedTrend():
            return None
        if self.isAscending() == bigger.isAscending():
            return None

        # Count only swings that formed after the parent trend's last impulse peak/trough
        # (i.e. since the correction started, not all accumulated L1 history)
        if bigger.isAscending():
            start_time = bigger.getTimeOfLastHigh() or 0  # correction started after L2 HH
        else:
            start_time = bigger.getTimeOfLastLow() or 0   # correction started after L2 LL
        swing_count = (
            sum(1 for p in self._highs if p.getTime() >= start_time)
            + sum(1 for p in self._lows  if p.getTime() >= start_time)
        )
        return {
            'swing_count': swing_count,
            'depth_pct': self._compute_correction_depth(bigger),
            'bos_level': self.getBreakOfStructure(),
            'bos_direction': 'above' if self.isDescending() else 'below',
        }

    def _compute_correction_depth(self, bigger: 'Trend') -> float:
        """% of the last parent-trend impulse retraced by this correction."""
        l2_high = bigger.getLastHigh()
        l2_low = bigger.getLastLow()
        if l2_high is None or l2_low is None:
            return 0.0
        impulse_size = l2_high.getHighValue() - l2_low.getLowValue()
        if impulse_size <= 0:
            return 0.0
        if bigger.isAscending():
            extreme = self.getLastLow()
            if extreme is None:
                return 0.0
            retraced = l2_high.getHighValue() - extreme.getLowValue()
        else:
            extreme = self.getLastHigh()
            if extreme is None:
                return 0.0
            retraced = extreme.getHighValue() - l2_low.getLowValue()
        return max(0.0, retraced / impulse_size * 100)

    def get_correction_end_info(self) -> Optional[dict]:
        """Returns metadata from the correction that just ended this candle, or None."""
        return self._correction_end_info

    # ------------------------------------------------------------------ #
    # Trend direction                                                      #
    # ------------------------------------------------------------------ #

    def updateTrendChangeTime(self, trend_change_time: int) -> None:
        self._trend_change_time = trend_change_time

    def getTrendChangeTime(self) -> int:
        return self._trend_change_time

    def hasDefinedTrend(self) -> bool:
        return self._ascending is not None

    def isAscending(self) -> bool:
        return self.hasDefinedTrend() and self._ascending

    def isDescending(self) -> bool:
        return self.hasDefinedTrend() and not self._ascending

    def setAscending(self, ascending: bool) -> None:
        self._ascending = ascending

    # ------------------------------------------------------------------ #
    # Current point                                                        #
    # ------------------------------------------------------------------ #

    def setCurrentPoint(self, point: Point) -> None:
        self._current_point = point.clone()

    def getCurrentPoint(self) -> Optional[Point]:
        return self._current_point

    # ------------------------------------------------------------------ #
    # Highs                                                                #
    # ------------------------------------------------------------------ #

    def getLevel(self) -> int:
        return self._level

    def getHighPoints(self) -> List[Point]:
        return list(self._highs)

    def getLowPoints(self) -> List[Point]:
        return list(self._lows)

    def sortPoints(self) -> None:
        self._highs.sort(key=lambda x: x.getTime())
        self._lows.sort(key=lambda x: x.getTime())

    def removePointsUpTo(self, timestamp: int) -> None:
        self._highs = [p for p in self._highs if p.getTime() > timestamp]
        self._lows = [p for p in self._lows if p.getTime() > timestamp]

    def addHighPoint(self, point: Point) -> None:
        self._highs.append(point.clone())
        self.sortPoints()

    def replaceLastHigh(self, point: Point) -> None:
        self._highs[-1] = point.clone()
        self.sortPoints()

    def hasHighBetween(self, timestamp1: int, timestamp2: int) -> bool:
        return any(timestamp1 < h.getTime() < timestamp2 for h in self._highs)

    def findHighestSince(self, timestamp: Optional[int]) -> Optional[Point]:
        highest = None
        for high in self._highs:
            if timestamp is None or high.getTime() > timestamp:
                if highest is None or high.getHighValue() > highest.getHighValue():
                    highest = high
        return highest

    def findHighestInBiggerTrendsSince(self, timestamp: Optional[int]) -> Optional[Point]:
        highest = self.findHighestSince(timestamp)
        if self.hasBiggerTrend():
            next_highest = self.getBiggerTrend().findHighestInBiggerTrendsSince(timestamp)
            if next_highest is not None and (highest is None or next_highest.getHighValue() > highest.getHighValue()):
                highest = next_highest
        return highest

    def isLastPointHigh(self) -> Optional[bool]:
        all_points = self._highs + self._lows
        all_points.sort(key=lambda x: x.getTime())
        return all_points[-1].isHigh() if all_points else None

    def hasHighs(self) -> bool:
        return len(self._highs) > 0

    def getLastHigh(self) -> Optional[Point]:
        return self._highs[-1] if self.hasHighs() else None

    def getTimeOfLastHigh(self) -> Optional[int]:
        last = self.getLastHigh()
        return last.getTime() if last is not None else None

    # ------------------------------------------------------------------ #
    # Lows                                                                 #
    # ------------------------------------------------------------------ #

    def addLowPoint(self, point: Point) -> None:
        self._lows.append(point.clone())
        self.sortPoints()

    def replaceLastLow(self, point: Point) -> None:
        self._lows[-1] = point.clone()
        self.sortPoints()

    def hasLowBetween(self, timestamp1: int, timestamp2: int) -> bool:
        return any(timestamp1 < low.getTime() < timestamp2 for low in self._lows)

    def findLowestSince(self, timestamp: Optional[int]) -> Optional[Point]:
        lowest = None
        for low in self._lows:
            if timestamp is None or low.getTime() > timestamp:
                if lowest is None or low.getLowValue() < lowest.getLowValue():
                    lowest = low
        return lowest

    def findLowestInBiggerTrendsSince(self, timestamp: Optional[int]) -> Optional[Point]:
        lowest = self.findLowestSince(timestamp)
        if self.hasBiggerTrend():
            next_lowest = self.getBiggerTrend().findLowestInBiggerTrendsSince(timestamp)
            if next_lowest is not None and (lowest is None or next_lowest.getLowValue() < lowest.getLowValue()):
                lowest = next_lowest
        return lowest

    def hasLows(self) -> bool:
        return len(self._lows) > 0

    def getLastLow(self) -> Optional[Point]:
        return self._lows[-1] if self.hasLows() else None

    def getTimeOfLastLow(self) -> Optional[int]:
        last = self.getLastLow()
        return last.getTime() if last is not None else None

    # ------------------------------------------------------------------ #
    # Point ingestion                                                      #
    # ------------------------------------------------------------------ #

    def checkPointObject(self, point_object: dict) -> None:
        point = Point(point_object)
        self.setCurrentPoint(point)

        if point.isHigh():
            self.setHighPoint(point)
        elif point.isLow():
            self.setLowPoint(point)

    def checkIfHigherThanDescBreakOfStructure(self, point: Point) -> None:
        if self.isDescending() and self.hasBreakOfStructure() and point.getCloseValue() > self.getBreakOfStructure():
            self._correction_end_info = self.get_correction_info()
            self.setAscending(True)
            last_low = self.getLastLow()
            self.setBreakOfStructure(last_low.getLowValue(), last_low.getTime())
            self.updateTrendChangeTime(point.getTime())
            time_of_last_high = self.getBiggerTrend().getTimeOfLastHigh()
            lowest_since = self.findLowestSince(time_of_last_high)
            if lowest_since is not None:
                self.getBiggerTrend().setLowPoint(lowest_since)
                self.removePointsUpTo(lowest_since.getTime())

    def checkIfLowerThanAscBreakOfStructure(self, point: Point) -> None:
        if self.isAscending() and self.hasBreakOfStructure() and point.getCloseValue() < self.getBreakOfStructure():
            self._correction_end_info = self.get_correction_info()
            self.setAscending(False)
            last_high = self.getLastHigh()
            self.setBreakOfStructure(last_high.getHighValue(), last_high.getTime())
            self.updateTrendChangeTime(point.getTime())
            time_of_last_low = self.getBiggerTrend().getTimeOfLastLow()
            highest_since = self.findHighestSince(time_of_last_low)
            if highest_since is not None:
                self.getBiggerTrend().setHighPoint(highest_since)
                self.removePointsUpTo(highest_since.getTime())

    def setHighPoint(self, point: Point) -> None:
        self._correction_end_info = None
        self._counter += 1
        last_high = self.getLastHigh()

        if last_high is None:
            self.addHighPoint(point)
            return

        if self.hasLowBetween(last_high.getTime(), point.getTime()):
            if not self.hasDefinedTrend():
                if point.getHighValue() > last_high.getHighValue():
                    self.setAscending(True)
                    last_low = self.getLastLow()
                    self.setBreakOfStructure(last_low.getLowValue(), last_low.getTime())
                else:
                    self.setAscending(False)
                    self.setBreakOfStructure(point.getHighValue(), point.getTime())
                self.updateTrendChangeTime(point.getTime())

            is_higher = point.getHighValue() > last_high.getHighValue()
            self.addHighPoint(point)

            if self.isAscending():
                if is_higher:
                    last_low = self.getLastLow()
                    if last_low is not None:
                        self.setBreakOfStructure(last_low.getLowValue(), last_low.getTime())
                        self.updateTrendChangeTime(point.getTime())
            else:
                self.checkIfHigherThanDescBreakOfStructure(point)
        else:
            if point.getHighValue() > last_high.getHighValue():
                self.replaceLastHigh(point)
            self.checkIfHigherThanDescBreakOfStructure(point)

    def setLowPoint(self, point: Point) -> None:
        self._correction_end_info = None
        self._counter += 1
        last_low = self.getLastLow()

        if last_low is None:
            self.addLowPoint(point)
            return

        if self.hasHighBetween(last_low.getTime(), point.getTime()):
            if not self.hasDefinedTrend():
                if point.getLowValue() < last_low.getLowValue():
                    self.setAscending(False)
                    last_high = self.getLastHigh()
                    self.setBreakOfStructure(last_high.getHighValue(), last_high.getTime())
                else:
                    self.setAscending(True)
                    self.setBreakOfStructure(point.getLowValue(), point.getTime())
                self.updateTrendChangeTime(point.getTime())

            is_lower = point.getLowValue() < last_low.getLowValue()
            self.addLowPoint(point)

            if self.isDescending():
                if is_lower:
                    last_high = self.getLastHigh()
                    if last_high is not None:
                        self.setBreakOfStructure(last_high.getHighValue(), last_high.getTime())
                        self.updateTrendChangeTime(point.getTime())
            else:
                self.checkIfLowerThanAscBreakOfStructure(point)
        else:
            if point.getLowValue() < last_low.getLowValue():
                self.replaceLastLow(point)
            self.checkIfLowerThanAscBreakOfStructure(point)

    def shouldCrossBreakOfStructure(self, point: Point) -> bool:
        if self.hasDefinedTrend() and self.getBreakOfStructure() is not None:
            if self.isAscending():
                return point.getLowValue() < self.getBreakOfStructure()
            else:
                return point.getHighValue() > self.getBreakOfStructure()
        return False

    # ------------------------------------------------------------------ #
    # Analysis helpers                                                     #
    # ------------------------------------------------------------------ #

    def getPointsDifferences(self, points: List[Point], is_high: bool) -> List[float]:
        diffs = []
        for i in range(len(points) - 1):
            v1 = points[i].getHighValue() if is_high else points[i].getLowValue()
            v2 = points[i + 1].getHighValue() if is_high else points[i + 1].getLowValue()
            diffs.append(abs(v2 - v1))
        return diffs

    def getAvgDifference(self, diffs: List[float]) -> float:
        return sum(diffs) / len(diffs) if diffs else 0.0

    def getLastPoints(self, points: List[Point], count: int = 4) -> List[Point]:
        return points[-count:]

    def whichIsCloser(
        self, current: float, high: float, low: float, threshold: float = 10.0
    ) -> Tuple[int, float]:
        """
        Returns (direction, closeness_pct).
        direction: 1 if closer to / above high, -1 if closer to / below low, 0 if in between.
        closeness_pct: how close as a percentage of the high-low range (100 means outside).
        threshold controls the closeness zone as % of the range.
        """
        if current > high:
            return 1, 100
        if current < low:
            return -1, 100

        whole = high - low
        if whole == 0:
            return 0, 0.0
        pct_to_high = (high - current) / whole * 100
        pct_to_low = (current - low) / whole * 100

        if pct_to_high < threshold:
            return 1, pct_to_high
        if pct_to_low < threshold:
            return -1, pct_to_low
        return 0, 0.0

    def getSupposedNextPoints(self) -> Tuple[Optional[float], Optional[float]]:
        if len(self._highs) < 3 or len(self._lows) < 3:
            return None, None

        last_highs = self.getLastPoints(self._highs, 4)
        last_lows = self.getLastPoints(self._lows, 4)

        avg_high_diff = self.getAvgDifference(self.getPointsDifferences(last_highs, True))
        avg_low_diff = self.getAvgDifference(self.getPointsDifferences(last_lows, False))

        multiplier = 1 if self.isAscending() else -1

        last_height = self.getLastHigh()
        last_low = self.getLastLow()

        lowest_since = self.findLowestInBiggerTrendsSince(last_height.getTime())
        highest_since = self.findHighestInBiggerTrendsSince(last_low.getTime())

        calculated_low = last_low.getLowValue() + (avg_low_diff * multiplier)
        calculated_high = last_height.getHighValue() + (avg_high_diff * multiplier)

        supposed_next_low = (
            lowest_since.getLowValue()
            if lowest_since is not None and lowest_since.getLowValue() < calculated_low
            else calculated_low
        )
        supposed_next_high = (
            highest_since.getHighValue()
            if highest_since is not None and highest_since.getHighValue() > calculated_high
            else calculated_high
        )

        return supposed_next_high, supposed_next_low

    # ------------------------------------------------------------------ #
    # Recommendations                                                      #
    # ------------------------------------------------------------------ #

    def getRecommendation(
        self,
        point: Optional[Point] = None,
        entry_price: Optional[float] = None,
        proximity_zone_pct: float = 10.0,
        lower_high_sell: bool = False,
        higher_low_buy: bool = False,
    ) -> Optional[Recommendation]:
        point = self.getCurrentPoint() if point is None else point
        if point is None:
            return None

        if entry_price is None:
            entry_price = point.getMainValue()

        if self.shouldCrossBreakOfStructure(point):
            return None

        supposed_next_high, supposed_next_low = self.getSupposedNextPoints()
        if supposed_next_high is None or supposed_next_low is None:
            return None

        smaller_trend = self.getSmallerTrend()
        if smaller_trend is None:
            return None

        is_last_high = self.isLastPointHigh()
        smaller_break_of_structure = smaller_trend.getBreakOfStructure() if smaller_trend.hasBreakOfStructure() else None

        rec: Optional[Recommendation] = None
        how_close = proximity_zone_pct  # default: no closeness bonus

        if is_last_high is not None:
            last_low = self.getLastLow()

            # When higher_low_buy is enabled: in an ascending trend, fire BUY
            # when price is within proximity_zone_pct of the projected higher low
            # (approaching from above, before the swing is confirmed).
            if (
                higher_low_buy
                and self.isAscending()
                and supposed_next_low is not None
                and supposed_next_low > last_low.getLowValue()
            ):
                last_high = self.getLastHigh()
                range_size = (
                    last_high.getHighValue() - last_low.getLowValue()
                    if last_high is not None else 0.0
                )
                if range_size > 0 and entry_price >= supposed_next_low:
                    dist = entry_price - supposed_next_low
                    prox = dist / range_size * 100
                    if prox <= proximity_zone_pct:
                        how_close = prox
                        rec = Recommendation(
                            point, supposed_next_high, last_low.getLowValue(),
                            Client.SIDE_BUY, RecommendationTypes.ASCENDING_NEAR_HIGHER_LOW,
                        ).setLevel(self._level)

            if rec is None:
                if point.getHighValue() > last_low.getLowValue():
                    rec = Recommendation(
                        point, supposed_next_low, smaller_break_of_structure,
                        Client.SIDE_SELL, RecommendationTypes.LOWERING_ABOVE_LAST_LOW,
                    ).setLevel(self._level)
                else:
                    what_is_closer, prox = self.whichIsCloser(
                        point.getMainValue(), last_low.getLowValue(), supposed_next_low,
                        proximity_zone_pct,
                    )
                    is_close = prox <= 20

                    if what_is_closer == 1 and is_close and self.isDescending():
                        how_close = prox
                        rec = Recommendation(
                            point, supposed_next_low, last_low.getLowValue(),
                            Client.SIDE_SELL, RecommendationTypes.LOWERING_NEAR_LAST_LOW,
                        ).setLevel(self._level)
                    elif what_is_closer == 1:
                        if is_close:
                            how_close = prox
                            rec = Recommendation(
                                point, supposed_next_high, supposed_next_low,
                                Client.SIDE_BUY, RecommendationTypes.LOWERING_NEAR_SUPPOSED_LOW, True,
                            ).setLevel(self._level)
                        elif prox == 100:
                            rec = Recommendation(
                                point, supposed_next_high, supposed_next_low,
                                Client.SIDE_BUY, RecommendationTypes.LOWERING_BELOW_SUPPOSED_LOW, True,
                            ).setLevel(self._level)
        else:
            last_high = self.getLastHigh()

            # When lower_high_sell is enabled: in a descending trend, fire SELL
            # when price is within proximity_zone_pct of the projected lower high
            # (approaching from below, before the swing is confirmed).
            if (
                lower_high_sell
                and self.isDescending()
                and supposed_next_high is not None
                and supposed_next_high < last_high.getHighValue()
            ):
                last_low = self.getLastLow()
                range_size = (
                    last_high.getHighValue() - last_low.getLowValue()
                    if last_low is not None else 0.0
                )
                if range_size > 0 and entry_price <= supposed_next_high:
                    dist = supposed_next_high - entry_price
                    prox = dist / range_size * 100
                    if prox <= proximity_zone_pct:
                        how_close = prox
                        rec = Recommendation(
                            point, supposed_next_low, last_high.getHighValue(),
                            Client.SIDE_SELL, RecommendationTypes.DESCENDING_NEAR_LOWER_HIGH,
                        ).setLevel(self._level)

            if rec is None:
                if point.getLowValue() < last_high.getHighValue():
                    rec = Recommendation(
                        point, supposed_next_high, smaller_break_of_structure,
                        Client.SIDE_BUY, RecommendationTypes.RISING_BELOW_LAST_HIGH,
                    ).setLevel(self._level)
                else:
                    what_is_closer, prox = self.whichIsCloser(
                        point.getMainValue(), last_high.getHighValue(), supposed_next_high,
                        proximity_zone_pct,
                    )
                    is_close = prox <= 20

                    if what_is_closer == 1 and is_close and self.isAscending():
                        how_close = prox
                        rec = Recommendation(
                            point, supposed_next_high, last_high.getHighValue(),
                            Client.SIDE_BUY, RecommendationTypes.RISING_NEAR_LAST_HIGH,
                        ).setLevel(self._level)
                    elif what_is_closer == 1:
                        if is_close:
                            how_close = prox
                            rec = Recommendation(
                                point, supposed_next_low, supposed_next_high,
                                Client.SIDE_SELL, RecommendationTypes.RISING_NEAR_SUPPOSED_HIGH,
                            ).setLevel(self._level)
                        elif prox == 100:
                            rec = Recommendation(
                                point, supposed_next_low, supposed_next_high,
                                Client.SIDE_SELL, RecommendationTypes.RISING_ABOVE_SUPPOSED_HIGH, True,
                            ).setLevel(self._level)

        if rec is not None:
            rec.setEntryPrice(entry_price).setHowClose(how_close)

        return rec

    def getRecommendations(
        self,
        entry_price: Optional[float] = None,
        proximity_zone_pct: float = 10.0,
        lower_high_sell: bool = False,
        higher_low_buy: bool = False,
    ) -> List[Recommendation]:
        result = []
        rec = self.getRecommendation(
            entry_price=entry_price,
            proximity_zone_pct=proximity_zone_pct,
            lower_high_sell=lower_high_sell,
            higher_low_buy=higher_low_buy,
        )
        if rec is not None:
            result.append(rec)
        if self.hasBiggerTrend():
            bigger = self.getBiggerTrend()
            if self.getCurrentPoint() is not None:
                bigger.setCurrentPoint(self.getCurrentPoint())
            result.extend(bigger.getRecommendations(
                entry_price=entry_price,
                proximity_zone_pct=proximity_zone_pct,
                lower_high_sell=lower_high_sell,
                higher_low_buy=higher_low_buy,
            ))
        return result

    # ------------------------------------------------------------------ #
    # String representation                                                #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:
        direction = 'NONE' if not self.hasDefinedTrend() else ('ASC' if self.isAscending() else 'DESC')
        bos_val = self._break_of_structure if self.hasBreakOfStructure() else 'NONE'
        bos_time = Utils.time_to_str(self.getBreakOfStructureTime())
        change_time = Utils.time_to_str(self.getTrendChangeTime())

        lines = [
            f'\n===================================== {self._counter}',
            f'Trend {self._level}: {direction}',
            f'Break of Structure: {bos_val}, time: {bos_time}',
            f'trend change time: {change_time}',
            '-----------',
        ]

        all_points = sorted(self._highs + self._lows, key=lambda p: p.getTime())
        for point in all_points[-10:]:
            lines.append(str(point))

        result = '\n'.join(lines)

        if self.hasBiggerTrend():
            self.getBiggerTrend().setCurrentPoint(self.getCurrentPoint())
            result += str(self.getBiggerTrend())

        return result
