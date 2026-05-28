#!/usr/bin/env python3
"""Replay the trend analyzer state at a historical candle index.

Usage:
    python replay_api.py '{"symbol": "SOLUSDT", "candle_index": 450}'

Prints JSON to stdout on success.
Prints {"error": "..."} and exits with code 1 on failure.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bot.analyzer import Analyzer

RESULTS_DIR = Path(__file__).parent / 'dashboard' / 'public'


def _ts(unix_seconds) -> str | None:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()


def _format_trend_levels(trend) -> list:
    levels = []
    current = trend
    while current is not None:
        last_high = current.getLastHigh()
        last_low  = current.getLastLow()
        levels.append({
            'level':     current.getLevel(),
            'direction': ('ASC' if current.isAscending() else 'DESC' if current.isDescending() else 'NONE'),
            'bos':       current.getBreakOfStructure(),
            'bos_since': _ts(current.getBreakOfStructureTime()),
            'last_high': {'price': last_high.getHighValue(), 'time': _ts(last_high.getTime())} if last_high else None,
            'last_low':  {'price': last_low.getLowValue(),  'time': _ts(last_low.getTime())}  if last_low  else None,
        })
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None
    return levels


def _format_rec(rec) -> dict:
    return {
        'level':       rec.getLevel(),
        'side':        rec.getSide(),
        'signal_type': rec.getType().value,
        'is_reversal': rec.isReversal(),
        'entry':       rec.getEntryPrice(),
        'target':      rec.getTarget(),
        'stop':        rec.getStop(),
        'rr':          rec.getRR(),
        'precision':   rec.getPrecision(),
    }


def replay(symbol: str, candle_index: int) -> dict:
    """Build and return historical trend state at candle_index.

    Raises FileNotFoundError if the results JSON for symbol is missing.
    """
    if not re.fullmatch(r'[A-Z0-9]{2,20}', symbol):
        raise ValueError(f'Invalid symbol: {symbol!r}')

    path = RESULTS_DIR / f'results_{symbol}.json'
    if not path.exists():
        raise FileNotFoundError(f'results_{symbol}.json not found')

    data = json.loads(path.read_text())

    # Convert dashboard kline format {time(s), open, high, low, close}
    # to analyzer format [timestamp_ms, open, high, low, close, volume].
    analyzer_klines = [
        [int(k['time']) * 1000, float(k['open']), float(k['high']), float(k['low']), float(k['close']), 0]
        for k in data['klines']
    ]

    if candle_index < 0:
        return {'trend_levels': [], 'all_points': [], 'signals': []}

    sliced = analyzer_klines[:candle_index + 1]
    if not sliced:
        return {'trend_levels': [], 'all_points': [], 'signals': []}

    analyzer = Analyzer(swing_neighbours=2)
    analyzer.build_from_klines(sliced)

    trend = analyzer.get_trend()
    if trend is None:
        return {'trend_levels': [], 'all_points': [], 'signals': []}

    replay_price = sliced[-1][4]  # close of last sliced candle
    analyzer.update_price(replay_price)

    trend_levels = _format_trend_levels(trend)

    raw_points = analyzer.get_all_points()
    raw_points.sort(key=lambda p: p['time'] or 0, reverse=True)
    all_points = [
        {
            'time':   _ts(p['time']),
            'level':  p['level'],
            'type':   p['type'],
            'price':  p['price'],
            'active': p['active'],
        }
        for p in raw_points
    ]

    recs = trend.getRecommendations(entry_price=replay_price, proximity_zone_pct=10.0)
    signals = [_format_rec(r) for r in recs]

    return {'trend_levels': trend_levels, 'all_points': all_points, 'signals': signals}


if __name__ == '__main__':
    try:
        args   = json.loads(sys.argv[1])
        symbol = str(args['symbol']).strip().upper()
        idx    = int(args['candle_index'])
        result = replay(symbol, idx)
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(1)
