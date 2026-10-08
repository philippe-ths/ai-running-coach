"""#1064: the challenge arithmetic and the measured weekly totals under it."""

from datetime import date, timedelta

import pytest

from app.schemas.season import ChallengeRule
from app.services.schedule import challenge
from app.services.schedule.norms import (
    MIN_ZONE_MEASURED_S,
    estimated_zone_s,
    weekly_actuals,
    zone_shares,
)
from tests._season_fixtures_1064 import MONDAY, THIS_WEEK, TODAY, fact, steady_weeks

Z2_RULE = dict(metric="zone_time_s", min_zone=2, weeks=10, start=date(2026, 12, 14))


def rule(**over):
    return ChallengeRule(**{**Z2_RULE, "at_least": 36000, **over})


# --- measured weekly totals --------------------------------------------------


def test_weekly_actuals_sum_by_discipline_and_measure_zone_time_from_heart_rate():
    facts = [
        fact(THIS_WEEK, kind="Run", seconds=3600, distance_m=10000,
             zones={"Z1": 600, "Z2": 2400, "Z3": 600}),
        fact(THIS_WEEK + timedelta(days=2), kind="Walk", seconds=1800, distance_m=3000,
             zones={"Z1": 1800}),
        fact(THIS_WEEK + timedelta(days=7), kind="Run", seconds=999),  # next week
    ]
    got = weekly_actuals(facts, THIS_WEEK + timedelta(days=3), MONDAY)  # snaps to the Monday
    assert got.week_start == THIS_WEEK
    assert got.time_s == {"run": 3600, "walk": 1800}
    assert got.distance_m["run"] == 10000
    assert got.sessions == 2
    assert got.zone_time_s(2) == 3000
    assert got.zone_time_s(2, ["walk"]) == 0
    assert got.zone_time_s(1) == 5400


def test_an_activity_without_heart_rate_contributes_no_zone_time_never_an_estimate():
    got = weekly_actuals([fact(THIS_WEEK, seconds=7200, zones=None)], THIS_WEEK, MONDAY)
    assert got.total_time_s == 7200
    assert got.zone_time_s(1) == 0


def test_zone_shares_use_measured_time_and_drop_thin_disciplines():
    as_of = TODAY
    facts = steady_weeks(6, hours=2.0, z2_share=0.25)
    shares = zone_shares(facts, as_of, 2)
    assert shares["run"] == pytest.approx(0.25)
    thin = [fact(THIS_WEEK - timedelta(days=10), kind="Walk", seconds=600,
                 zones={"Z2": MIN_ZONE_MEASURED_S - 1})]
    assert "walk" not in zone_shares(facts + thin, as_of, 2)
    assert estimated_zone_s({"run": 3600, "walk": 3600}, {"run": 0.5}) == 1800


# --- current level -----------------------------------------------------------


def test_current_level_is_the_higher_of_the_recent_and_the_long_mean():
    spec = challenge.MetricSpec("zone_time_s", (), 2)
    # 12 weeks of 2 h at zone 2, then the last 4 at 6 h: recent mean beats the 12-week mean.
    facts = steady_weeks(12, hours=2.0, z2_share=1.0)
    facts += [
        fact(THIS_WEEK - timedelta(weeks=n) + timedelta(days=3), seconds=4 * 3600,
             zones={"Z2": 4 * 3600})
        for n in range(1, 5)
    ]
    recent = (2 + 4) * 3600
    long = (8 * 2 + 4 * 6) / 12 * 3600
    assert recent > long
    assert challenge.current_level(spec, facts, TODAY, MONDAY) == pytest.approx(recent)
    # Reverse it: a recent lull leaves the longer mean standing.
    lull = steady_weeks(12, hours=6.0, z2_share=1.0)
    lull = [f for f in lull if f.local_date < THIS_WEEK - timedelta(weeks=4)]
    lull += steady_weeks(4, hours=0.5, z2_share=1.0)
    level = challenge.current_level(spec, lull, TODAY, MONDAY)
    assert level == pytest.approx((8 * 6 + 4 * 0.5) / 12 * 3600)


def test_current_level_does_not_count_the_week_in_progress_or_pre_history():
    spec = challenge.MetricSpec("time_s")
    facts = steady_weeks(2, hours=4.0) + [fact(THIS_WEEK + timedelta(days=1), seconds=99 * 3600)]
    # Two weeks of history: the 12-week window is clamped to them, not diluted by 10 empty weeks.
    assert challenge.current_level(spec, facts, TODAY, MONDAY) == pytest.approx(4 * 3600)


def test_current_level_is_zero_with_no_facts():
    assert challenge.current_level(rule(), [], TODAY, MONDAY) == 0


def test_the_discipline_filter_narrows_what_counts():
    facts = steady_weeks(6, hours=3.0) + [
        fact(THIS_WEEK - timedelta(weeks=1) + timedelta(days=2), kind="Walk", seconds=3600)
    ]
    run = challenge.MetricSpec("time_s", ("run",))
    both = challenge.MetricSpec("time_s")
    assert challenge.current_level(both, facts, TODAY, MONDAY) > challenge.current_level(run, facts, TODAY, MONDAY)


