"""The price history chart, drawn on the server as SVG.

One axis, in Ist. Asks are a band with a median line, sales are a line with markers, and
season boundaries are always visible, because a price comparison across a reset is
meaningless and the chart has to say so. Hover detail is in `static/app.js`.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape

from markupsafe import Markup

from d2_trader.currency import format_like, format_range, format_value
from d2_trader.forum_gold import FgEstimate
from d2_trader.seasons import Season
from d2_trader.stats import DayPoint
from d2_trader.trend import PathPoint

WIDTH, HEIGHT = 860, 300
LEFT, RIGHT, TOP, BOTTOM = 52, 20, 26, 28
MIN_POINTS = 2
# Connecting two days further apart than this would draw data that does not exist.
MAX_GAP_DAYS = 3
TICKS = 4


@dataclass(frozen=True)
class Chart:
    svg: Markup
    data: Markup


def nice_ceiling(value: float) -> float:
    """The next round number above `value`, so axis labels read cleanly."""
    if value <= 0:
        return 1.0
    magnitude = 10.0 ** (len(str(int(value))) - 1) if value >= 1 else 0.1
    while magnitude > value:
        magnitude /= 10
    for step in (1, 2, 2.5, 4, 6, 8, 10):
        if step * magnitude >= value:
            return step * magnitude
    return 10 * magnitude


class Scale:
    def __init__(self, start: date, end: date, ceiling: float) -> None:
        self.start = start
        self.days = max((end - start).days, 1)
        self.ceiling = ceiling

    def x(self, day: date) -> float:
        span = WIDTH - LEFT - RIGHT
        return round(LEFT + (day - self.start).days / self.days * span, 1)

    def y(self, value: float) -> float:
        span = HEIGHT - TOP - BOTTOM
        return round(TOP + (1 - value / self.ceiling) * span, 1)


def split_runs(days: list[DayPoint]) -> list[list[DayPoint]]:
    """Consecutive stretches of points, so a line breaks where the data does."""
    runs: list[list[DayPoint]] = []
    for point in days:
        if runs and (point.day - runs[-1][-1].day).days <= MAX_GAP_DAYS:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def _polyline(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x},{y}" for x, y in points)


def _trace(run: list[DayPoint], field: str, scale: Scale) -> list[tuple[float, float]]:
    """A run's points along one field. Runs are filtered, so each has a value."""
    return [(scale.x(p.day), scale.y(getattr(p, field))) for p in run]


def _asks(days: list[DayPoint], scale: Scale) -> list[str]:
    marks = []
    for run in split_runs([p for p in days if p.ask_median is not None]):
        line = _trace(run, "ask_median", scale)
        marks.append(f'<polyline class="line ask" points="{_polyline(line)}"/>')
    return marks


def _sales(days: list[DayPoint], scale: Scale) -> list[str]:
    """Each day's median sale as a dot. The line through them is the model's, below."""
    points = [p for p in days if p.sold_median is not None]
    return [
        f'<circle class="dot sold" cx="{x}" cy="{y}" r="4"/>'
        for x, y in _trace(points, "sold_median", scale)
    ]


def _estimate(path: list[PathPoint], scale: Scale) -> list[str]:
    """The modelled price and its 95% band, drawn through quiet days too."""
    if len(path) < MIN_POINTS:
        return []
    top = [(scale.x(p.day), scale.y(min(p.high, scale.ceiling))) for p in path]
    bottom = [(scale.x(p.day), scale.y(p.low)) for p in reversed(path)]
    line = [(scale.x(p.day), scale.y(p.value)) for p in path]
    return [
        f'<polygon class="band sold" points="{_polyline(top + bottom)}"/>',
        f'<polyline class="line sold" points="{_polyline(line)}"/>',
    ]


def _grid(scale: Scale) -> list[str]:
    marks = []
    for tick in range(TICKS + 1):
        value = scale.ceiling * tick / TICKS
        y = scale.y(value)
        marks.append(f'<line class="grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y}" y2="{y}"/>')
        marks.append(
            f'<text class="axis" x="{LEFT - 8}" y="{y + 4}" text-anchor="end">{value:g}</text>'
        )
    return marks


def _date_axis(scale: Scale) -> list[str]:
    marks = []
    for tick in range(5):
        day = scale.start + timedelta(days=round(scale.days * tick / 4))
        anchor = ("start", "middle", "middle", "middle", "end")[tick]
        marks.append(
            f'<text class="axis" x="{scale.x(day)}" y="{HEIGHT - 8}" '
            f'text-anchor="{anchor}">{day:%b %d}</text>'
        )
    return marks


def _in_range(day: date, scale: Scale) -> bool:
    return 0 <= (day - scale.start).days <= scale.days


def _seasons(seasons: list[Season], scale: Scale, now: datetime) -> list[str]:
    """Off-season spans shaded, and a labelled rule at every season start."""
    marks = []
    ordered = sorted(seasons, key=lambda s: s.number)
    for index, season in enumerate(ordered):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        start = season.starts_at.date()
        if _in_range(start, scale):
            x = scale.x(start)
            marks.append(
                f'<line class="reset" x1="{x}" x2="{x}" y1="{TOP}" y2="{HEIGHT - BOTTOM}"/>'
            )
            marks.append(
                f'<text class="reset-label" x="{x + 4}" y="{TOP - 8}">S{season.number}</text>'
            )
        if season.ends_at and season.ends_at <= now:
            gap_end = following.starts_at.date() if following else now.date()
            left = scale.x(max(season.ends_at.date(), scale.start))
            right = scale.x(min(gap_end, scale.start + timedelta(days=scale.days)))
            if right > left:
                marks.append(
                    f'<rect class="gap" x="{left}" y="{TOP}" width="{round(right - left, 1)}" '
                    f'height="{HEIGHT - TOP - BOTTOM}"/>'
                )
    return marks


