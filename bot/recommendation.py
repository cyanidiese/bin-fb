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

    def setLevel(self, level: int) -> 'Recommendation':
        self._level = level
        return self

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

    def __str__(self) -> str:
        stop_str = f'{self._stop:.2f}' if self._stop is not None else 'None'
        return (
            f'[L{self._level}] {self._side} | {self._type.value} | '
            f'target={self._target:.2f} stop={stop_str}'
            f'{" [reversal]" if self._is_reversal else ""}'
        )
