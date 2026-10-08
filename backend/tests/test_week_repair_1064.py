"""#1064: deterministic repair of a week's numeric shortfalls.

Repair exists for one case: the coach has had its one retry and the week is still
short of a NUMBER. Code then lengthens what the coach wrote or copies a session the
runner really does, says what it changed, and stops at the absurdity ceilings. It
never touches structure, a rule or a race day.

All data is synthetic test setup (exercises code paths; represents no real runner).
"""

from datetime import date, timedelta

import pytest

from app.schemas.schedule import SpacingRule
from app.schemas.season import ChallengeRule, SeasonPlan
from app.services.schedule.frames import build_frames
from app.services.schedule.plan_validator import validate_drafted_plan
from app.services.schedule.draft_contract import DraftedPlan, DraftedWeek
from app.services.schedule.repair import repair_week, repair_weeks
from app.services.schedule.week_check import (
    CHALLENGE, WALKING, check_week, planned_metrics, rule_value,
)
from tests._week_fixtures_1064 import (
    THIS_WEEK, TODAY, challenge_season, goal_row, owner_facts, session,
)

WEEK = THIS_WEEK + timedelta(weeks=1)  # Monday 12 Oct
RACE_DAY = date(2026, 11, 8)
RACE_WEEK = date(2026, 11, 2)


def d(n, week=WEEK):
    return week + timedelta(days=n)


def frame_for(week=WEEK, *, season=None, goals=None, facts=None):
    g = goal_row("10h a week")
    season = season or challenge_season(g, start=date(2026, 10, 12))
    return {
        f.week_start: f
        for f in build_frames(
            season=season, goals=goals or [g], facts=owner_facts() if facts is None else facts,
            starts_on=0, today=TODAY, horizon_weeks=12,
        )
    }[week]


def value(frame, sessions):
    return rule_value(frame.challenges[0].rule, planned_metrics(sessions, frame))


def short_week():
    """4.4 h of estimated zone 2+ time and 27 km of walking: 5.6 h short."""
    return [
        session(d(1), "run", hours=1.0, km=10),
        session(d(3), "run", hours=1.0, km=10),
        session(d(5), "run", hours=1.5, km=15, intent="long"),
        *[session(d(n), "walk", hours=0.9, km=4.5) for n in range(6)],
    ]


# --- closing a zone gap ------------------------------------------------------


def test_a_zone_gap_is_closed_by_the_activity_with_the_highest_share():
    frame = frame_for()
    frame.run_ceiling_m = None  # running is not boxed in: the share decides
    before = value(frame, short_week())
    assert before < 36_000

    result = repair_week(frame, short_week())

    assert check_week(frame, result.sessions) == []
    added = [s for s in result.sessions if s not in short_week()
             and "Added to hold the week's target" in (s.detail or "")]
    # The runner's best share is running, so the first move is to stretch the easy
    # run, then add easy runs, before any other activity is touched.
    assert added and all(s.discipline == "run" for s in added)


def test_stretching_an_easy_session_keeps_the_coachs_distance_to_time_ratio():
    frame = frame_for()
    frame.run_ceiling_m = None
    week = short_week() + [session(d(2), "run", hours=1.0, km=12, intent="easy")]
    easy_before = next(s for s in week if s.window_start == d(2) and s.discipline == "run")

    result = repair_week(frame, week)

    stretched = next(s for s in result.sessions if s.window_start == d(2) and s.title == easy_before.title)
    ratio_before = easy_before.target_distance_m / easy_before.target_duration_s
    assert stretched.target_duration_s > easy_before.target_duration_s
    assert stretched.target_distance_m / stretched.target_duration_s == pytest.approx(
        ratio_before, rel=1e-3)
    # By at most half again.
    assert stretched.target_duration_s <= easy_before.target_duration_s * 1.5 + 1


def test_only_easy_sessions_are_stretched_never_the_long_run_or_the_quality():
    frame = frame_for()
    week = short_week()
    long_before = next(s for s in week if s.intent == "long")

    result = repair_week(frame, week)

    long_after = next(s for s in result.sessions if s.intent == "long")
    assert long_after.target_duration_s == long_before.target_duration_s
    assert long_after.target_distance_m == long_before.target_distance_m


