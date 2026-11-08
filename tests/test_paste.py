import pytest

from d2_trader.sources.paste import (
    PastedPrice,
    RunePrice,
    parse_line,
    parse_paste,
    parse_rune_line,
    parse_rune_paste,
)


def test_a_rune_price_keeps_the_line_it_came_from():
    found = parse_rune_line("Ber 150fg")
    assert isinstance(found, RunePrice)
    assert found.price.line == "Ber 150fg"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("150 fg", [[("fg", 150.0)]]),
        ("sold 150fg", [[("fg", 150.0)]]),
        ("1.5k fg", [[("fg", 1500.0)]]),
        ("2 ber", [[("ber", 2.0)]]),
        ("3x ist", [[("ist", 3.0)]]),
        ("selling for ber", [[("ber", 1.0)]]),
        ("3 ist + 1 vex", [[("ist", 3.0), ("vex", 1.0)]]),
        ("200fg or ber", [[("fg", 200.0)], [("ber", 1.0)]]),
        ("2 lo / 1 sur", [[("lo", 2.0)], [("sur", 1.0)]]),
    ],
)
def test_prices_are_found(line, expected):
    assert parse_line(line).alternatives == expected


@pytest.mark.parametrize("line", ["iso anything", "bump", "20 ed 5 res", "um what about lo"])
def test_lines_without_a_price_are_not_invented(line):
    assert parse_line(line) is None


def test_a_rune_word_that_is_also_english_needs_a_quantity():
    assert parse_line("2 um").alternatives == [[("um", 2.0)]]
    assert parse_line("um") is None


def test_ethereal_shorthand_is_not_read_as_the_eth_rune():
    assert parse_line("eth thresher 2 ist").alternatives == [[("ist", 2.0)]]


def test_a_number_inside_a_word_is_not_a_quantity():
    assert parse_line("v2 ber").alternatives == [[("ber", 1.0)]]


def test_paste_separates_prices_from_noise():
    found, skipped = parse_paste("2 ber\n\n  bump  \n150 fg\n")
    assert [p.line for p in found] == ["2 ber", "150 fg"]
    assert skipped == ["bump"]


def test_empty_paste_is_empty():
    assert parse_paste("") == ([], [])


@pytest.mark.parametrize(
    ("line", "rune", "quantity", "fg"),
    [
        ("Ber 150fg", "ber", 1, 150.0),
        ("jah - 200 fg", "jah", 1, 200.0),
        ("Ist rune: 8fg", "ist", 1, 8.0),
        ("2 Lo 90fg", "lo", 2, 90.0),
        ("3x um 6 fg", "um", 3, 6.0),
        ("Zod 1.2k fg", "zod", 1, 1200.0),
        ("selling cham runes 140fg each", "cham", 1, 140.0),
    ],
)
def test_a_rune_price_line_names_its_rune_and_price(line, rune, quantity, fg):
    found = parse_rune_line(line)
    assert (found.rune, found.quantity) == (rune, quantity)
    assert found.price.alternatives == [[("fg", fg)]]


@pytest.mark.parametrize(
    "line", ["Lo / Sur 50fg", "Ber 150fg or 160fg", "Ber for a Jah", "Shako 50fg", "bump"]
)
def test_an_ambiguous_rune_line_is_skipped_not_guessed(line):
    assert parse_rune_line(line) is None


def test_a_rune_list_returns_what_it_could_not_read():
    found, skipped = parse_rune_paste("Ber 150fg\n\nwho buys?\nJah 190fg")
    assert [f.rune for f in found] == ["ber", "jah"]
    assert skipped == ["who buys?"]


def test_identity_depends_on_what_the_line_was_about():
    price = PastedPrice("2 ber", [[("ber", 2.0)]])
    assert price.external_id("enigma") != price.external_id("shako")
    assert price.external_id("enigma") == price.external_id("enigma")
