"""#1064: the checks a season passes before it is stored.

Each test breaks ONE thing in an otherwise valid season and asserts the check that
owns it fires, naming what the coach can act on. The valid season passing is the
control: without it, every "fires" below could be a check that fires on anything.
"""

import copy
from datetime import date

import pytest

from app.core.config import settings
from app.schemas.season import SeasonPlan
from app.services.schedule.season_check import check_season
from tests._season_fixtures_1064 import (
    MONDAY, TODAY, level, standard_goals, valid_payload,
)

LEVEL = 18000.0  # 5 h a week of zone 2+


@pytest.fixture
def g():
    return standard_goals()


def run(g, mutate=None, *, levels=level(LEVEL), goals=None):
    payload = copy.deepcopy(valid_payload(g))
    if mutate:
        mutate(payload)
    plan = SeasonPlan.model_validate(payload)
    return check_season(plan, goals if goals is not None else list(g.values()),
                        TODAY, MONDAY, levels)


def having(failures, *needles):
    return [f for f in failures if all(n in f for n in needles)]


def test_the_valid_season_passes(g):
    assert run(g) == []


# --- goals answered once -----------------------------------------------------


def test_a_missing_goal_is_named(g):
    failures = run(g, lambda p: p["goals"].pop(3))
    assert having(failures, "Backyard ultra", "no view")


def test_a_duplicate_view_is_named(g):
    failures = run(g, lambda p: p["goals"].append(copy.deepcopy(p["goals"][3])))
    assert having(failures, "Backyard ultra", "2 times")


def test_an_unknown_goal_id_is_named(g):
    def mutate(p):
        p["goals"][3]["goal_id"] = "00000000-0000-0000-0000-000000000001"
    failures = run(g, mutate)
    assert having(failures, "not one of this runner's goals")
    assert having(failures, "Backyard ultra", "no view")  # and the real one is still missing


# --- dates -------------------------------------------------------------------


def test_a_booked_date_that_moved_fails_and_says_where_it_belongs(g):
    failures = run(g, lambda p: p["goals"][0].update(date="2026-11-15"))
    assert having(failures, "Chatham 10k", "booked", "2026-11-08")


def test_a_booked_goal_given_only_a_window_fails(g):
    def mutate(p):
        p["goals"][0].pop("date")
        p["goals"][0].update(window_start="2026-11-01", window_end="2026-11-10")
    assert having(run(g, mutate), "Chatham 10k", "booked", "no exact date")


def test_an_unbooked_goal_may_move_its_date(g):
    g["race"].booked = False
    # The date moved but nothing about it is a booked-date failure.
    failures = run(g, lambda p: p["goals"][0].update(date="2026-11-15"))
    assert not having(failures, "booked")


@pytest.mark.parametrize("kind", ["race", "finish", "completion"])
def test_a_dated_kind_with_no_date_or_window_fails(g, kind):
    def mutate(p):
        view = p["goals"][2]
        view.update(kind=kind)
        view.pop("window_start"), view.pop("window_end")
    assert having(run(g, mutate), "First marathon", kind, "no date or window")


def test_a_date_before_the_current_week_fails(g):
    g["race"].booked = False
    failures = run(g, lambda p: p["goals"][0].update(date="2026-10-02"))
    assert having(failures, "Chatham 10k", "before the current week")


def test_a_window_ending_before_the_current_week_fails(g):
    def mutate(p):
        p["goals"][2].update(window_start="2026-08-01", window_end="2026-09-01")
    assert having(run(g, mutate), "First marathon", "window ending", "before the current week")


def test_an_event_in_the_past_fails(g):
    def mutate(p):
        p["goals"][2]["events"][0]["date"] = "2026-09-01"
    assert having(run(g, mutate), "Some Marathon", "passed")


def test_an_event_in_the_future_passes(g):
    def mutate(p):
        p["goals"][2]["events"][0]["date"] = "2027-05-16"
    assert run(g, mutate) == []


# --- the challenge -----------------------------------------------------------


def test_a_challenge_must_start_on_the_week_boundary(g):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-12-15"
    failures = run(g, mutate)
    assert having(failures, "10h a week", "not a week boundary", "Monday")
    assert having(failures, "14 December 2026")  # the nearest boundary is offered


def test_a_sunday_runner_has_sunday_boundaries(g):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-12-13"
    plan = SeasonPlan.model_validate(_with(g, mutate))
    failures = check_season(plan, list(g.values()), TODAY, 6, level(LEVEL))
    assert not having(failures, "not a week boundary")
    assert having(check_season(plan, list(g.values()), TODAY, MONDAY, level(LEVEL)),
                  "not a week boundary")


