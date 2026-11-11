"""The reset calendar. These are the rules the whole app's honesty depends on."""

from datetime import UTC, datetime, timedelta

import pytest

from d2_trader.seasons import (
    DEFAULT_SEASON_DAYS,
    Phase,
    PriceWindow,
    Season,
    SeasonEvent,
    SeasonState,
    apply_update,
    history_floor,
    reset_days,
    season_floor,
    merge_date,
    season_events,
    season_state,
    seasonal_note,
    typical_season_days,
    window_for,
)

S14 = Season(
    14, datetime(2026, 5, 23, tzinfo=UTC), datetime(2026, 8, 17, 18, tzinfo=UTC), end_source="seed"
)
S15 = Season(
    15, datetime(2026, 8, 22, tzinfo=UTC), datetime(2026, 11, 9, 19, tzinfo=UTC), end_source="seed"
)
S16 = Season(16, datetime(2026, 11, 14, 1, tzinfo=UTC), start_source="blizzard")
ALL = [S14, S15, S16]


def at(month, day, hour=12):
    return datetime(2026, month, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "phase"),
    [
        (at(8, 23), Phase.LAUNCH),
        (at(9, 4), Phase.LAUNCH),
        (at(9, 6), Phase.EARLY),
        (at(10, 1), Phase.MID),
        (at(10, 20), Phase.LATE),
        (at(11, 6), Phase.LATE),
        (at(11, 10), Phase.OFFSEASON),
        (at(11, 13), Phase.OFFSEASON),
        (at(11, 15), Phase.LAUNCH),
    ],
)
def test_phase_follows_the_calendar(now, phase):
    assert season_state(now, ALL).phase is phase


def test_no_seasons_is_unknown_not_a_crash():
    assert season_state(at(10, 1), []).phase is Phase.UNKNOWN


def test_before_the_first_recorded_season_is_unknown_and_names_what_is_coming():
    state = season_state(at(1, 1), ALL)
    assert state.phase is Phase.UNKNOWN
    assert state.next_season == S14


def test_offseason_names_the_next_season_as_soon_as_it_is_known():
    state = season_state(at(11, 10), ALL)
    assert state.season == S15
    assert state.next_season == S16


def test_offseason_with_no_announcement_yet_has_no_next_season():
    assert season_state(at(11, 10), [S14, S15]).next_season is None


def test_an_unannounced_end_is_estimated_and_marked_as_one():
    current = Season(16, at(11, 14))
    state = season_state(at(11, 20), [S14, S15, current])
    assert state.end_is_estimate is True
    assert state.ends_at == current.starts_at + timedelta(days=typical_season_days([S14, S15]))


def test_an_announced_end_is_not_marked_as_an_estimate():
    assert season_state(at(10, 1), ALL).end_is_estimate is False


def test_a_season_past_its_estimate_stays_late_rather_than_ending_by_itself():
    long_season = Season(16, datetime(2026, 1, 1, tzinfo=UTC))
    state = season_state(at(11, 1), [long_season])
    assert state.phase is Phase.LATE
    assert state.end_is_estimate is True


def test_typical_length_needs_two_finished_seasons():
    assert typical_season_days([S14]) == DEFAULT_SEASON_DAYS
    assert typical_season_days([S14, S15, S16]) == 82  # 86 and 79 days; S16 has no end yet.


def test_ladder_window_never_crosses_into_the_previous_season():
    now = at(11, 15)
    state = season_state(now, ALL)
    assert window_for(state, True, now).start == S16.starts_at


def test_ladder_market_is_closed_between_seasons():
    now = at(11, 11)
    state = season_state(now, ALL)
    assert isinstance(state, SeasonState)
    assert window_for(state, True, now) == PriceWindow(now - timedelta(days=5), closed=True)


def test_a_season_event_says_what_changed():
    [event] = [e for e in season_events(at(11, 8), ALL) if e.key == "s15:ending-soon"]
    assert isinstance(event, SeasonEvent)
    assert "3 days" in event.title


def test_non_ladder_market_stays_open_between_seasons():
    now = at(11, 11)
    assert window_for(season_state(now, ALL), False, now).closed is False


def test_non_ladder_window_restarts_at_the_transfer():
    now = at(11, 11)
    window = window_for(season_state(now, ALL), False, now)
    assert window.start == S15.ends_at


def test_mid_season_window_is_the_plain_lookback():
    now = at(10, 1)
    assert window_for(season_state(now, ALL), True, now).start == now - timedelta(days=14)


