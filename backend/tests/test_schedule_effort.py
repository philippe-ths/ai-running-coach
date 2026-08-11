"""#830: the load model — what a planned session is worth, computed not asked for.

The North Star names `effort_score` as the hard case, so the division this module
holds is the one under test here: the coach chooses WHAT to do, the app prices it
from the runner's own history, and when the history cannot support a price the
model ABSTAINS rather than inventing one. This file pins the median (not mean)
rate, the minimum-sessions abstention, the duration -> distance -> per-session
fallback order, and the two ways a fact is excluded from the rate at all.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pytest

from app.services.schedule.effort import (
    LOOKBACK_DAYS,
    MIN_SESSIONS_FOR_RATE,
    build_load_model,
    estimate_effort,
)

TODAY = date(2026, 8, 10)


@dataclass
class _Fact:
    """The duck-typed shape `build_load_model` reads off the fact stream."""

    local_date: date
    activity_type: str = "Run"
    distance_m: float = 8000.0
    moving_time_s: int = 2400
    effort_score: Optional[float] = 30.0


def _runs(count: int, *, load: float = 30.0, moving: int = 2400, distance: float = 8000.0):
    return [
        _Fact(
            local_date=TODAY - timedelta(days=offset + 1),
            activity_type="Run",
            distance_m=distance,
            moving_time_s=moving,
            effort_score=load,
        )
        for offset in range(count)
    ]


# --- the median is the point ------------------------------------------------


def test_one_freak_session_does_not_move_the_price_of_a_normal_one():
    """A median rather than a mean: one three-hour epic (or one mis-recorded
    session) must not reprice every normal session that follows it."""
    normal = _runs(4, load=30.0, moving=2400)          # 0.0125 load/second
    freak = [
        _Fact(
            local_date=TODAY - timedelta(days=9),
            activity_type="Run",
            distance_m=32000.0,
            moving_time_s=10800,
            effort_score=200.0,                        # 0.0185 load/second
        )
    ]

    model = build_load_model(normal + freak, TODAY)

    # An hour of running is priced at the normal rate, not a rate the epic pulled up.
    assert estimate_effort(model, "run", duration_s=3600) == 45.0
    # What a mean would have produced, stated so the difference is visible.
    mean_rate = (4 * (30.0 / 2400) + (200.0 / 10800)) / 5
    assert round(mean_rate * 3600, 1) == pytest.approx(49.3, abs=0.05)
    assert model.sessions_seen["run"] == 5


# --- abstention -------------------------------------------------------------


def test_a_discipline_below_the_minimum_gets_no_rate_at_all():
    """Below the threshold a "median" is one odd session wearing a rate's clothes."""
    thin = [
        _Fact(
            local_date=TODAY - timedelta(days=offset + 1),
            activity_type="Ride",
            distance_m=30000.0,
            moving_time_s=3600,
            effort_score=60.0,
        )
        for offset in range(MIN_SESSIONS_FOR_RATE - 1)
    ]

    model = build_load_model(thin, TODAY)

    assert model.knows("bike") is False
    assert "bike" not in model.per_second
    assert "bike" not in model.per_metre
    assert "bike" not in model.per_session
    # It is still SEEN — the count is honest about the history, the rate abstains.
    assert model.sessions_seen["bike"] == MIN_SESSIONS_FOR_RATE - 1
    assert estimate_effort(model, "bike", duration_s=3600) is None


def test_one_more_session_is_what_turns_a_count_into_a_rate():
    at_threshold = build_load_model(_runs(MIN_SESSIONS_FOR_RATE), TODAY)
    below = build_load_model(_runs(MIN_SESSIONS_FOR_RATE - 1), TODAY)

    assert at_threshold.knows("run") is True
    assert below.knows("run") is False


def test_a_runner_with_no_history_is_priced_at_nothing_rather_than_at_a_guess():
    model = build_load_model([], TODAY)

    assert model.per_second == {} and model.per_metre == {} and model.per_session == {}
    assert model.sessions_seen == {}
    for discipline in ("run", "bike", "strength", "row", "walk", "other"):
        assert estimate_effort(model, discipline, duration_s=3600) is None
        assert estimate_effort(model, discipline, distance_m=10000) is None
        assert estimate_effort(model, discipline) is None


# --- the fallback order -----------------------------------------------------


