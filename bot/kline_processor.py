from typing import List


class KlineProcessor:
    """
    Detects swing high/low points in a kline series.

    A candle at index i is a swing high if its high is strictly greater than
    the highs of the `neighbours` candles on each side.
    Same logic inverted (using lows) for swing lows.
    """

    def __init__(self, neighbours: int = 2):
        self.neighbours = neighbours

    def detect_points(self, klines: list) -> List[dict]:
        """
        Full sweep of a kline list. Returns point dicts for every confirmed swing.
        Kline format: [open_time_ms, open, high, low, close, volume, ...]
        """
        n = self.neighbours
        result = []
        for i in range(n, len(klines) - n):
            result.extend(self._check_index(klines, i))
        return result

    def check_last_confirmed(self, klines: list) -> List[dict]:
        """
        After a new candle is appended, checks whether the candle `neighbours`
        positions from the end is now a confirmed swing point.
        Requires at least 2*neighbours+1 candles in the list.
        """
        n = self.neighbours
        if len(klines) < 2 * n + 1:
            return []
        return self._check_index(klines, -(n + 1))

    def _check_index(self, klines: list, i: int) -> List[dict]:
        n = self.neighbours
        high = float(klines[i][2])
        low = float(klines[i][3])
        time_s = int(klines[i][6]) // 1000

        is_high = (
            all(high > float(klines[i - j][2]) for j in range(1, n + 1))
            and all(high > float(klines[i + j][2]) for j in range(1, n + 1))
        )
        is_low = (
            all(low < float(klines[i - j][3]) for j in range(1, n + 1))
            and all(low < float(klines[i + j][3]) for j in range(1, n + 1))
        )

        if is_high or is_low:
            return [{'is_high': is_high, 'is_low': is_low, 'value': {'high': high, 'low': low}, 'time': time_s}]
        return []
