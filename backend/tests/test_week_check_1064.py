"""#1064: one check of a week against every goal, collecting every failure.

The check asks the same few questions of any week: does it reach the challenge's
threshold, keep the runner's usual walking, hold the day of each dated goal. Each
question has a test that fires and a week that passes, and the headline property is
that ONE call returns EVERY failure, since there is one retry.

All data is synthetic test setup (exercises code paths; represents no real runner).
"""

from datetime import date, timedelta

import pytest

from app.schemas.season import ChallengeRule
from app.services.schedule.frames import build_frames
from app.services.schedule.week_check import (
    CHALLENGE, DATED_GOAL, WALKING, check_week, planned_metrics, rule_value,
)
from tests._week_fixtures_1064 import (
    THIS_WEEK, TODAY, challenge_season, fact, goal_row, owner_facts, session,
)

WEEK_2 = THIS_WEEK + timedelta(weeks=1)  # Monday 12 Oct: a full future week
RACE_DAY = date(2026, 11, 8)
RACE_WEEK = date(2026, 11, 2)


def day(week, n):
    return week + timedelta(days=n)


def frames(season=None, goals=(), facts=None):
    return {
        f.week_start: f
        for f in build_frames(
            season=season, goals=list(goals),
            facts=owner_facts() if facts is None else facts,
            starts_on=0, today=TODAY, horizon_weeks=12,
        )
    }


def challenge_frame(hours=10.0, weeks=10):
    g = goal_row("10h a week")
    return frames(challenge_season(g, hours=hours, weeks=weeks), [g])[WEEK_2]


def good_week(week=WEEK_2):
    """10.9 h of estimated zone 2+ time and 30 km of walking."""
    return [
        *[session(day(week, n), "run", hours=1.5) for n in (0, 2, 4, 5)],  # 6 h
        session(day(week, 1), "bike", hours=2.0),
        session(day(week, 3), "bike", hours=2.0),
        *[session(day(week, n), "walk", hours=0.9, km=5.0) for n in range(6)],  # 30 km, the usual
    ]


# --- planned metrics ----------------------------------------------------------


def test_planned_metrics_count_only_committed_work_still_ahead():
    frame = challenge_frame()
    sessions = [
        session(day(WEEK_2, 0), "run", hours=1.0, km=10),
        session(day(WEEK_2, 1), "bike", hours=1.0, commitment="suggested"),
        session(day(WEEK_2, 2), "walk", seconds=0, intent="rest"),
        session(day(WEEK_2, 3), "walk", hours=1.0, km=5),
    ]

    metrics = planned_metrics(sessions, frame)

    assert metrics.time_s == {"run": 3600, "walk": 3600}
    assert metrics.distance_m == {"run": 10_000, "walk": 5_000}
    assert metrics.sessions_by_discipline == {"run": 1, "walk": 1}


def test_a_session_whose_window_has_closed_is_history_not_plan():
    frames_ = frames()
    current = frames_[THIS_WEEK]
    yesterday = session(TODAY - timedelta(days=1), "run", hours=1.0)
    today = session(TODAY, "run", hours=1.0)

    assert planned_metrics([yesterday], current).time_s == {}
    assert planned_metrics([today], current).time_s == {"run": 3600}


def test_estimated_zone_time_is_duration_times_the_runners_own_share():
    frame = challenge_frame()
    sessions = [session(day(WEEK_2, 0), "run", hours=2.0),
                session(day(WEEK_2, 1), "walk", hours=1.0),
                session(day(WEEK_2, 2), "row", hours=1.0)]  # no measured share: counts none

    zone = planned_metrics(sessions, frame).zone_est_s[2]

    assert zone["run"] == pytest.approx(2 * 3600 * 0.98)
    assert zone["walk"] == pytest.approx(3600 * 0.27)
    assert zone["row"] == 0.0


def test_a_rep_session_counts_the_distance_its_parts_add_up_to():
    frame = challenge_frame()
    reps = session(day(WEEK_2, 3), "run", hours=1.0, intent="quality")
    reps = reps.model_copy(update={"reps_planned": 6, "rep_distance_m": 800,
                                   "warmup_distance_m": 2000, "cooldown_distance_m": 1500})

    assert planned_metrics([reps], frame).distance_m["run"] == 2000 + 4800 + 1500


# --- rule values, per metric --------------------------------------------------


def rule(metric, at_least, *, disciplines=(), min_zone=None):
    return ChallengeRule(metric=metric, at_least=at_least, weeks=4, start=WEEK_2,
                         disciplines=list(disciplines), min_zone=min_zone)