def test_duration_is_preferred_to_distance_and_distance_to_a_per_session_median():
    """Load is cumulative in TIME, so a per-second rate is the honest conversion;
    distance is the fallback for a session prescribed only in kilometres."""
    facts = _runs(3, load=30.0, moving=2400, distance=8000.0)
    model = build_load_model(facts, TODAY)

    per_second = 30.0 / 2400
    per_metre = 30.0 / 8000

    # Both rates are known and they disagree for these arguments, so which one is
    # used is observable rather than a coincidence.
    by_duration = estimate_effort(model, "run", duration_s=3600, distance_m=20000)
    assert by_duration == round(per_second * 3600, 1) == 45.0
    assert by_duration != round(per_metre * 20000, 1)

    by_distance = estimate_effort(model, "run", distance_m=20000)
    assert by_distance == round(per_metre * 20000, 1) == 75.0

    by_session = estimate_effort(model, "run")
    assert by_session == 30.0


def test_a_duration_falls_through_to_distance_when_only_a_distance_rate_exists():
    """Sessions with no moving time contribute a per-metre rate and no per-second
    one, so a duration-bearing plan still gets priced instead of abstaining."""
    facts = [
        _Fact(
            local_date=TODAY - timedelta(days=offset + 1),
            activity_type="Rowing",
            distance_m=5000.0,
            moving_time_s=0,
            effort_score=25.0,
        )
        for offset in range(3)
    ]

    model = build_load_model(facts, TODAY)

    assert "row" not in model.per_second
    assert estimate_effort(model, "row", duration_s=1800, distance_m=5000) == 25.0
    # With neither, the flat per-session median is the last resort.
    assert estimate_effort(model, "row") == 25.0


def test_a_zero_or_absent_target_never_counts_as_a_size():
    facts = _runs(3, load=30.0, moving=2400, distance=8000.0)
    model = build_load_model(facts, TODAY)

    # duration_s=0 must not be read as "zero seconds of running", it is "not given".
    assert estimate_effort(model, "run", duration_s=0) == 30.0
    assert estimate_effort(model, "run", distance_m=0) == 30.0


# --- what is excluded from the rate ----------------------------------------


def test_facts_outside_the_lookback_window_do_not_price_todays_session():
    """A year-old fitness level is not what today's session costs."""
    old = [
        _Fact(
            local_date=TODAY - timedelta(days=LOOKBACK_DAYS + offset + 1),
            activity_type="Run",
            effort_score=90.0,
            moving_time_s=2400,
        )
        for offset in range(5)
    ]
    recent = _runs(3, load=30.0, moving=2400)

    model = build_load_model(old + recent, TODAY)

    assert model.sessions_seen["run"] == 3
    assert estimate_effort(model, "run", duration_s=2400) == 30.0


def test_a_fact_dated_after_today_is_not_in_the_window_either():
    future = [
        _Fact(local_date=TODAY + timedelta(days=offset + 1), effort_score=90.0)
        for offset in range(4)
    ]

    model = build_load_model(future, TODAY)

    assert model.sessions_seen == {}
    assert estimate_effort(model, "run", duration_s=2400) is None


@pytest.mark.parametrize("load", [0.0, -5.0, None])
def test_a_session_carrying_no_load_contributes_nothing_to_the_rate(load):
    """An unscored or zero-scored activity is missing data, not free training."""
    unscored = [
        _Fact(local_date=TODAY - timedelta(days=offset + 1), effort_score=load)
        for offset in range(5)
    ]
    real = _runs(3, load=30.0, moving=2400)

    model = build_load_model(unscored + real, TODAY)

    assert model.sessions_seen["run"] == 3
    assert estimate_effort(model, "run", duration_s=2400) == 30.0


def test_each_discipline_is_priced_from_its_own_history():
    """Cross-training is how load moves sideways, so a bike hour must not be
    priced at a running hour's rate."""
    runs = _runs(3, load=30.0, moving=2400)
    rides = [
        _Fact(
            local_date=TODAY - timedelta(days=offset + 10),
            activity_type="Ride",
            distance_m=30000.0,
            moving_time_s=3600,
            effort_score=45.0,
        )
        for offset in range(3)
    ]

    model = build_load_model(runs + rides, TODAY)

    assert estimate_effort(model, "run", duration_s=3600) == 45.0
    assert estimate_effort(model, "bike", duration_s=3600) == 45.0
    assert estimate_effort(model, "bike", duration_s=1800) == 22.5
    assert estimate_effort(model, "strength", duration_s=3600) is None