def test_added_sessions_are_copies_of_the_runners_own_typical_one():
    frame = frame_for()

    result = repair_week(frame, [session(d(n), "walk", hours=0.9, km=4.5) for n in range(6)])

    added = [s for s in result.sessions if "Added to hold" in (s.detail or "")]
    assert added
    typical = frame.typical["run"]
    first = added[0]
    assert (first.discipline, first.intent, first.commitment) == ("run", "easy", "committed")
    # The typical run is 54 min over 9.33 km; the copy keeps that pace.
    pace = first.target_duration_s / first.target_distance_m
    assert pace == pytest.approx(typical.duration_s / typical.distance_m, rel=0.02)
    for s in added:
        assert 1200 <= s.target_duration_s <= 5400


def test_it_says_what_it_changed_in_the_runners_terms():
    frame = frame_for()

    result = repair_week(frame, short_week())

    assert result.notes
    assert any(n.startswith("Added a ") and "to hold the 10 h zone 2+ week" in n
               for n in result.notes)


def test_a_week_that_already_holds_is_left_exactly_as_the_coach_wrote_it():
    frame = frame_for()
    week = short_week() + [session(d(2), "bike", hours=3.0), session(d(4), "bike", hours=3.0)]
    assert value(frame, week) >= 36_000

    result = repair_week(frame, week)

    assert result.sessions == week and result.notes == []


# --- never past a ceiling ---------------------------------------------------------


def test_repair_stops_at_the_hours_ceiling_and_leaves_the_rest_as_a_shortfall():
    frame = frame_for()
    week = short_week()
    total = sum(s.target_duration_s for s in week)
    frame.hours_ceiling_s = total + 2 * 3600  # room for only two more hours

    result = repair_week(frame, week)

    assert sum(s.target_duration_s for s in result.sessions) <= frame.hours_ceiling_s
    remaining = check_week(frame, result.sessions)
    assert [f.code for f in remaining] == [CHALLENGE]


def test_repair_stops_at_the_running_ceiling_and_turns_to_the_next_activity():
    frame = frame_for()
    frame.run_ceiling_m = 40_000  # the week already holds 35 km of running
    result = repair_week(frame, short_week())

    run_m = sum(s.target_distance_m for s in result.sessions if s.discipline == "run")
    assert run_m <= 40_000 + 1
    # Running is boxed in, so the next-best share (riding) carries the rest.
    assert any(s.discipline == "bike" for s in result.sessions)


# --- never on a race day or the day before it ----------------------------------------


def race_frame():
    race = goal_row("Chatham 10k", race_date=RACE_DAY, distance_m=10_000, booked=True)
    g = goal_row("10h a week")
    season = challenge_season(g, start=RACE_WEEK, weeks=3, race=race)
    return frame_for(RACE_WEEK, season=season, goals=[g, race])


def test_nothing_is_added_or_stretched_on_a_race_day_or_the_day_before():
    frame = race_frame()
    week = [
        session(d(1, RACE_WEEK), "run", hours=1.0, km=10),
        session(RACE_DAY - timedelta(days=1), "run", hours=0.5, km=5, title="Shakeout"),
        session(RACE_DAY, "run", hours=0.8, km=10, intent="quality", title="Chatham 10k race"),
    ]

    result = repair_week(frame, week)

    protected = {RACE_DAY, RACE_DAY - timedelta(days=1)}
    new = [s for s in result.sessions if s not in week]
    assert new
    assert all(s.window_start not in protected for s in new)
    race_after = next(s for s in result.sessions if s.title == "Chatham 10k race")
    shake_after = next(s for s in result.sessions if s.title == "Shakeout")
    assert race_after.target_duration_s == 2880 and shake_after.target_duration_s == 1800


def test_a_repair_that_cannot_find_a_legal_day_adds_nothing():
    frame = frame_for()
    rules = [SpacingRule(kind="max_sessions_per_day", label="one a day", count=1)]
    week = [session(d(n), "walk", hours=0.9, km=4.5) for n in range(7)]  # every day taken

    result = repair_week(frame, week, rules=rules)

    # Every day is taken, so no session can be ADDED; the walks the week holds may
    # still be stretched, since lengthening needs no new day.
    assert len(result.sessions) == len(week)


