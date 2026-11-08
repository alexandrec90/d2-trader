from d2_trader.markets import MarketKey, d2jsp_forum_url


def test_default_market_is_pc_softcore_ladder_rotw():
    assert MarketKey().label == "PC · Softcore · Ladder · RotW"


def test_label_says_non_ladder_and_hardcore():
    assert MarketKey("PC", "hardcore", False, "lod").label == "PC · Hardcore · Non-Ladder · LoD"


def test_market_keys_compare_by_value():
    assert MarketKey() == MarketKey("PC", "softcore", True, "rotw")
    assert MarketKey() != MarketKey(ladder=False)


def test_forum_link_matches_the_market():
    assert d2jsp_forum_url(MarketKey()).endswith("f=271")
    assert d2jsp_forum_url(MarketKey(mode="hardcore", ladder=False)).endswith("f=229")


def test_consoles_have_no_forum_link():
    assert d2jsp_forum_url(MarketKey(platform="xbox")) is None


def test_an_unmapped_market_has_no_forum_link():
    assert d2jsp_forum_url(MarketKey(mode="hardcore", game_version="classic")) is None
