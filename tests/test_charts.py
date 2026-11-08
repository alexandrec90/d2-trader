import json
from datetime import UTC, date, datetime, timedelta

import pytest

from d2_trader.currency import NO_VALUE
from d2_trader.forum_gold import FgEstimate
from d2_trader.trend import PathPoint
from d2_trader.seasons import Season
from d2_trader.stats import DayPoint
from d2_trader.web.charts import (
    Chart,
    Scale,
    fg_per_ist,
    nice_ceiling,
    price_chart,
    rate_chart,
    split_runs,
)

RATES = {"ist": 1.0, "ber": 10.0}
NOW = datetime(2026, 11, 20, tzinfo=UTC)


def day(offset, sold=None, ask=None):
    when = date(2026, 11, 1) + timedelta(days=offset)
    return DayPoint(
        when,
        sold,
        1 if sold else 0,
        ask * 0.8 if ask else None,
        ask,
        ask * 1.2 if ask else None,
    )


def chart(days, seasons=()):
    return price_chart(days, list(seasons), RATES, now=NOW, title="Test")


def test_a_chart_is_svg_plus_its_hover_data():
    assert isinstance(chart([day(0, sold=1), day(1, sold=2)]), Chart)


def test_a_single_day_is_not_a_chart():
    assert chart([day(0, sold=10)]) is None
    assert chart([]) is None


def test_sales_are_dots_and_asks_a_line():
    svg = chart([day(0, sold=10, ask=12), day(1, sold=11, ask=13)]).svg
    assert svg.count('class="dot sold"') == 2
    assert 'class="line ask"' in svg
    assert 'class="line sold"' not in svg  # The line through sales is the model's.


def test_a_missing_series_is_simply_absent():
    assert 'class="line ask"' not in chart([day(0, sold=10), day(1, sold=11)]).svg


def test_a_long_silence_breaks_the_ask_line():
    days = [day(0, ask=10), day(1, ask=11), day(10, ask=12), day(11, ask=13)]
    assert len(split_runs(days)) == 2
    assert chart(days).svg.count('class="line ask"') == 2


def test_a_short_gap_stays_connected():
    assert len(split_runs([day(0, sold=1), day(3, sold=1)])) == 1


def test_a_season_start_is_marked():
    start = datetime(2026, 11, 14, tzinfo=UTC)
    svg = chart([day(0, sold=10), day(18, sold=11)], [Season(16, start)]).svg
    assert 'class="reset"' in svg
    assert ">S16<" in svg