def test_added_sessions_keep_the_plans_spacing_rules():
    frame = frame_for()
    rules = [SpacingRule(kind="rest_day_after", label="rest after long", intent="long")]
    week = short_week()  # long run on Saturday: Sunday must stay clear of work

    result = repair_week(frame, week, rules=rules)

    new = [s for s in result.sessions if s not in week]
    assert new
    assert all(s.window_start != d(6) for s in new)


# --- the walking floor ---------------------------------------------------------------


def test_a_walking_shortfall_lengthens_the_walks_then_adds_walks_from_the_weeks_median():
    frame = frame_for()
    week = [
        *[s for s in short_week() if s.discipline == "run"],
        *[session(d(n), "bike", hours=3.0) for n in (1, 3, 5)],
        session(d(2), "walk", hours=0.9, km=4.5),
        session(d(4), "walk", hours=0.9, km=4.5),
    ]
    assert [f.code for f in check_week(frame, week)] == [WALKING]

    result = repair_week(frame, week)

    walked = planned_metrics(result.sessions, frame).distance_m["walk"]
    assert walked >= 27_000 - 10
    assert [f.code for f in check_week(frame, result.sessions)] == []
    assert any("usual walking" in n for n in result.notes)
    # The two walks were stretched together (distance with time), then more added,
    # all at the pace of the walks the week already held.
    for s in result.sessions:
        if s.discipline == "walk":
            assert s.target_distance_m / s.target_duration_s == pytest.approx(
                4500 / 3240, rel=1e-3)


# --- the other metrics ------------------------------------------------------------------


def metric_frame(metric, at_least, **kw):
    g = goal_row("Goal")
    season = SeasonPlan.model_validate({
        "summary": "s",
        "goals": [{"goal_id": str(g.id), "kind": "challenge", "success": "x", "approach": "y",
                   "challenge": {"metric": metric, "at_least": at_least, "weeks": 4,
                                 "start": "2026-10-12", **kw}}],
        "phases": [{"kind": "build", "start": "2026-10-05", "end": "2026-12-27"}],
    })
    return frame_for(season=season, goals=[g])


def test_a_distance_challenge_is_closed_with_run_or_walk_distance():
    frame = metric_frame("distance_m", 70_000)
    week = short_week()

    result = repair_week(frame, week)

    assert value(frame, result.sessions) >= 70_000 - 10


def test_a_time_challenge_is_closed_with_what_the_runner_does_most():
    frame = metric_frame("time_s", 14 * 3600)

    result = repair_week(frame, short_week())

    assert value(frame, result.sessions) >= 14 * 3600 - 60
    added = [s for s in result.sessions if "Added to hold" in (s.detail or "")]
    stretched = [s for s in result.sessions if s not in short_week() and s not in added]
    assert all(s.discipline == "walk" for s in added)


def test_a_sessions_challenge_adds_copies_of_a_typical_easy_session():
    frame = metric_frame("sessions", 12)

    result = repair_week(frame, short_week())  # 9 sessions

    assert value(frame, result.sessions) >= 12


# --- repair never touches structure --------------------------------------------------------


def test_only_the_weeks_the_check_found_numerically_short_are_repaired():
    frame = frame_for()
    other = frame_for(WEEK + timedelta(weeks=1))
    good = [session(d(1), "run", hours=1.0, km=10)]
    plan = DraftedPlan(weeks=[
        DraftedWeek(week_start=WEEK, sessions=short_week()),
        DraftedWeek(week_start=WEEK + timedelta(weeks=1), sessions=good),
    ])
    check = validate_drafted_plan(plan, today=TODAY, frames=[frame])

    weeks, notes = repair_weeks(plan.weeks, check, {WEEK: frame, other.week_start: other})

    assert weeks[1] is plan.weeks[1]  # the second week had no frame check: untouched
    assert len(weeks[0].sessions) > len(plan.weeks[0].sessions) or notes