# --- earliest start ----------------------------------------------------------


def test_already_there_starts_this_week():
    r = rule(at_least=18000)
    assert challenge.earliest_start(r, 18000, TODAY, MONDAY) == THIS_WEEK
    assert challenge.earliest_start(r, 25000, TODAY, MONDAY) == THIS_WEEK


def test_a_ten_percent_ramp_sets_the_first_week_it_reaches_the_level():
    # 5.1 h -> 10 h needs ceil(ln(10/5.1)/ln(1.1)) = 8 rises, the first landing next week.
    start = challenge.earliest_start(rule(at_least=36000), 5.1 * 3600, TODAY, MONDAY)
    assert start == THIS_WEEK + timedelta(weeks=8)
    assert start.weekday() == MONDAY
    # One rise short of it would land a week earlier, so the boundary is exact.
    assert 5.1 * 3600 * 1.1 ** 7 < 36000 <= 5.1 * 3600 * 1.1 ** 8


def test_a_runner_at_zero_ramps_from_a_stated_floor():
    spec = challenge.MetricSpec("time_s")
    weeks = challenge.weeks_to_reach(spec, 36000, 0)
    assert weeks == challenge.weeks_to_reach(spec, 36000, challenge.RAMP_FLOOR["time_s"])
    assert weeks > 0


def test_sessions_ramp_one_a_week():
    spec = challenge.MetricSpec("sessions")
    assert challenge.weeks_to_reach(spec, 6, 3) == 3
    r = ChallengeRule(metric="sessions", at_least=6, weeks=4, start=date(2026, 12, 14))
    assert challenge.earliest_start(r, 3, TODAY, MONDAY) == THIS_WEEK + timedelta(weeks=3)


def test_the_week_boundary_follows_the_runner():
    sunday = challenge.earliest_start(rule(at_least=18000), 18000, TODAY, 6)
    assert sunday == date(2026, 10, 4) and sunday.weekday() == 6


# --- status and streak -------------------------------------------------------


def _week_fact(week, hours, z=2):
    return fact(week + timedelta(days=1), seconds=int(hours * 3600), zones={f"Z{z}": hours * 3600})


def test_status_shows_actuals_only_for_weeks_that_have_begun_and_met_only_when_ended():
    r = rule(start=THIS_WEEK - timedelta(weeks=3), weeks=6, at_least=36000)
    facts = [
        _week_fact(r.start, 10), _week_fact(r.start + timedelta(weeks=1), 10),
        _week_fact(r.start + timedelta(weeks=2), 9),     # missed
        _week_fact(THIS_WEEK, 4),                          # in progress
    ]
    weeks, streak = challenge.challenge_status(r, facts, TODAY, MONDAY, planned_by_week={
        THIS_WEEK + timedelta(weeks=1): 37000.0})
    assert [w.index for w in weeks] == [1, 2, 3, 4, 5, 6]
    assert [w.met for w in weeks] == [True, True, False, None, None, None]
    assert streak == 2
    assert weeks[3].actual == 4 * 3600  # so far this week, not yet a verdict
    assert weeks[4].actual is None and weeks[4].planned == 37000.0
    assert all(w.threshold == 36000 for w in weeks)


def test_a_missed_week_breaks_the_streak_for_good():
    r = rule(start=THIS_WEEK - timedelta(weeks=4), weeks=4, at_least=36000)
    facts = [_week_fact(r.start, 10), _week_fact(r.start + timedelta(weeks=1), 1),
             _week_fact(r.start + timedelta(weeks=2), 10), _week_fact(r.start + timedelta(weeks=3), 10)]
    weeks, streak = challenge.challenge_status(r, facts, TODAY, MONDAY)
    assert [w.met for w in weeks] == [True, False, True, True]
    assert streak == 1


def test_a_challenge_that_has_not_started_has_no_actuals_and_no_streak():
    weeks, streak = challenge.challenge_status(rule(), [_week_fact(THIS_WEEK, 10)], TODAY, MONDAY)
    assert streak == 0
    assert all(w.actual is None and w.met is None for w in weeks)


def test_a_smart_recorded_walk_is_measured_on_its_moving_time_not_its_samples():
    # A watch on smart recording logs a walk about every 4 s, so 2000 s of walking
    # stores 500 heart-rate samples. Counting samples as seconds would credit the
    # walk with a quarter of its zone time.
    walk = fact(THIS_WEEK, kind="Walk", seconds=2000, distance_m=3000,
                zones={"Z1": 250, "Z2": 250, "Z3": 0, "Z4": 0, "Z5": 0})
    week = weekly_actuals([walk], THIS_WEEK, MONDAY)
    assert week.zone_time_s(2) == pytest.approx(1000)
    assert week.zone_time_s(1) == pytest.approx(2000)
