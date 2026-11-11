from datetime import UTC, date, datetime

from d2_trader.stats import (
    PriceRange,
    Quote,
    Summary,
    daily_history,
    drop_outliers,
    price_range,
    summarize,
)
from d2_trader.trend import PathPoint

AT = datetime(2026, 10, 1, tzinfo=UTC)


def quotes(kind, values):
    return [Quote(kind, v, AT) for v in values]


def test_bait_price_is_dropped():
    assert 0.01 not in drop_outliers([10, 11, 12, 10, 11, 0.01])


def test_absurd_high_price_is_dropped():
    assert 900 not in drop_outliers([10, 11, 12, 10, 11, 900])


def test_ordinary_spread_survives():
    values = [10, 12, 14, 16, 18]
    assert drop_outliers(values) == values


def test_a_tight_market_does_not_turn_a_normal_sale_into_an_outlier():
    assert 16 in drop_outliers([10, 10, 10, 10, 10, 16])


def test_too_few_values_to_judge_are_all_kept():
    assert drop_outliers([1, 100, 10000]) == [1, 100, 10000]


def test_range_is_median_and_quartiles():
    assert price_range([10, 20, 30, 40, 50]) == PriceRange(20, 30, 40, 5)


def test_range_reports_what_it_threw_out():
    assert price_range([10, 11, 12, 10, 11, 0.01]).outliers == 1


def test_range_of_one_value_is_that_value():
    result = price_range([7])
    assert (result.low, result.median, result.high, result.samples) == (7, 7, 7, 1)


def test_range_of_nothing_is_none():
    assert price_range([]) is None


def test_sales_outrank_asks():
    summary = summarize(quotes("sold", [10] * 6) + quotes("ask", [20] * 6))
    assert summary.basis == "sold"
    assert summary.fair.median == 10
    assert summary.confidence == "medium"


def test_many_sales_are_high_confidence():
    assert summarize(quotes("sold", [10] * 10)).confidence == "high"


def test_asks_alone_are_low_confidence():
    summary = summarize(quotes("ask", [20] * 6))
    assert (summary.basis, summary.confidence) == ("asking", "low")


def test_a_handful_of_quotes_is_shown_but_not_trusted():
    summary = summarize(quotes("sold", [10, 11]))
    assert (summary.basis, summary.confidence) == ("thin", "none")
    assert summary.fair is not None


def test_empty_market_has_no_prices():
    summary = summarize([])
    assert isinstance(summary, Summary)
    assert summary.fair is None
    assert summary.lowest_ask is None
    assert summary.sold_median is None


def test_lowest_ask_ignores_bait():
    summary = summarize(quotes("ask", [10, 11, 12, 10, 11, 0.01]))
    assert summary.lowest_ask == 10


def test_highest_bid_ignores_joke_offers():
    assert summarize(quotes("bid", [10, 11, 12, 10, 11, 900])).highest_bid == 12


def test_free_listings_do_not_count_as_prices():
    assert summarize(quotes("ask", [0, 0, 5])).lowest_ask == 5


def test_the_estimate_is_carried_and_read_as_a_metric():
    estimate = PathPoint(AT.date(), 12.0, 10.0, 14.0, 3)
    summary = summarize(quotes("sold", [10] * 5), estimate)
    assert summary.estimate is estimate
    assert summary.metric("estimate") == 12.0
    assert summarize([]).metric("estimate") is None


def test_metric_reads_each_alert_input():
    summary = summarize(quotes("sold", [10] * 5) + quotes("ask", [20] * 5) + quotes("bid", [5] * 5))
    assert summary.metric("sold_median") == 10
    assert summary.metric("lowest_ask") == 20
    assert summary.metric("median_ask") == 20
    assert summary.metric("highest_bid") == 5


def test_history_is_one_point_per_day_in_order():
    later = datetime(2026, 10, 3, tzinfo=UTC)
    points = daily_history([Quote("ask", 20, later), Quote("sold", 10, AT), Quote("sold", 12, AT)])
    assert [p.day for p in points] == [date(2026, 10, 1), date(2026, 10, 3)]
    assert points[0].sold_median == 11
    assert points[0].sold_count == 2
    assert points[0].ask_median is None
    assert points[1].ask_low == points[1].ask_high == 20


def test_history_of_nothing_is_empty():
    assert daily_history([]) == []