def test_every_metric_reads_the_week_the_way_its_unit_says():
    frame = challenge_frame()
    metrics = planned_metrics(
        [session(day(WEEK_2, 0), "run", hours=2.0, km=20),
         session(day(WEEK_2, 1), "bike", hours=1.0, km=25),
         session(day(WEEK_2, 2), "walk", hours=1.0, km=5)], frame)

    assert rule_value(rule("zone_time_s", 1, min_zone=2), metrics) == pytest.approx(
        3600 * (2 * 0.98 + 0.89 + 0.27))
    assert rule_value(rule("zone_time_s", 1, min_zone=2, disciplines=["bike"]),
                      metrics) == pytest.approx(3600 * 0.89)
    assert rule_value(rule("time_s", 1), metrics) == 4 * 3600
    assert rule_value(rule("time_s", 1, disciplines=["run", "walk"]), metrics) == 3 * 3600
    assert rule_value(rule("distance_m", 1, disciplines=["run"]), metrics) == 20_000
    assert rule_value(rule("distance_m", 1), metrics) == 50_000
    assert rule_value(rule("sessions", 1), metrics) == 3


def test_what_was_done_earlier_this_week_counts_as_measured_not_estimated():
    facts = owner_facts() + [
        fact(THIS_WEEK, kind="Run", seconds=7200, distance_m=20_000,
             zones={"Z1": 0, "Z2": 7200, "Z3": 0, "Z4": 0, "Z5": 0}),
        fact(THIS_WEEK + timedelta(days=1), kind="Run", seconds=3600, distance_m=9_000),
    ]
    g = goal_row("10h a week")
    current = frames(challenge_season(g), [g], facts=facts)[THIS_WEEK]
    metrics = planned_metrics([session(TODAY, "bike", hours=1.0)], current)

    value = rule_value(rule("zone_time_s", 1, min_zone=2), metrics)

    # 2 h measured in zone 2+ (the run with no heart-rate data adds none) plus the
    # planned hour of riding at 89 %.
    assert value == pytest.approx(7200 + 3600 * 0.89)
    assert rule_value(rule("time_s", 1), metrics) == 7200 + 3600 + 3600


# --- the checks that fire -------------------------------------------------------


def test_a_week_that_holds_everything_passes():
    assert check_week(challenge_frame(), good_week()) == []


def test_a_week_short_of_the_challenge_fails_with_the_exact_gap_and_what_to_add():
    frame = challenge_frame()
    # One ride fewer than the good week: 6 h of running (5.88 h at zone 2+), one 2 h
    # ride (1.78 h) and 5.4 h of walking (1.46 h) is 9.12 h of an estimated 10.
    week = [s for s in good_week() if not (s.discipline == "bike" and s.window_start == day(WEEK_2, 3))]

    (failure,) = check_week(frame, week)

    assert failure.code == CHALLENGE
    assert failure.week_start == WEEK_2
    assert failure.gap == pytest.approx(36_000 - 3600 * (5.88 + 1.78 + 1.458))
    text = failure.message
    assert 'the challenge "10h a week" (week 1 of 10) needs 10.0 h' in text
    assert "this week holds 9.1 h" in text and "estimate" in text
    assert "Add 0.9 h" in text
    # The activity that closes it fastest for THIS runner is named with its share.
    assert "98% of this runner's run" in text
    assert failure.shortfall.startswith("The week of 12 Oct plans 9.1 h")


def challenge_frame_rule(frame):
    return frame.challenges[0].rule


def test_a_week_within_a_minute_of_the_threshold_meets_it():
    frame = challenge_frame(hours=10.0)
    week = good_week()
    metrics = planned_metrics(week, frame)
    exact = rule_value(challenge_frame_rule(frame), metrics)
    near = challenge_frame(hours=(exact + 30) / 3600)

    assert check_week(near, week) == []
    farther = challenge_frame(hours=(exact + 120) / 3600)
    assert [f.code for f in check_week(farther, week)] == [CHALLENGE]


def test_the_walking_floor_fires_with_the_runners_own_numbers():
    frame = challenge_frame()
    week = [s for s in good_week() if s.discipline != "walk"]
    week += [session(day(WEEK_2, 1), "walk", hours=2.0, km=10)]
    week += [session(day(WEEK_2, 6), "bike", hours=3.0)]  # keep the challenge met

    failures = check_week(frame, week)

    walking = [f for f in failures if f.code == WALKING]
    assert len(walking) == 1
    assert walking[0].gap == pytest.approx(20_000)
    assert "usually walks 30 km a week" in walking[0].message
    assert "at least 30.0 km" in walking[0].message
    assert "holds 10.0 km" in walking[0].message


