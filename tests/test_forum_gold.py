"""The forum gold rate: every rune reading feeds one model of a drifting rate."""

from datetime import UTC, date, datetime, timedelta

import pytest

from d2_trader.forum_gold import (
    FgEstimate,
    FgQuote,
    RuneReading,
    Valuation,
    fg_history,
    rate_on,
    readings,
)

DAY = datetime(2026, 11, 1, 12, tzinfo=UTC)
# Ist values, as Traderie trades would have measured them.
VALUES = {"ist": 1.0, "ber": 12.0, "jah": 16.0, "lo": 6.0}


def quote(rune, fg, at=DAY):
    return FgQuote(rune, fg, at)


def test_a_reading_summarises_a_rune_by_its_median_fg():
    [reading] = readings([quote("ber", 100), quote("ber", 120), quote("ber", 500)], VALUES)
    assert reading == RuneReading("ber", 120, 12.0, 3)
    assert reading.ist_per_fg == pytest.approx(0.1)


def test_a_rune_without_an_ist_value_gives_no_reading():
    assert readings([quote("annihilus", 1000)], VALUES) == []


def test_a_free_or_zero_price_is_ignored():
    assert readings([quote("ber", 0)], VALUES) == []


def test_agreeing_runes_give_their_rate_and_a_tight_interval():
    # Every rune says 1 Ist = 10 fg.
    quotes = [quote("ist", 10), quote("ber", 120), quote("jah", 160), quote("lo", 60)]
    today = fg_history(quotes, VALUES, until=DAY.date())[DAY.date()]
    assert today.ist_per_fg == pytest.approx(0.1)
    assert today.low < 0.1 < today.high
    assert today.observations == 4


def test_every_reading_from_every_rune_is_used():
    quotes = [quote("ber", 120), quote("ber", 130), quote("ist", 9)]
    assert fg_history(quotes, VALUES, until=DAY.date())[DAY.date()].observations == 3


def test_a_day_without_prices_still_has_a_rate_with_a_wider_interval():
    quotes = [quote("ber", 120, DAY - timedelta(days=d)) for d in range(10, 20)]
    quotes += [quote("ist", 10, DAY - timedelta(days=d)) for d in range(10, 20)]
    history = fg_history(quotes, VALUES, until=DAY.date())
    priced, quiet = history[(DAY - timedelta(days=12)).date()], history[DAY.date()]
    assert quiet.observations == 0
    assert quiet.ist_per_fg == pytest.approx(0.1, rel=0.05)
    assert quiet.high / quiet.low > priced.high / priced.low


def test_a_drifting_rate_is_followed_not_averaged_away():
    """fg losing value through a season: 10 fg an Ist a month ago, 14 now."""
    quotes = []
    for back in range(30, -1, -1):
        fg_per_ist = 14 - 4 * back / 30
        when = DAY - timedelta(days=back)
        quotes += [quote("ber", 12 * fg_per_ist, when), quote("ist", fg_per_ist, when)]
    today = fg_history(quotes, VALUES, until=DAY.date())[DAY.date()]
    assert 1 / today.ist_per_fg == pytest.approx(14, rel=0.05)


def test_one_wild_price_counts_for_little():
    calm = [quote("ber", 120, DAY - timedelta(days=d)) for d in range(10)]
    calm += [quote("ist", 10, DAY - timedelta(days=d)) for d in range(10)]
    plain = fg_history(calm, VALUES, until=DAY.date())[DAY.date()].ist_per_fg
    bait = fg_history([*calm, quote("ber", 12)], VALUES, until=DAY.date())[DAY.date()]
    assert bait.ist_per_fg == pytest.approx(plain, rel=0.1)


def test_a_reset_lets_the_rate_jump():
    before = [quote("ber", 120, DAY - timedelta(days=d)) for d in range(5, 20)]
    after = [quote("ber", 40, DAY - timedelta(days=d)) for d in range(0, 3)]
    reset = (DAY - timedelta(days=3)).date()
    jumped = fg_history([*before, *after], VALUES, DAY.date(), frozenset({reset}))
    assert 1 / jumped[DAY.date()].ist_per_fg == pytest.approx(40 / 12, rel=0.15)


def test_no_readings_no_history():
    assert fg_history([], VALUES, until=DAY.date()) == {}
    assert fg_history([quote("annihilus", 9)], VALUES, until=DAY.date()) == {}


def estimate(ist_per_fg):
    return FgEstimate(ist_per_fg, ist_per_fg * 0.9, ist_per_fg * 1.1, 1)


def test_rate_on_reads_its_day_and_backcasts_before_the_first():
    history = {date(2026, 11, 1): estimate(0.1), date(2026, 11, 2): estimate(0.2)}
    assert rate_on(history, date(2026, 11, 2)).ist_per_fg == 0.2
    assert rate_on(history, date(2026, 11, 9)).ist_per_fg == 0.2
    assert rate_on(history, date(2026, 10, 1)).ist_per_fg == 0.1  # Earliest is the best guess.
    assert rate_on({}, date(2026, 11, 1)) is None


def test_valuation_reads_fg_at_the_rate_of_its_day():
    history = {date(2026, 10, 1): estimate(0.05), date(2026, 11, 1): estimate(0.1)}
    valuation = Valuation({"fg": 0.1, "ber": 12.0}, history)
    assert valuation.at(datetime(2026, 10, 15, tzinfo=UTC))["fg"] == 0.05
    assert valuation.at(datetime(2026, 11, 5, tzinfo=UTC))["fg"] == 0.1
    assert valuation.at(datetime(2026, 11, 5, tzinfo=UTC))["ber"] == 12.0
    assert valuation.current_fg.ist_per_fg == 0.1


def test_a_valuation_without_history_uses_its_rates():
    valuation = Valuation({"fg": 0.1})
    assert valuation.current_fg is None
    assert valuation.at(DAY)["fg"] == 0.1
