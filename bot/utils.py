from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class Utils:
    @staticmethod
    def time_to_str(timestamp: int, tz: str = 'UTC') -> str:
        if not timestamp:
            return 'N/A'
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo(tz)).strftime('%Y-%m-%d %H:%M:%S')

    @staticmethod
    def short_time(timestamp: int, tz: str = 'UTC') -> str:
        """'Apr 18 15:00' in the given timezone."""
        if not timestamp:
            return '—'
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo(tz)).strftime('%b %d %H:%M')

    @staticmethod
    def chart_time(timestamp: int, tz: str = 'UTC') -> str:
        """PHP 'j.n H:i' — e.g. '18.4 15:00' — in the given timezone."""
        if not timestamp:
            return '—'
        dt = datetime.fromtimestamp(timestamp, tz=ZoneInfo(tz))
        return f"{dt.day}.{dt.month} {dt.strftime('%H:%M')}"
