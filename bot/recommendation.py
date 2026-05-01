from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.point import Point


class RecommendationTypes(Enum):
    RISING_BELOW_LAST_HIGH = 'rising_below_last_high'
    RISING_NEAR_LAST_HIGH = 'rising_near_last_high'
    RISING_NEAR_SUPPOSED_HIGH = 'rising_near_supposed_high'
    RISING_ABOVE_SUPPOSED_HIGH = 'rising_above_supposed_high'
    LOWERING_ABOVE_LAST_LOW = 'lowering_above_last_low'
    LOWERING_NEAR_LAST_LOW = 'lowering_near_last_low'
    LOWERING_NEAR_SUPPOSED_LOW = 'lowering_near_supposed_low'
    LOWERING_BELOW_SUPPOSED_LOW = 'lowering_below_supposed_low'


class Recommendation:
    def __init__(
        self,
        point: 'Point',
        target: float,
        stop: Optional[float],
        side: str,
        rec_type: RecommendationTypes,
        is_reversal: bool = False,
    ):
        self._point = point
        self._target = target
        self._stop = stop
        self._side = side
        self._type = rec_type
        self._is_reversal = is_reversal
        self._level: Optional[int] = None

        # Scoring fields — populated by RecommendationEngine after construction.
        self._entry_price: float = 0.0
        # how_close: distance from the nearest structural boundary as % of swing range
        # (from whichIsCloser). Defaults to large value so types that skip the
        # proximity check contribute zero entry-quality score.
        self._how_close: float = 100.0
        self._precision: Optional[float] = None
        self._rr: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Identity setters (chainable)                                        #
    # ------------------------------------------------------------------ #

    def setLevel(self, level: int) -> 'Recommendation':
        self._level = level
        return self

    def setEntryPrice(self, price: float) -> 'Recommendation':
        self._entry_price = price
        return self

    def setHowClose(self, how_close: float) -> 'Recommendation':
        self._how_close = how_close
        return self

    def setPrecision(self, precision: float) -> 'Recommendation':
        self._precision = precision
        return self

    def setRR(self, rr: float) -> 'Recommendation':
        self._rr = rr
        return self

    # ------------------------------------------------------------------ #
    # Getters                                                              #
    # ------------------------------------------------------------------ #

    def getPoint(self) -> 'Point':
        return self._point

    def getTarget(self) -> float:
        return self._target

    def getStop(self) -> Optional[float]:
        return self._stop

    def getSide(self) -> str:
        return self._side

    def getType(self) -> RecommendationTypes:
        return self._type

    def isReversal(self) -> bool:
        return self._is_reversal

    def getLevel(self) -> Optional[int]:
        return self._level

    def getEntryPrice(self) -> float:
        return self._entry_price

    def getHowClose(self) -> float:
        return self._how_close

    def getPrecision(self) -> Optional[float]:
        return self._precision

    def getRR(self) -> Optional[float]:
        return self._rr

    def getProjectedProfitPct(self) -> Optional[float]:
        if self._entry_price == 0:
            return None
        return abs(self._target - self._entry_price) / self._entry_price * 100

    def getProjectedLossPct(self) -> Optional[float]:
        if self._stop is None or self._entry_price == 0:
            return None
        return abs(self._stop - self._entry_price) / self._entry_price * 100

    def __str__(self) -> str:
        stop_str = f'{self._stop:.2f}' if self._stop is not None else 'None'
        precision_str = f'{self._precision:.3f}' if self._precision is not None else 'None'
        rr_str = f'{self._rr:.2f}' if self._rr is not None else 'None'
        return (
            f'[L{self._level}] {self._side} | {self._type.value} | '
            f'entry={self._entry_price:.2f} target={self._target:.2f} stop={stop_str} '
            f'rr={rr_str} precision={precision_str}'
            f'{" [reversal]" if self._is_reversal else ""}'
        )
