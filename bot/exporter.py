import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.trend import Trend

logger = logging.getLogger(__name__)

_OUTPUT_PATH = Path('dashboard/public/results.json')
_MAX_KLINES = 1000


def export(
    symbol: str,
    timeframe: str,
    mode: str,
    current_price: float,
    trend: Optional[Trend],
    klines: list,
    recommendations: list,
    all_points_history: Optional[list] = None,
) -> None:
    if trend is None:
        return

    trend_levels = []
    current = trend
    while current is not None:
        last_high = current.getLastHigh()
        last_low = current.getLastLow()
        trend_levels.append({
            'level': current.getLevel(),
            'direction': (
                'ASC' if current.isAscending()
                else 'DESC' if current.isDescending()
                else 'NONE'
            ),
            'bos': current.getBreakOfStructure(),
            'bos_since': _ts(current.getBreakOfStructureTime()),
            'last_high': {'price': last_high.getHighValue(), 'time': _ts(last_high.getTime())} if last_high else None,
            'last_low':  {'price': last_low.getLowValue(),  'time': _ts(last_low.getTime())}  if last_low  else None,
        })
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    if all_points_history is not None:
        # Use the permanent history from the Analyzer — this includes all swing
        # points ever detected, even those wiped from the live trend by BoS events.
        all_points = sorted(
            [{'time': _ts(p['time']), 'level': p['level'], 'type': p['type'], 'price': p['price'], 'active': p['active']}
             for p in all_points_history],
            key=lambda p: p['time'], reverse=True,
        )
    else:
        # Fallback: traverse the live trend (points wiped by BoS won't appear).
        seen: set = set()
        all_points = []
        current = trend
        while current is not None:
            level = current.getLevel()
            for pt in current.getHighPoints():
                key = (pt.getTime(), level, True)
                if key not in seen:
                    seen.add(key)
                    all_points.append({'time': _ts(pt.getTime()), 'level': level, 'type': 'high', 'price': pt.getHighValue()})
            for pt in current.getLowPoints():
                key = (pt.getTime(), level, False)
                if key not in seen:
                    seen.add(key)
                    all_points.append({'time': _ts(pt.getTime()), 'level': level, 'type': 'low', 'price': pt.getLowValue()})
            current = current.getBiggerTrend() if current.hasBiggerTrend() else None
        all_points.sort(key=lambda p: p['time'], reverse=True)

    kline_data = [
        {'time': int(k[0]) // 1000, 'open': float(k[1]), 'high': float(k[2]), 'low': float(k[3]), 'close': float(k[4])}
        for k in klines[-_MAX_KLINES:]
    ]

    signals = [
        {
            'level': rec.getLevel(),
            'side': rec.getSide(),
            'signal_type': rec.getType().value,
            'target': rec.getTarget(),
            'stop': rec.getStop(),
        }
        for rec in recommendations
    ]

    result = {
        'symbol': symbol,
        'timeframe': timeframe,
        'mode': mode,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'current_price': current_price,
        'trend_levels': trend_levels,
        'all_points': all_points,
        'klines': kline_data,
        'signals': signals,
    }

    try:
        _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _OUTPUT_PATH.write_text(json.dumps(result, indent=2))
    except Exception as e:
        logger.error(f"Failed to write results.json: {e}")


def _ts(unix_seconds: Optional[int]) -> Optional[str]:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()