def _tooltip_rows(
    days: list[DayPoint], path: list[PathPoint], scale: Scale, rates: dict[str, float]
) -> list[dict]:
    modelled = {p.day: p for p in path}
    rows = []
    for p in days:
        lines = [
            f"Median sold: {format_value(p.sold_median, rates)} ({p.sold_count})",
            f"Median ask: {format_value(p.ask_median, rates)}",
            f"Asks: {format_range(p.ask_low, p.ask_high, rates)}",
        ]
        estimate = modelled.get(p.day)
        if estimate:
            band = format_range(estimate.low, estimate.high, rates)
            value = format_like(estimate.value, estimate.high, rates)
            lines.insert(0, f"Estimate: {value} (95%: {band})")
        rows.append({"x": scale.x(p.day), "day": f"{p.day:%b %d, %Y}", "lines": lines})
    return rows


def _finish(marks: list[str], rows: list[dict], title: str) -> Chart:
    svg = (
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" '
        f'aria-label="{escape(title, quote=True)}">{"".join(marks)}'
        f'<line class="cross" x1="0" x2="0" y1="{TOP}" y2="{HEIGHT - BOTTOM}" hidden/>'
        f'<rect class="hit" x="{LEFT}" y="{TOP}" width="{WIDTH - LEFT - RIGHT}" '
        f'height="{HEIGHT - TOP - BOTTOM}"/></svg>'
    )
    payload = json.dumps(rows).replace("<", "\\u003c")
    return Chart(
        Markup(svg),  # noqa: S704 - built here from numbers and an escaped title
        Markup(payload),  # noqa: S704 - JSON with every `<` already escaped
    )


def fg_per_ist(estimate: FgEstimate) -> tuple[float, float, float]:
    """The rate the way traders say it, "an Ist is 12 fg", with its interval flipped too."""
    return 1 / estimate.ist_per_fg, 1 / estimate.high, 1 / estimate.low


def _rate_marks(points: list[tuple[date, FgEstimate]], scale: Scale) -> list[str]:
    flipped = [(day, fg_per_ist(e)) for day, e in points]
    # A long gap can widen the interval far past anything plotted, so it is cut at the top.
    top = [(scale.x(d), scale.y(min(high, scale.ceiling))) for d, (_, _, high) in flipped]
    bottom = [(scale.x(d), scale.y(low)) for d, (_, low, _) in reversed(flipped)]
    line = [(scale.x(day), scale.y(value)) for day, (value, _, _) in flipped]
    dots = [
        f'<circle class="dot sold" cx="{scale.x(day)}" cy="{scale.y(fg_per_ist(e)[0])}" r="3"/>'
        for day, e in points
        if e.observations
    ]
    return [
        f'<polygon class="band" points="{_polyline(top + bottom)}"/>',
        f'<polyline class="line ask" points="{_polyline(line)}"/>',
        *dots,
    ]


def _rate_rows(points: list[tuple[date, FgEstimate]], scale: Scale) -> list[dict]:
    rows = []
    for day, estimate in points:
        value, low, high = fg_per_ist(estimate)
        count = estimate.observations
        evidence = f"{count} rune prices that day" if count else "no prices that day: estimated"
        rows.append(
            {
                "x": scale.x(day),
                "day": f"{day:%b %d, %Y}",
                "lines": [
                    f"1 Ist = {value:.3g} fg",
                    f"95% CI {low:.3g} to {high:.3g} fg",
                    evidence,
                ],
            }
        )
    return rows


def rate_chart(
    history: dict[date, FgEstimate], seasons: list[Season], *, now: datetime, title: str
) -> Chart | None:
    """Forum gold per Ist over time: the model's line, its 95% band, and days with prices."""
    points = sorted(history.items())
    if len(points) < MIN_POINTS:
        return None
    values = [fg_per_ist(e) for _, e in points]
    peak = max(min(high, 2 * value) for value, _, high in values)
    scale = Scale(points[0][0], max(points[-1][0], now.date()), nice_ceiling(peak))
    marks = [*_seasons(seasons, scale, now), *_grid(scale), *_date_axis(scale)]
    return _finish([*marks, *_rate_marks(points, scale)], _rate_rows(points, scale), title)


def price_chart(
    days: list[DayPoint],
    seasons: list[Season],
    rates: dict[str, float],
    *,
    now: datetime,
    title: str,
    path: list[PathPoint] | None = None,
) -> Chart | None:
    """The chart, or None when there are too few days to draw a line through."""
    if len(days) < MIN_POINTS:
        return None
    path = path or []
    highs = [v for p in days for v in (p.ask_median, p.sold_median) if v is not None]
    highs += [min(p.high, 2 * p.value) for p in path]
    start = min(days[0].day, path[0].day) if path else days[0].day
    scale = Scale(start, max(days[-1].day, now.date()), nice_ceiling(max(highs, default=1)))
    marks = [
        *_seasons(seasons, scale, now),
        *_grid(scale),
        *_date_axis(scale),
        *_estimate(path, scale),
        *_asks(days, scale),
        *_sales(days, scale),
    ]
    return _finish(marks, _tooltip_rows(days, path, scale, rates), title)
