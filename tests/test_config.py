from d2_trader.config import Settings, get_settings


def test_traderie_stays_off_unless_someone_decides_otherwise():
    # Its terms forbid automated access, so this default is a decision, not a preference.
    assert Settings(_env_file=None, database_url="x").traderie_enabled is False


def test_user_agent_is_this_app_and_not_a_browser():
    agent = Settings(_env_file=None, database_url="x").user_agent
    assert agent.startswith("d2-trader/")
    assert "Mozilla" not in agent


def test_diablo2io_pace_matches_what_the_site_asks_for():
    assert Settings(_env_file=None, database_url="x").diablo2io_min_interval_s >= 60


def test_requests_are_spread_out_by_default():
    assert Settings(_env_file=None, database_url="x").pace_jitter > 0


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