def test_the_gap_between_seasons_is_shaded():
    s15 = Season(15, datetime(2026, 8, 22, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    s16 = Season(16, datetime(2026, 11, 14, tzinfo=UTC))
    assert 'class="gap"' in chart([day(0, sold=10), day(18, sold=11)], [s15, s16]).svg


def test_a_season_with_no_successor_yet_shades_up_to_today():
    ended = Season(15, datetime(2026, 8, 22, tzinfo=UTC), datetime(2026, 11, 9, tzinfo=UTC))
    assert 'class="gap"' in chart([day(0, sold=10), day(18, sold=11)], [ended]).svg


def test_seasons_outside_the_range_draw_nothing():
    old = Season(1, datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 2, 1, tzinfo=UTC))
    current = Season(2, datetime(2020, 3, 1, tzinfo=UTC))
    svg = chart([day(0, sold=10), day(1, sold=11)], [old, current]).svg
    assert 'class="reset"' not in svg
    assert 'class="gap"' not in svg


def test_the_title_is_escaped():
    svg = price_chart(
        [day(0, sold=1), day(1, sold=2)], [], RATES, now=NOW, title='<script>"x"</script>'
    ).svg
    assert "<script>" not in svg


def test_tooltip_data_is_valid_json_in_display_units():
    data = json.loads(str(chart([day(0, sold=15), day(1, ask=20)]).data))
    assert data[0]["lines"][0] == "Median sold: 1.5 Ber (1)"
    assert data[1]["lines"][0] == f"Median sold: {NO_VALUE} (0)"
    assert data[1]["lines"][1] == "Median ask: 2 Ber"


def test_nice_ceiling_rounds_up_to_a_clean_number():
    assert nice_ceiling(87) == 100
    assert nice_ceiling(21) == 25
    assert nice_ceiling(1.3) == 2
    assert nice_ceiling(0.3) == 0.4
    assert nice_ceiling(0) == 1


def test_the_axis_does_not_waste_a_third_of_the_plot():
    # Regression: data peaking at 62 got an axis of 100.
    assert nice_ceiling(62) == 80


def test_the_tooltip_range_uses_one_unit():
    data = json.loads(str(chart([day(0, ask=8), day(1, ask=20)]).data))
    assert data[1]["lines"][2] == "Asks: 1.6 to 2.4 Ber"


def estimate_of(ist_per_fg, low=None, high=None, observations=2):
    return FgEstimate(ist_per_fg, low or ist_per_fg * 0.8, high or ist_per_fg * 1.25, observations)


def test_the_fg_rate_is_shown_the_way_traders_say_it():
    assert fg_per_ist(estimate_of(0.1, 0.08, 0.125)) == pytest.approx((10, 8, 12.5))


def test_the_fg_chart_draws_the_rate_its_interval_and_days_with_prices():
    history = {
        date(2026, 11, 1): estimate_of(0.1, 0.08, 0.125),
        date(2026, 11, 2): estimate_of(0.09, 0.08, 0.1, observations=0),
    }
    drawn = rate_chart(history, [], now=NOW, title="fg")
    assert 'class="band"' in drawn.svg
    assert 'class="line ask"' in drawn.svg
    assert drawn.svg.count('class="dot sold"') == 1  # Only the day that had prices.
    rows = json.loads(str(drawn.data))
    assert rows[0]["lines"] == ["1 Ist = 10 fg", "95% CI 8 to 12.5 fg", "2 rune prices that day"]
    assert rows[1]["lines"][2] == "no prices that day: estimated"


def test_a_runaway_interval_does_not_squash_the_fg_chart():
    history = {
        date(2026, 11, 1): estimate_of(0.1),
        date(2026, 11, 2): estimate_of(0.1, low=0.001),  # A long gap's interval: 1000 fg.
    }
    drawn = rate_chart(history, [], now=NOW, title="fg")
    assert ">20<" in drawn.svg  # Axis scaled to the rate, not to the interval's top.


def test_the_estimate_is_drawn_as_a_line_and_band_through_quiet_days():
    path = [
        PathPoint(date(2026, 11, 1), 10, 8, 12, 2),
        PathPoint(date(2026, 11, 2), 11, 8, 14, 0),
        PathPoint(date(2026, 11, 3), 12, 10, 14, 1),
    ]
    drawn = price_chart(
        [day(0, sold=10), day(2, sold=12)], [], RATES, now=NOW, title="x", path=path
    )
    assert 'class="band sold"' in drawn.svg
    assert 'class="line sold"' in drawn.svg
    rows = json.loads(str(drawn.data))
    assert rows[0]["lines"][0] == "Estimate: 1 Ber (95%: 0.8 to 1.2 Ber)"


def test_one_day_of_fg_is_not_a_chart():
    assert rate_chart({date(2026, 11, 1): estimate_of(0.1)}, [], now=NOW, title="fg") is None


def test_scale_maps_values_inside_the_plot():
    scale = Scale(date(2026, 11, 1), date(2026, 11, 11), 100)
    assert scale.y(100) < scale.y(0)
    assert scale.x(date(2026, 11, 1)) < scale.x(date(2026, 11, 11))


def test_scale_survives_a_single_day_range():
    scale = Scale(date(2026, 11, 1), date(2026, 11, 1), 10)
    assert scale.days == 1