def test_a_ladder_price_draws_only_on_its_own_season():
    assert history_floor(at(10, 1), ALL, ladder=True) == S15.starts_at
    assert history_floor(at(11, 20), ALL, ladder=True) == S16.starts_at


def test_a_non_ladder_price_draws_back_to_the_last_transfer():
    assert history_floor(at(10, 1), ALL, ladder=False) == S14.ends_at
    assert history_floor(at(11, 20), ALL, ladder=False) == S15.ends_at


def test_before_any_season_there_is_no_floor():
    assert history_floor(at(1, 1), ALL, ladder=True) is None


def test_a_ladder_resets_at_each_start_and_a_non_ladder_at_each_transfer():
    assert reset_days(ALL, ladder=True) == {s.starts_at.date() for s in ALL}
    assert reset_days(ALL, ladder=False) == {S14.ends_at.date(), S15.ends_at.date()}


def test_the_season_floor_reads_straight_from_a_state():
    state = season_state(at(10, 1), ALL)
    assert season_floor(state, ladder=True) == S15.starts_at
    assert season_floor(state, ladder=False) == S14.ends_at


def test_notes_warn_at_both_ends_of_a_season():
    assert "launch" in seasonal_note(season_state(at(11, 15), ALL), True)
    assert "official" in seasonal_note(season_state(at(11, 1), ALL), True)
    assert "stale" in seasonal_note(season_state(at(11, 11), ALL), True)


def test_late_note_admits_when_the_end_is_only_an_estimate():
    current = Season(16, datetime(2026, 8, 1, tzinfo=UTC))
    assert "estimated" in seasonal_note(season_state(at(10, 20), [S14, S15, current]), True)


def test_non_ladder_gets_a_note_only_for_the_transfer():
    assert seasonal_note(season_state(at(11, 1), ALL), False) == ""
    assert "supply" in seasonal_note(season_state(at(11, 11), ALL), False)
    assert seasonal_note(season_state(at(10, 1), ALL), True) == ""


def test_higher_ranked_source_wins_and_lower_is_ignored():
    old, new = at(1, 1), at(1, 2)
    assert merge_date(old, "seed", new, "blizzard") == (new, "blizzard")
    assert merge_date(old, "blizzard", new, "diablo2io") == (old, "blizzard")
    assert merge_date(old, "manual", new, "blizzard") == (old, "manual")


def test_same_source_may_correct_itself():
    old, new = at(1, 1), at(1, 2)
    assert merge_date(old, "blizzard", new, "blizzard") == (new, "blizzard")


def test_merge_fills_a_gap_and_ignores_nothing_new():
    new = at(1, 2)
    assert merge_date(None, None, new, "diablo2io") == (new, "diablo2io")
    assert merge_date(new, "seed", None, "blizzard") == (new, "seed")


def test_apply_update_sets_the_right_boundary():
    updated = apply_update(S16, "end", at(12, 1), "blizzard", "http://x")
    assert (updated.ends_at, updated.end_source, updated.url) == (at(12, 1), "blizzard", "http://x")
    moved = apply_update(S16, "start", at(11, 20), "manual")
    assert (moved.starts_at, moved.start_source) == (at(11, 20), "manual")


def keys(now, seasons=None):
    return {e.key for e in season_events(now, seasons or ALL)}


def test_events_cover_each_step_of_a_reset():
    announced = Season(15, S15.starts_at, S15.ends_at, end_source="blizzard")
    seasons = [announced, S16]
    assert "s15:end-date:2026-11-09" in keys(at(10, 30), seasons)
    assert "s15:ending-soon" in keys(at(11, 8), seasons)
    assert "s15:ended" in keys(at(11, 10), seasons)
    assert "s16:start-date:2026-11-14" in keys(at(11, 10), seasons)
    assert "s16:starting-soon" in keys(at(11, 13, 12), seasons)
    assert "s16:started" in keys(at(11, 14, 12), seasons)


def test_a_seed_date_is_not_announced_as_news():
    assert "s15:end-date:2026-11-09" not in keys(at(10, 30))


def test_old_milestones_are_not_replayed_on_a_fresh_install():
    assert keys(at(11, 10), [S14, S15]) == {"s15:ended"}
    assert keys(at(11, 12), [S14, S15]) == set()
    assert keys(datetime(2027, 1, 1, tzinfo=UTC), [S14, S15]) == set()
