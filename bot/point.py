from typing import Optional

from bot.utils import Utils


class Point:
    """
    Represents a single price swing point detected in a kline series.

    A point carries both the high and low prices of the candle at that moment,
    plus flags indicating whether it is a swing high, a swing low, or both
    (a candle that is simultaneously higher than its neighbours on the high axis
    and lower on the low axis).
    """

    def __init__(self, point_object: dict):
        """
        Constructs a Point from a raw swing-point dict.

        Expected keys:
          - is_high  (bool)  — True if this candle is a local high
          - is_low   (bool)  — True if this candle is a local low
          - value    (dict)  — {'high': float, 'low': float, 'close': float}
          - time     (int)   — Unix timestamp (seconds)

        @param point_object  Raw dict produced by the kline-scanning loop.
        """
        self._is_high_point = bool(point_object['is_high'])
        self._is_low_point = bool(point_object['is_low'])
        self._value_high = float(point_object['value']['high'])
        self._value_low = float(point_object['value']['low'])
        self._value_close = float(point_object['value'].get('close', point_object['value']['high']))
        self._time = int(point_object['time'])

    # ------------------------------------------------------------------ #
    # Type checks                                                          #
    # ------------------------------------------------------------------ #

    def isHigh(self) -> bool:
        """Returns True if this point is a swing high."""
        return self._is_high_point

    def isLow(self) -> bool:
        """Returns True if this point is a swing low."""
        return self._is_low_point

    # ------------------------------------------------------------------ #
    # Value accessors                                                      #
    # ------------------------------------------------------------------ #

    def getHighValue(self) -> float:
        """Returns the candle's high price at this point."""
        return self._value_high

    def getLowValue(self) -> float:
        """Returns the candle's low price at this point."""
        return self._value_low

    def getCloseValue(self) -> float:
        """Returns the candle's close price at this point."""
        return self._value_close

    def getMainValue(self) -> float:
        """
        Returns the representative price for this point.

        If high == low (e.g. a doji), returns that value directly.
        Otherwise returns the midpoint between high and low.

        @return  Midpoint or exact price.
        """
        if self._value_high == self._value_low:
            return self._value_low
        return (self._value_high + self._value_low) / 2

    def getTime(self) -> int:
        """Returns the Unix timestamp (seconds) of this point."""
        return self._time

    # ------------------------------------------------------------------ #
    # Utility                                                              #
    # ------------------------------------------------------------------ #

    def clone(self) -> 'Point':
        """
        Creates an independent copy of this point.

        @return  A new Point with identical field values.
        """
        return Point({
            'is_high': self._is_high_point,
            'is_low': self._is_low_point,
            'value': {
                'high': self._value_high,
                'low': self._value_low,
                'close': self._value_close,
            },
            'time': self._time,
        })

    def __str__(self) -> str:
        """
        Returns a compact one-line summary for logging.

        Format: 'H: 94500.00    -- 2024-01-15 10:30:00'
        Prefix is 'H' for swing high, 'L' for swing low.
        """
        value = self._value_high if self._is_high_point else self._value_low
        label = 'H' if self._is_high_point else 'L'
        return f'{label}: {value:.2f}    -- {Utils.time_to_str(self._time)}'
