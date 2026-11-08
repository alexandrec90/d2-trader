from datetime import UTC, datetime

from d2_trader.seasons import Phase
from d2_trader.services.calendar import (
    SEED_SEASONS,
    Disagreement,
    apply_date,
    current_state,
    ensure_seed_seasons,
    find_disagreements,
    load_seasons,
)
from d2_trader.sources.blizzard import ScheduleDate
from d2_trader.sources.diablo2io import BannerDate


def by_number(seasons):
    return {s.number: s for s in seasons}


async def test_seeding_is_idempotent(session):
    await ensure_seed_seasons(session)
    await ensure_seed_seasons(session)
    assert len(await load_seasons(session)) == len(SEED_SEASONS)


async def test_seeding_never_overwrites_a_later_correction(session):
    await ensure_seed_seasons(session)
    moved = datetime(2026, 11, 20, tzinfo=UTC)
    await apply_date(session, 16, "start", moved, "manual")
    await ensure_seed_seasons(session)
    session.expire_all()
    assert by_number(await load_seasons(session))[16].starts_at == moved


async def test_today_is_late_in_season_15(session):
    await ensure_seed_seasons(session)
    state, _ = await current_state(session, datetime(2026, 11, 6, tzinfo=UTC))
    assert state.phase is Phase.LATE
    assert state.season.number == 15
    assert state.next_season.number == 16


async def test_blizzard_confirms_a_seeded_date(session):
    await ensure_seed_seasons(session)
    at = datetime(2026, 11, 9, 19, tzinfo=UTC)
    await apply_date(session, 15, "end", at, "blizzard", "https://news")
    season = by_number(await load_seasons(session))[15]
    assert (season.end_source, season.url) == ("blizzard", "https://news")


async def test_a_new_season_appears_from_its_start_date(session):
    at = datetime(2027, 2, 1, tzinfo=UTC)
    await apply_date(session, 17, "start", at, "blizzard")
    assert by_number(await load_seasons(session))[17].starts_at == at


async def test_an_end_date_alone_cannot_invent_a_season(session):
    await apply_date(session, 18, "end", datetime(2027, 2, 1, tzinfo=UTC), "blizzard")
    assert await load_seasons(session) == []


async def test_diablo2io_cannot_override_blizzard(session):
    await ensure_seed_seasons(session)
    official = datetime(2026, 11, 9, 19, tzinfo=UTC)
    await apply_date(session, 15, "end", official, "blizzard")
    await apply_date(session, 15, "end", datetime(2026, 11, 9, tzinfo=UTC), "diablo2io")
    assert by_number(await load_seasons(session))[15].ends_at == official


def test_matching_sources_are_not_a_disagreement():
    official = [ScheduleDate(15, "end", datetime(2026, 11, 9, 19, tzinfo=UTC))]
    # The banner carries no time of day, so it reads as midnight the same date.
    banner = [BannerDate(15, "end", datetime(2026, 11, 9, tzinfo=UTC))]
    assert find_disagreements(official, banner) == []


def test_a_real_difference_is_reported():
    official = [ScheduleDate(16, "start", datetime(2026, 11, 14, 1, tzinfo=UTC))]
    banner = [BannerDate(16, "start", datetime(2026, 11, 21, 1, tzinfo=UTC))]
    assert find_disagreements(official, banner) == [
        Disagreement(16, "start", official[0].at, banner[0].at)
    ]


def test_a_date_only_one_source_mentions_is_not_a_disagreement():
    banner = [BannerDate(16, "start", datetime(2026, 11, 21, tzinfo=UTC))]
    assert find_disagreements([], banner) == []
