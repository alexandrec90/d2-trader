"""The ladder calendar: which phase the market is in, and what that does to prices.

A reset is the largest thing that ever happens to a D2R price. Ladder items move to
non-ladder when a season ends, the ladder economy restarts from nothing, and it stays
empty for the days between seasons (Season 15 ended on Nov 9, Season 16 starts Nov 14).
So three rules follow from this module, and everything else leans on them:

- a ladder price window never reaches back past the season's start;
- a ladder market is closed between seasons: nothing there is a live price;
- a non-ladder window never reaches back past the last transfer of ladder items.
"""

import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum

# Used until two seasons have finished to measure from. Seasons 14 and 15 ran 87 and 79
# days, so the older, six-month figure would badly overstate how far off a reset is.
DEFAULT_SEASON_DAYS = 84
LAUNCH_DAYS = 14
EARLY_DAYS = 35
LATE_WINDOW_DAYS = 21

# Who may overwrite whom: a higher rank wins, and an equal rank is a correction.
SOURCE_RANK = {"manual": 3, "blizzard": 2, "diablo2io": 1, "seed": 0}


class Phase(StrEnum):
    LAUNCH = "launch"
    EARLY = "early"
    MID = "mid"
    LATE = "late"
    OFFSEASON = "offseason"
    UNKNOWN = "unknown"


LOOKBACK_DAYS = {
    Phase.LAUNCH: 3,
    Phase.EARLY: 7,
    Phase.MID: 14,
    Phase.LATE: 5,
    Phase.OFFSEASON: 5,
    Phase.UNKNOWN: 14,
}


@dataclass(frozen=True)
class Season:
    number: int
    starts_at: datetime
    ends_at: datetime | None = None
    start_source: str = "seed"
    end_source: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SeasonState:
    phase: Phase
    season: Season | None = None
    next_season: Season | None = None
    ends_at: datetime | None = None
    end_is_estimate: bool = False
    day_of_season: int | None = None
    last_transfer_at: datetime | None = None


@dataclass(frozen=True)
class PriceWindow:
    start: datetime
    closed: bool = False


@dataclass(frozen=True)
class SeasonEvent:
    key: str
    title: str
    body: str


def typical_season_days(seasons: list[Season]) -> int:
    lengths = [(s.ends_at - s.starts_at).days for s in seasons if s.ends_at]
    return round(statistics.median(lengths)) if len(lengths) >= 2 else DEFAULT_SEASON_DAYS


def _phase_within_season(day: int, days_left: float) -> Phase:
    if day < LAUNCH_DAYS:
        return Phase.LAUNCH
    if days_left <= LATE_WINDOW_DAYS:
        return Phase.LATE
    return Phase.EARLY if day < EARLY_DAYS else Phase.MID


def season_state(now: datetime, seasons: list[Season]) -> SeasonState:
    """Where `now` sits on the calendar. An unannounced end is estimated, and says so."""
    ordered = sorted(seasons, key=lambda s: s.number)
    started = [s for s in ordered if s.starts_at <= now]
    upcoming = [s for s in ordered if s.starts_at > now]
    next_season = upcoming[0] if upcoming else None
    ended = [s.ends_at for s in ordered if s.ends_at and s.ends_at <= now]
    last_transfer = max(ended, default=None)
    if not started:
        return SeasonState(Phase.UNKNOWN, next_season=next_season)

    current = started[-1]
    if current.ends_at and current.ends_at <= now:
        return SeasonState(
            Phase.OFFSEASON,
            season=current,
            next_season=next_season,
            ends_at=current.ends_at,
            last_transfer_at=last_transfer,
        )
    ends_at = current.ends_at or current.starts_at + timedelta(days=typical_season_days(ordered))
    day = (now - current.starts_at).days
    days_left = (ends_at - now).total_seconds() / 86400
    return SeasonState(
        _phase_within_season(day, days_left),
        season=current,
        next_season=next_season,
        ends_at=ends_at,
        end_is_estimate=current.ends_at is None,
        day_of_season=day,
        last_transfer_at=last_transfer,
    )


def window_for(state: SeasonState, ladder: bool, now: datetime) -> PriceWindow:
    """The stretch of history that still describes today's market."""
    start = now - timedelta(days=LOOKBACK_DAYS[state.phase])
    if not ladder:
        floor = state.last_transfer_at
        return PriceWindow(max(start, floor) if floor else start)
    if state.season is None:
        return PriceWindow(start)
    return PriceWindow(max(start, state.season.starts_at), closed=state.phase is Phase.OFFSEASON)


def reset_days(seasons: list[Season], ladder: bool) -> frozenset[date]:
    """Days a market's prices may jump: a ladder's starts, or a non-ladder's transfers."""
    if ladder:
        return frozenset(s.starts_at.date() for s in seasons)
    return frozenset(s.ends_at.date() for s in seasons if s.ends_at)