def _with(g, mutate):
    payload = copy.deepcopy(valid_payload(g))
    mutate(payload)
    return payload


def test_a_challenge_may_not_start_before_the_current_week(g):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-09-28"
    assert having(run(g, mutate), "10h a week", "before the current week")


def test_a_far_challenge_is_the_coachs_call_however_far_above_the_current_level(g):
    # The fixture starts the challenge beyond the concrete weeks: the build
    # between now and then is the coach's, held week by week by the plan's own
    # ceilings, so no level ceiling applies here.
    assert run(g, levels=level(1000.0)) == []


def test_a_near_challenge_is_held_to_two_times_the_current_level(g):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-10-19"  # two weeks out: concrete
    msg = having(run(g, mutate, levels=level(15000.0)), "at most 2x", "currently does")
    assert msg and having(msg, "week of")  # 36000 > 2 x 15000, and points at a later start
    assert not having(run(g, mutate, levels=level(18000.0)), "at most")  # 36000 == 2 x 18000


def test_the_near_window_follows_the_concrete_week_setting(g, monkeypatch):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-11-02"  # four weeks out
    assert not having(run(g, mutate, levels=level(15000.0)), "at most 2x")
    monkeypatch.setattr(settings, "SCHEDULE_CONCRETE_WEEKS", 6)
    assert having(run(g, mutate, levels=level(15000.0)), "at most 2x")


def test_a_runner_doing_none_of_it_is_measured_from_the_ramp_floor(g):
    def mutate(p):
        p["goals"][1]["challenge"]["start"] = "2026-10-19"  # near, so the ceiling applies
    failures = run(g, mutate, levels=level(0.0))
    assert having(failures, "10h a week", "currently does 0.0 h")
    assert not having(failures, "division")


# --- the timeline ------------------------------------------------------------


def test_no_phases_fails(g):
    assert having(run(g, lambda p: p.update(phases=[])), "no phases")


def test_an_unsorted_timeline_fails(g):
    failures = run(g, lambda p: p["phases"].reverse())
    assert having(failures, "not in date order")


def test_overlapping_phases_fail(g):
    failures = run(g, lambda p: p["phases"][0].update(end="2026-11-03"))
    assert having(failures, "overlaps")


def test_a_gap_between_phases_fails_and_states_the_day_it_should_start(g):
    failures = run(g, lambda p: p["phases"][1].update(start="2026-11-04"))
    assert having(failures, "gap", "2026-11-02")


def test_the_first_phase_must_cover_today(g):
    failures = run(g, lambda p: p["phases"][0].update(start="2026-10-12"))
    assert having(failures, "first phase", "cover")


def test_the_timeline_must_reach_the_latest_dated_goal(g):
    failures = run(g, lambda p: p["phases"][-1].update(end="2027-03-01"))
    assert having(failures, "First marathon", "2027-06-30", "Extend")


def test_a_dated_race_needs_a_race_phase_carrying_its_goal(g):
    def mutate(p):
        p["phases"][2]["kind"] = "taper"
    assert having(run(g, mutate), "Chatham 10k", "no race phase")


def test_a_race_phase_for_another_goal_does_not_count(g):
    def mutate(p):
        p["phases"][2]["goal_id"] = str(g["marathon"].id)
    assert having(run(g, mutate), "Chatham 10k", "no race phase")


def test_a_race_phase_that_misses_the_day_does_not_count(g):
    def mutate(p):
        p["phases"][2].update(start="2026-11-09", end="2026-11-09")
        p["phases"][1]["end"] = "2026-11-08"
        p["phases"][3]["start"] = "2026-11-10"
    assert having(run(g, mutate), "Chatham 10k", "no race phase")


def test_a_phase_naming_a_stranger_goal_fails(g):
    def mutate(p):
        p["phases"][0]["goal_id"] = "00000000-0000-0000-0000-000000000009"
    assert having(run(g, mutate), "names goal id")


def test_every_failure_is_reported_not_just_the_first(g):
    def mutate(p):
        p["goals"][0]["date"] = "2026-11-15"          # booked date moved
        p["goals"].pop(3)                              # a goal unanswered
        p["phases"][1]["start"] = "2026-11-05"         # a gap
        p["goals"][1]["challenge"]["start"] = "2026-10-19"  # and an absurd near challenge
    failures = run(g, mutate, levels=level(1000.0))
    for needle in ("booked", "no view", "gap", "at most 2x"):
        assert having(failures, needle), needle