def test_this_weeks_walking_floor_is_the_share_of_the_week_still_ahead():
    current = frames()[THIS_WEEK]
    needed = 30_000 * 4 / 7  # Thursday to Sunday

    short = check_week(current, [session(TODAY, "walk", hours=1.0, km=needed / 1000 - 1)])
    enough = check_week(current, [session(TODAY, "walk", hours=2.0, km=needed / 1000 + 0.1)])

    assert [f.code for f in short] == [WALKING]
    assert "for the days left in it" in short[0].message
    assert enough == []


def goal_frame(kind="race", distance_m=10_000):
    race = goal_row("Chatham 10k", race_date=RACE_DAY, distance_m=distance_m, booked=True)
    g = goal_row("10h a week")
    season = challenge_season(g, start=RACE_WEEK, weeks=3, race=race)
    if kind != "race":
        season = season.model_copy(deep=True)
        season.goals[1].kind = kind
    return frames(season, [g, race])[RACE_WEEK]


def race_week(extra=()):
    return [
        *[session(day(RACE_WEEK, n), "run", hours=2.0) for n in (0, 2)],
        session(day(RACE_WEEK, 1), "bike", hours=2.0),
        session(day(RACE_WEEK, 3), "bike", hours=2.0),
        session(day(RACE_WEEK, 4), "bike", hours=2.0),
        *[session(day(RACE_WEEK, n), "walk", hours=0.9, km=5.0) for n in range(6)],
        *extra,
    ]


def race_session(**kw):
    kw.setdefault("km", 10)
    return session(RACE_DAY, "run", hours=0.8, intent="quality", title="Chatham 10k race", **kw)


def test_a_week_holding_its_race_on_the_day_passes():
    assert check_week(goal_frame(), race_week([race_session()])) == []


@pytest.mark.parametrize(
    "bad",
    [
        None,  # nothing on the day
        dict(intent="easy"),  # a race is quality or long
        dict(title="Hard 10k"),  # the title says race
        dict(km=8),  # under 90 % of the distance
        dict(commitment="suggested"),  # a suggestion is not a commitment
        dict(discipline="bike"),  # run or walk only
    ],
)
def test_a_race_week_without_the_race_as_the_goal_asks_for_it_fails(bad):
    sessions = race_week()
    if bad is not None:
        kw = dict(bad)
        discipline = kw.pop("discipline", "run")
        title = kw.pop("title", "Chatham 10k race")
        intent = kw.pop("intent", "quality")
        km = kw.pop("km", 10)
        sessions.append(session(RACE_DAY, discipline, hours=0.8, km=km, intent=intent,
                                title=title, **kw))

    failures = check_week(goal_frame(), sessions)

    assert [f.code for f in failures] == [DATED_GOAL]
    assert '"Chatham 10k" is on 8 Nov' in failures[0].message
    assert "pinned to 8 Nov" in failures[0].message


def test_a_race_not_pinned_to_its_day_does_not_hold_it():
    floating = session(RACE_DAY - timedelta(days=1), "run", hours=0.8, km=10,
                       intent="quality", title="Chatham 10k race", end=RACE_DAY)

    assert [f.code for f in check_week(goal_frame(), race_week([floating]))] == [DATED_GOAL]


def test_a_finish_or_completion_goal_needs_a_run_or_walk_not_the_word_race():
    plain = session(RACE_DAY, "walk", hours=9.0, km=9.5, title="The loop")

    assert check_week(goal_frame("completion"), race_week([plain])) == []
    assert [f.code for f in check_week(goal_frame("race"), race_week([plain]))] == [DATED_GOAL]


def test_one_call_returns_every_failure_of_every_kind():
    frame = goal_frame()
    # Nothing but two short runs: short of the challenge, of the walking and of the
    # race, three different questions, one check.
    failures = check_week(frame, [session(day(RACE_WEEK, 1), "run", hours=1.0, km=10),
                                  session(day(RACE_WEEK, 3), "run", hours=1.0, km=10)])

    assert sorted(f.code for f in failures) == sorted([CHALLENGE, WALKING, DATED_GOAL])


def test_a_race_that_is_already_run_is_not_asked_for_again():
    race = goal_row("Chatham 10k", race_date=THIS_WEEK + timedelta(days=1), distance_m=10_000)
    g = goal_row("10h a week")
    season = challenge_season(g, race=race, start=THIS_WEEK)
    current = frames(season, [g, race])[THIS_WEEK]

    codes = [f.code for f in check_week(current, [session(TODAY, "run", hours=1.0)])]

    assert DATED_GOAL not in codes
