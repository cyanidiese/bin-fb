from datetime import datetime, timezone
from typing import List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bot.chart import build_chart_points, render_chart
from bot.recommendation import Recommendation
from bot.trend import Trend
from bot.utils import Utils
from config.settings import Settings

console = Console(width=130)


def _fmt_price(price: float) -> str:
    return f"{price:,.2f}"



def _direction_text(trend: Trend) -> Text:
    if not trend.hasDefinedTrend():
        return Text("─  NONE", style="dim")
    if trend.isAscending():
        return Text("▲  ASC", style="green bold")
    return Text("▼  DESC", style="red bold")


def _all_points_table(trend: Trend, tz: str = 'UTC') -> Table:
    seen: set = set()
    rows = []

    current: Optional[Trend] = trend
    while current is not None:
        level = current.getLevel()
        for point in current.getHighPoints():
            key = (point.getTime(), level, True)
            if key not in seen:
                seen.add(key)
                rows.append((point.getTime(), level, True, point.getHighValue()))
        for point in current.getLowPoints():
            key = (point.getTime(), level, False)
            if key not in seen:
                seen.add(key)
                rows.append((point.getTime(), level, False, point.getLowValue()))
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    rows.sort(key=lambda r: r[0], reverse=True)
    rows = rows[:50]

    mid = (len(rows) + 1) // 2

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", pad_edge=False)
    for _ in range(2):
        table.add_column("Time", no_wrap=True)
        table.add_column("Lvl", no_wrap=True)
        table.add_column("Type", no_wrap=True)
        table.add_column("Price", no_wrap=True)

    left = rows[:mid]
    right = rows[mid:]

    for i in range(mid):
        lt, ll, lh, lp = left[i]
        l_type = Text("▲  High", style="green") if lh else Text("▼  Low", style="red")
        if i < len(right):
            rt, rl, rh, rp = right[i]
            r_type = Text("▲  High", style="green") if rh else Text("▼  Low", style="red")
            table.add_row(Utils.short_time(lt, tz), f"L{ll}", l_type, _fmt_price(lp),
                          Utils.short_time(rt, tz), f"L{rl}", r_type, _fmt_price(rp))
        else:
            table.add_row(Utils.short_time(lt, tz), f"L{ll}", l_type, _fmt_price(lp),
                          "", "", Text(""), "")

    return table


def _trend_table(trend: Trend, tz: str = 'UTC') -> Table:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan", pad_edge=False)
    table.add_column("Lvl", no_wrap=True)
    table.add_column("Direction", no_wrap=True)
    table.add_column("Break of Structure", no_wrap=True)
    table.add_column("BoS Since", no_wrap=True)
    table.add_column("Last High", no_wrap=True)
    table.add_column("Last Low", no_wrap=True)

    current: Optional[Trend] = trend
    while current is not None:
        bb = _fmt_price(current.getBreakOfStructure()) if current.hasBreakOfStructure() else "—"
        bb_time = Utils.short_time(current.getBreakOfStructureTime(), tz) if current.hasBreakOfStructure() else "—"
        last_high = (
            f"{_fmt_price(current.getLastHigh().getHighValue())} @ {Utils.short_time(current.getLastHigh().getTime(), tz)}"
            if current.hasHighs() else "—"
        )
        last_low = (
            f"{_fmt_price(current.getLastLow().getLowValue())} @ {Utils.short_time(current.getLastLow().getTime(), tz)}"
            if current.hasLows() else "—"
        )
        table.add_row(
            f"L{current.getLevel()}",
            _direction_text(current),
            bb,
            bb_time,
            last_high,
            last_low,
        )
        current = current.getBiggerTrend() if current.hasBiggerTrend() else None

    return table


def show(
    settings: Settings,
    trend: Optional[Trend],
    price: float,
    recommendations: List[Recommendation],
    candle_time: Optional[int] = None,
) -> None:
    """Print the full console UI. Called on startup and on each candle close."""
    ts = Utils.time_to_str(candle_time) if candle_time else \
        datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    mode_style = "yellow" if settings.trading_mode == 'testnet' else "red bold"

    console.rule()
    console.print(
        f"[bold]{settings.symbol}[/bold]  [dim]{settings.timeframe}[/dim]  "
        f"[{mode_style}]{settings.trading_mode.upper()}[/{mode_style}]  |  "
        f"price: [bold white]{_fmt_price(price)}[/bold white]  |  "
        f"[dim]{ts} UTC[/dim]"
    )

    if trend is None:
        console.print("[dim]  No trend data yet.[/dim]")
        return

    tz = settings.timezone

    # Chart
    points = build_chart_points(trend)
    if len(points) >= 2:
        chart = render_chart(points, tz=tz)
        console.print(Panel(chart, title="[bold blue]Swing Points[/bold blue]", border_style="blue"))
    else:
        console.print("[dim]  Chart: not enough points yet.[/dim]")

    # Trend levels table
    console.print(Panel(_trend_table(trend, tz), title="[bold cyan]Trend Levels[/bold cyan]", border_style="cyan"))

    # All points table
    console.print(Panel(_all_points_table(trend, tz), title="[bold cyan]All Points (newest first)[/bold cyan]", border_style="cyan"))

    # Signals
    if recommendations:
        rec_lines = Text()
        for rec in recommendations:
            style = "green" if rec.getSide() == "BUY" else "red"
            rec_lines.append(f"  {rec}\n", style=style)
        console.print(Panel(rec_lines, title="[bold yellow]Signals[/bold yellow]", border_style="yellow"))
    else:
        console.print(Panel("[dim]  No signals.[/dim]", title="Signals", border_style="dim"))
