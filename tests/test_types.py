from datetime import UTC, datetime

from d2_trader.markets import MarketKey
from d2_trader.sources.types import ParsedListing


def test_a_listing_defaults_to_one_item_of_unstated_copy():
    listing = ParsedListing(
        source="x",
        external_id="1",
        kind="ask",
        market=MarketKey(),
        alternatives=[[("ist", 1.0)]],
        observed_at=datetime(2026, 11, 6, tzinfo=UTC),
    )
    assert listing.quantity == 1
    assert listing.ethereal is None
    assert listing.attrs == {}
