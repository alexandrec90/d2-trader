from datetime import UTC, datetime, timedelta

from d2_trader.alerts import Decision, RuleState, evaluate, threshold_met

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def rule(**overrides):
    return RuleState(**{"direction": "at_or_below", "threshold": 100.0, **overrides})


def test_threshold_is_inclusive_in_both_directions():
    assert threshold_met("at_or_below", 100, 100)
    assert threshold_met("at_or_above", 100, 100)
    assert not threshold_met("at_or_below", 101, 100)
    assert not threshold_met("at_or_above", 99, 100)


def test_crossing_fires_and_disarms():
    assert evaluate(rule(), 90, NOW, closed=False) == Decision(True, False, "threshold reached")


def test_staying_past_the_line_does_not_fire_again():
    assert evaluate(rule(armed=False), 90, NOW, closed=False).fire is False


def test_a_price_jittering_on_the_line_does_not_rearm():
    assert evaluate(rule(armed=False), 101, NOW, closed=False).armed is False


def test_moving_clearly_back_rearms():
    assert evaluate(rule(armed=False), 110, NOW, closed=False).armed is True


def test_rearm_margin_works_upward_too():
    upward = rule(direction="at_or_above", armed=False)
    assert evaluate(upward, 99, NOW, closed=False).armed is False
    assert evaluate(upward, 90, NOW, closed=False).armed is True


def test_full_cycle_fires_twice_across_two_crossings():
    state = rule()
    fired = 0
    for hour, value in enumerate([120, 90, 92, 91, 130, 95]):
        now = NOW + timedelta(days=hour)
        decision = evaluate(state, value, now, closed=False)
        fired += decision.fire
        state = RuleState(
            direction=state.direction,
            threshold=state.threshold,
            armed=decision.armed,
            last_fired_at=now if decision.fire else state.last_fired_at,
        )
    assert fired == 2


def test_cooldown_holds_a_second_crossing_without_losing_it():
    recent = rule(last_fired_at=NOW - timedelta(hours=1))
    decision = evaluate(recent, 90, NOW, closed=False)
    assert decision.fire is False
    assert decision.armed is True
    assert decision.reason == "cooling down"


def test_after_the_cooldown_it_fires():
    earlier = rule(last_fired_at=NOW - timedelta(hours=13))
    assert evaluate(earlier, 90, NOW, closed=False).fire is True


def test_no_price_changes_nothing():
    decision = evaluate(rule(), None, NOW, closed=False)
    assert (decision.fire, decision.armed) == (False, True)


def test_a_paused_rule_stays_quiet():
    assert evaluate(rule(enabled=False), 1, NOW, closed=False).fire is False


def test_a_closed_ladder_market_never_fires_even_at_a_great_price():
    decision = evaluate(rule(), 1, NOW, closed=True)
    assert decision.fire is False
    assert "between seasons" in decision.reason


def test_a_closed_market_leaves_a_disarmed_rule_disarmed():
    assert evaluate(rule(armed=False), 500, NOW, closed=True).armed is False
