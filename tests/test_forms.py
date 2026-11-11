import pytest
from pydantic import ValidationError

from d2_trader.markets import MarketKey
from d2_trader.web.forms import AddItemForm, ImportForm, MarketForm, RuleForm, parse_ethereal

MARKET = {"platform": "PC", "mode": "hardcore", "ladder": "no", "game_version": "lod"}


def test_ethereal_choice_maps_to_the_filter():
    assert parse_ethereal("yes") is True
    assert parse_ethereal("no") is False
    assert parse_ethereal("any") is None


def test_a_market_form_describes_a_market():
    form = MarketForm(**MARKET)
    assert form.market == MarketKey("PC", "hardcore", False, "lod")
    assert form.ethereal_filter is None


def test_an_item_form_keeps_the_market_and_filter():
    form = AddItemForm(item="Shako", ethereal="yes", **MARKET)
    assert form.item == "Shako"
    assert form.ethereal_filter is True


def test_import_defaults_to_asking_prices_for_one_item():
    form = ImportForm(item="x", text="2 ber", **MARKET)
    assert (form.kind, form.paste_as) == ("ask", "item")


def test_a_rune_list_needs_no_item_and_keeps_its_market():
    form = ImportForm(text="Ber 150fg", paste_as="runes", **MARKET)
    assert form.market.mode == "hardcore"


def test_a_market_outside_the_lists_is_refused():
    with pytest.raises(ValidationError):
        MarketForm(**{**MARKET, "mode": "softcoreish"})


def test_a_rule_needs_a_positive_threshold():
    fields = {
        "metric": "lowest_ask",
        "direction": "at_or_below",
        "threshold_currency": "ber",
    }
    assert RuleForm(threshold_amount=1, **fields).cooldown_hours == 12
    with pytest.raises(ValidationError):
        RuleForm(threshold_amount=0, **fields)
