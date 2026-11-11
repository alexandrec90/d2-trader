"""When a price rule fires. Pure, so every case of it can be stated in a test.

A rule fires on crossing its threshold, not on being past it: otherwise a market that
sits under the line would notify every hour. Once fired it disarms, and it rearms only
after the price has moved clearly back, so a price jittering on the line stays quiet.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

METRICS = {
    "estimate": "Estimated price",
    "lowest_ask": "Lowest ask",
    "median_ask": "Median ask",
    "sold_median": "Median sold",
    "highest_bid": "Highest bid",
}
DIRECTIONS = {"at_or_below": "at or below", "at_or_above": "at or above"}
REARM_MARGIN = 0.03


@dataclass(frozen=True)
class RuleState:
    direction: str
    threshold: float
    armed: bool = True
    enabled: bool = True
    cooldown_hours: int = 12
    last_fired_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    fire: bool
    armed: bool
    reason: str


def threshold_met(direction: str, value: float, threshold: float) -> bool:
    return value <= threshold if direction == "at_or_below" else value >= threshold


def _clear_of_line(direction: str, value: float, threshold: float) -> bool:
    if direction == "at_or_below":
        return value > threshold * (1 + REARM_MARGIN)
    return value < threshold * (1 - REARM_MARGIN)


def evaluate(rule: RuleState, value: float | None, now: datetime, closed: bool) -> Decision:
    """Whether to fire now, and the armed state to store either way."""
    if not rule.enabled:
        return Decision(False, rule.armed, "disabled")
    if closed:
        # Between seasons a ladder market has only stale listings. Hold state so the rule
        # judges the new season fresh, rather than firing on what the old one left behind.
        return Decision(False, rule.armed, "market closed between seasons")
    if value is None:
        return Decision(False, rule.armed, "no live price")
    if not threshold_met(rule.direction, value, rule.threshold):
        armed = rule.armed or _clear_of_line(rule.direction, value, rule.threshold)
        return Decision(False, armed, "threshold not reached")
    if not rule.armed:
        return Decision(False, False, "already fired for this crossing")
    if rule.last_fired_at and now - rule.last_fired_at < timedelta(hours=rule.cooldown_hours):
        return Decision(False, True, "cooling down")
    return Decision(True, False, "threshold reached")
