from typing import List, Tuple, TYPE_CHECKING

from bot.utils import Utils

if TYPE_CHECKING:
    from bot.trend import Trend

# (time_s, price, label, is_high, level)
ChartPoint = Tuple[int, float, str, bool, int]

_HEIGHT = 24
_WIDTH = 108   # fixed — always fills the panel
_Y_WIDTH = 10  # "  76,113 |"


def _place(grid: List[List[str]], row: int, col: int, text: str, w: int) -> None:
    col = max(0, min(col, w - len(text)))
    for j, ch in enumerate(text):
        if col + j < w:
            grid[row][col + j] = ch


def build_chart_points(trend: 'Trend', count: int = 4) -> List[ChartPoint]:
    """
    Collects the last `count` chronological swing points from each active trend level.
    Deduplicates by timestamp, keeping the label of the highest level.
    """
    best: dict = {}  # time_s -> (price, level, is_high)

    current = trend
    while current is not None:
        level = current.getLevel()
        combined = sorted(
            current.getHighPoints() + current.getLowPoints(),
            key=lambda p: p.getTime(),
        )
        for point in combined[-count:]:
            t = point.getTime()
            price = point.getHighValue() if point.isHigh() else point.getLowValue()
            if t not in best or best[t][1] < level:
                best[t] = (price, level, point.isHigh())
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    return [
        (t, price, f"L{level} {price:,.0f}", is_high, level)
        for t, (price, level, is_high) in sorted(best.items())
    ]


def render_chart(points: List[ChartPoint], tz: str = 'UTC') -> str:
    """
    Renders a fixed-size ASCII chart.

    X-axis: equal index spacing.
    Y-axis: rank-based — equal visual room per price level.
    Each point shows a label and a timestamp below the label.
    """
    if len(points) < 2:
        return "  (not enough points to render chart)"

    prices = [p[1] for p in points]
    n = len(points)
    h, w = _HEIGHT, _WIDTH

    # --- Y: rank-based ---
    sorted_unique = sorted(set(prices))
    max_rank = len(sorted_unique) - 1

    def to_row(price: float) -> int:
        rank = sorted_unique.index(price)
        return round((max_rank - rank) / max_rank * (h - 1)) if max_rank > 0 else h // 2

    # --- X: equal index spacing ---
    def to_col(i: int) -> int:
        return round(i / (n - 1) * (w - 1))

    grid: List[List[str]] = [[' '] * w for _ in range(h)]

    # Connecting lines
    for i in range(n - 1):
        c1, r1 = to_col(i), to_row(prices[i])
        c2, r2 = to_col(i + 1), to_row(prices[i + 1])
        steps = max(abs(c2 - c1), abs(r2 - r1), 1)
        for s in range(steps + 1):
            c = round(c1 + (c2 - c1) * s / steps)
            r = round(r1 + (r2 - r1) * s / steps)
            if 0 <= r < h and 0 <= c < w:
                grid[r][c] = '.'

    # Point markers
    for i, (_, price, _, is_high, _lv) in enumerate(points):
        c, r = to_col(i), to_row(price)
        if 0 <= r < h and 0 <= c < w:
            grid[r][c] = '^' if is_high else 'v'

    # Labels + timestamps
    # HIGH: label 2 rows above marker, time 1 row above (between label and marker)
    # LOW:  label 1 row below marker, time 2 rows below
    # Fall back gracefully when near top/bottom edge.
    for i, (t, price, label, is_high, _lv) in enumerate(points):
        c, r = to_col(i), to_row(price)
        ts = Utils.chart_time(t, tz)
        lc = c - len(label) // 2
        tc = c - len(ts) // 2

        if is_high:
            if r >= 2:
                label_row, time_row = r - 2, r - 1
            elif r == 1:
                label_row, time_row = r - 1, r + 1   # label above, time below marker
            else:                                       # r == 0, no room above
                label_row, time_row = r + 1, r + 2
        else:
            if r <= h - 3:
                label_row, time_row = r + 1, r + 2
            elif r == h - 2:
                label_row, time_row = r + 1, r - 1   # label below, time above marker
            else:                                       # r == h-1, no room below
                label_row, time_row = r - 2, r - 1

        if 0 <= label_row < h:
            _place(grid, label_row, lc, label, w)
        if 0 <= time_row < h:
            _place(grid, time_row, tc, ts, w)

    # Y-axis: 5 price ticks at evenly distributed rank positions
    y_ticks: dict = {}
    for tick in range(5):
        idx = round(tick / 4 * max_rank)
        price = sorted_unique[idx]
        r = round((max_rank - idx) / max_rank * (h - 1)) if max_rank > 0 else h // 2
        y_ticks[r] = f"{price:>8,.0f} |"

    lines = []
    for r in range(h):
        axis = y_ticks.get(r, ' ' * 9 + '|')
        lines.append(axis + ''.join(grid[r]))
    lines.append(' ' * 9 + '+' + '-' * w)

    return '\n'.join(lines)