def season_floor(state: SeasonState, ladder: bool) -> datetime | None:
    """The oldest moment that still belongs to this market: its season, or since the transfer."""
    if ladder:
        return state.season.starts_at if state.season else None
    return state.last_transfer_at


def history_floor(at: datetime, seasons: list[Season], ladder: bool) -> datetime | None:
    """How far back a price at `at` may draw on: its season's start, or the last transfer."""
    return season_floor(season_state(at, seasons), ladder)


def seasonal_note(state: SeasonState, ladder: bool) -> str:
    """One line that says how much of a price is the calendar rather than the item."""
    number = state.season.number if state.season else None
    if state.phase is Phase.OFFSEASON:
        if ladder:
            return f"Season {number} is over: ladder listings are stale until the next one."
        return "Ladder items have just moved to non-ladder, so supply here is jumping."
    if not ladder:
        return ""
    if state.phase is Phase.LAUNCH:
        return f"Season {number} day {state.day_of_season}: launch prices, which usually fall."
    if state.phase is Phase.LATE:
        hedge = "estimated" if state.end_is_estimate else "official"
        return f"Season {number} is ending ({hedge} date): ladder prices usually slide into it."
    return ""


def merge_date(
    current: datetime | None,
    current_source: str | None,
    new: datetime | None,
    new_source: str,
) -> tuple[datetime | None, str | None]:
    """Keep the better-sourced date. A lower-ranked source never overrides a higher one."""
    if new is None:
        return current, current_source
    if current is None:
        return new, new_source
    rank = SOURCE_RANK.get(current_source or "", -1)
    return (new, new_source) if SOURCE_RANK[new_source] >= rank else (current, current_source)


def apply_update(season: Season, kind: str, value: datetime, source: str, url=None) -> Season:
    """`season` with its start or end date updated, if `source` is entitled to."""
    if kind == "start":
        at, by = merge_date(season.starts_at, season.start_source, value, source)
        return replace(
            season,
            starts_at=at or season.starts_at,
            start_source=by or season.start_source,
            url=url or season.url,
        )
    at, by = merge_date(season.ends_at, season.end_source, value, source)
    return replace(season, ends_at=at, end_source=by, url=url or season.url)


def _announcements(season: Season, now: datetime) -> list[SeasonEvent]:
    events = []
    if season.ends_at and season.ends_at > now and season.end_source != "seed":
        events.append(
            SeasonEvent(
                f"s{season.number}:end-date:{season.ends_at:%Y-%m-%d}",
                f"Season {season.number} end date set",
                f"Ladder Season {season.number} ends {season.ends_at:%a %b %d %H:%M} UTC "
                f"(source: {season.end_source}).",
            )
        )
    if season.starts_at > now and season.start_source != "seed":
        events.append(
            SeasonEvent(
                f"s{season.number}:start-date:{season.starts_at:%Y-%m-%d}",
                f"Season {season.number} start date set",
                f"Ladder Season {season.number} starts {season.starts_at:%a %b %d %H:%M} UTC "
                f"(source: {season.start_source}).",
            )
        )
    return events


def _milestones(season: Season, now: datetime) -> list[SeasonEvent]:
    number = season.number
    events = []
    if season.ends_at:
        until_end = season.ends_at - now
        if timedelta(0) < until_end <= timedelta(days=3):
            events.append(
                SeasonEvent(
                    f"s{number}:ending-soon",
                    f"Season {number} ends within 3 days",
                    "Ladder prices are at their weakest; ladder trades must finish before the "
                    "reset moves everything to non-ladder.",
                )
            )
        if timedelta(days=-2) <= until_end <= timedelta(0):
            events.append(
                SeasonEvent(
                    f"s{number}:ended",
                    f"Season {number} has ended",
                    "Ladder alerts are paused until the next season. Non-ladder supply is "
                    "jumping as ladder characters transfer.",
                )
            )
    until_start = season.starts_at - now
    if timedelta(0) < until_start <= timedelta(days=1):
        events.append(
            SeasonEvent(
                f"s{number}:starting-soon",
                f"Season {number} starts within a day",
                "Ladder prices restart from nothing; expect them high and falling.",
            )
        )
    if timedelta(days=-2) <= until_start <= timedelta(0):
        events.append(
            SeasonEvent(
                f"s{number}:started",
                f"Season {number} has started",
                "Ladder price history starts over from this moment.",
            )
        )
    return events


def season_events(now: datetime, seasons: list[Season]) -> list[SeasonEvent]:
    """Everything worth telling the operator now. Each key is meant to be sent once."""
    events = []
    for season in sorted(seasons, key=lambda s: s.number):
        events += _announcements(season, now) + _milestones(season, now)
    return events
