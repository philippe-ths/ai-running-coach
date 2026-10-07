"""A race is recognised as a race, and a race's load spike is not scored as risk (#1032).

Before #1032 only the word "race" in the activity name or stated intent counted. The
owner never renamed or tagged their goal half marathon, so it was classified as a hard
long run, its load spike scored +3 and the day went red. The runner's own goal race on
that day, at about that distance, is now the first witness.

Activities and races are synthetic test setup (trust level 5).
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.goal_race import GoalRace
from app.services.analysis import analyze
from app.services.analysis.classifier import race_source
from app.services.analysis.risk import compute_risk_score

RACE_DAY = date(2026, 9, 27)
HALF = 21097.5


def _activity(**kw):
    base = dict(
        type="Run",
        name="Morning Run",
        user_intent=None,
        distance_m=21340,
        raw_summary={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _race(distance_m=HALF):
    return SimpleNamespace(name="Half", distance_m=distance_m, priority="A")


# --- detection ---------------------------------------------------------------


def test_a_run_on_goal_race_day_at_the_race_distance_is_that_race():
    assert race_source(_activity(), [_race()]) == "goal_race"


def test_a_course_that_ran_long_still_counts():
    assert race_source(_activity(distance_m=23500), [_race()]) == "goal_race"


def test_a_shakeout_jog_on_race_morning_is_not_the_race():
    assert race_source(_activity(distance_m=3000), [_race()]) is None


def test_a_warm_up_before_a_5k_is_not_the_race():
    assert race_source(_activity(distance_m=4300), [_race(distance_m=5000)]) is None


def test_a_word_that_merely_contains_race_is_not_a_race():
    for name in ("Embrace the hills", "Terrace loop", "Race-pace strides"):
        assert race_source(_activity(name=name), []) is None, name


def test_a_walk_on_race_day_is_not_the_race():
    assert race_source(_activity(type="Walk"), [_race()]) is None


def test_a_trail_race_counts_like_any_run():
    # Strava sends a trail run as type "Run" with sport_type "TrailRun".
    trail = _activity(raw_summary={"sport_type": "TrailRun"})
    assert race_source(trail, [_race()]) == "goal_race"


def test_without_a_goal_race_the_runners_own_tag_still_counts():
    assert race_source(_activity(user_intent="Race"), []) == "stated_intent"


def test_strava_race_marker_counts():
    assert race_source(_activity(raw_summary={"workout_type": 1}), []) == "strava_marked"


def test_a_name_saying_race_still_counts():
    assert race_source(_activity(name="Club race"), []) == "activity_name"


def test_an_untagged_unscheduled_run_is_not_a_race():
    assert race_source(_activity(), []) is None


# --- risk --------------------------------------------------------------------


def test_a_races_load_spike_scores_nothing_and_says_why():
    result = compute_risk_score(["load_spike"], None, None, is_race=True)
    assert result["risk_score"] == 0, result
    assert result["risk_level"] == "green", result
    assert result["risk_reasons"] == ["load_spike (race, expected: +0)"]


def test_a_training_runs_load_spike_still_scores():
    result = compute_risk_score(["load_spike"], None, None)
    assert result["risk_score"] == 3
    assert result["risk_reasons"] == ["load_spike (+3)"]


def test_on_a_race_other_warnings_still_score():
    """Only the expected cost of racing is exempt; pain on race day is still pain."""
    result = compute_risk_score(["load_spike", "pain_severe"], None, None, is_race=True)
    assert result["risk_score"] == 4
    assert result["risk_level"] == "red"


# --- through the pipeline ----------------------------------------------------


def _seed(db, *, with_goal_race: bool) -> Activity:
    user = User(email=f"race-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="half", experience_level="intermediate",
                       weekly_days_available=5, max_hr=190))
    if with_goal_race:
        db.add(GoalRace(user_id=user.id, name="Autumn Half", race_date=RACE_DAY,
                        distance_m=HALF, priority="A"))
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc),
        start_date_local=datetime(2026, 9, 27, 9, 0),
        type="Run",
        name="Morning Run",
        distance_m=21340,
        moving_time_s=6274,
        elapsed_time_s=6309,
        elev_gain_m=148.0,
        avg_hr=181.5,
        max_hr=190,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def test_analysis_marks_the_goal_race_as_a_race(db):
    activity = _seed(db, with_goal_race=True)
    analyze(db, activity.id)
    dm = db.query(DerivedMetric).filter_by(activity_id=activity.id).one()
    assert dm.is_race is True


def test_analysis_leaves_the_same_run_unraced_without_a_goal_race(db):
    activity = _seed(db, with_goal_race=False)
    analyze(db, activity.id)
    dm = db.query(DerivedMetric).filter_by(activity_id=activity.id).one()
    assert dm.is_race is False


def test_the_pipeline_scores_a_races_load_spike_as_expected(db):
    """End to end through analyze: a real load spike on the goal race reaches risk as
    the race exemption, not +3. A race that went red here would mean the stage wiring
    dropped `is_race` on its way to risk."""
    activity = _seed(db, with_goal_race=True)
    for days_back in range(1, 8):
        prior = Activity(
            user_id=activity.user_id,
            strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
            start_date=datetime(2026, 9, 27 - days_back, 8, 0, tzinfo=timezone.utc),
            type="Run", name="Easy", distance_m=3000, moving_time_s=1200,
            elapsed_time_s=1200, elev_gain_m=0.0, avg_hr=120.0, max_hr=130, raw_summary={},
        )
        db.add(prior)
        db.commit()
        analyze(db, prior.id)
    analyze(db, activity.id)
    dm = db.query(DerivedMetric).filter_by(activity_id=activity.id).one()
    assert "load_spike" in dm.flags, dm.flags
    assert "load_spike (race, expected: +0)" in dm.risk_reasons, dm.risk_reasons
