"""A drafted plan keeps the runner's usual walking and meets their weekly time goal,
and a draft no worker will finish stops reading as being written.

The runner walks about 30 km a week and set "10h a week, any activity, zone 2+"
for October to December. Drafts kept planning 5-15 km of walking and 4.5-6.8 h
weeks, and rewording the prompt moved walking from one walk to three and no
further, so the gate holds both. Pinned here: the two floors in the gate, the
drafting context stating them from the same functions, the draft wiring that
feeds them, and the abandoned-draft read.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models.goal_race import GoalRace
from app.services.activity_facts import query_facts
from app.services.schedule import goals, store
from app.services.schedule.draft import build_draft_context, draft_plan
from app.services.schedule.draft_contract import (
    DraftedPlan,
    DraftedSession,
    DraftedWeek,
    SketchedWeek,
)
from app.services.schedule.norms import walking_norm_weekly_m
from app.services.schedule.plan_validator import validate_drafted_plan
from tests.test_schedule_draft import (
    TODAY,
    _FakeClient,
    _good_plan,
    _inject,
    _seed_activity,
    _seed_history,
    _seed_user,
)

THU = TODAY + timedelta(days=3)
NEXT_MON = TODAY + timedelta(days=7)
WALK_NORM_M = 30_000.0  # floor 24 km a whole week


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _session(day, *, discipline="walk", km=0.0, seconds=3600):
    return DraftedSession(
        window_start=day,
        window_end=day,
        intent="easy",
        discipline=discipline,
        title=f"{discipline} {km} km",
        target_distance_m=km * 1000 or None,
        target_duration_s=seconds,
    )


def _plan_with(*sessions, start=TODAY, sketch=None):
    return DraftedPlan(
        rules=[],
        weeks=[DraftedWeek(week_start=start, sessions=list(sessions))] if sessions else [],
        sketch_weeks=[sketch] if sketch else [],
    )


# --- walking ----------------------------------------------------------------


def test_a_week_below_the_runners_usual_walking_is_rejected_with_their_numbers():
    short = validate_drafted_plan(
        _plan_with(_session(TODAY, km=15)), today=TODAY, norm_weekly_walking_m=WALK_NORM_M
    )
    kept = validate_drafted_plan(
        _plan_with(_session(TODAY, km=12), _session(THU, km=12)),
        today=TODAY,
        norm_weekly_walking_m=WALK_NORM_M,
    )

    assert not short.ok
    assert "usually walks 30 km a week, so it needs at least 24 km" in short.failures[0]
    assert kept.ok, kept.failures


def test_a_week_under_way_keeps_only_the_share_of_walking_still_ahead():
    # Thursday of a Monday week: four days left, so 24 * 4/7 = 13.7 km.
    plan = _plan_with(_session(THU, km=14))
    assert validate_drafted_plan(plan, today=THU, norm_weekly_walking_m=WALK_NORM_M).ok
    short = validate_drafted_plan(
        _plan_with(_session(THU, km=13)), today=THU, norm_weekly_walking_m=WALK_NORM_M
    )
    assert "at least 14 km for the days left in it" in short.failures[0]


def test_a_sketched_week_states_the_runners_walking_too():
    def sketch(km):
        return SketchedWeek(week_start=NEXT_MON, target_walking_distance_m=km * 1000)

    assert validate_drafted_plan(
        _plan_with(sketch=sketch(24)), today=TODAY, norm_weekly_walking_m=WALK_NORM_M
    ).ok
    short = validate_drafted_plan(
        _plan_with(sketch=sketch(5)), today=TODAY, norm_weekly_walking_m=WALK_NORM_M
    )
    assert "sketched week" in short.failures[0]


def test_walking_that_is_not_a_habit_is_not_held():
    assert validate_drafted_plan(
        _plan_with(_session(TODAY, discipline="run", km=8)),
        today=TODAY,
        norm_weekly_walking_m=4000,
    ).ok


# --- a weekly time goal -----------------------------------------------------

# 10 h a week over the four weeks from TODAY; a 10k on the Sunday of the third.
GOAL = [(TODAY, TODAY + timedelta(days=27), 36_000)]
RACE_DAY = TODAY + timedelta(days=20)


def _hours_week(start, hours):
    return _plan_with(_session(start, discipline="bike", seconds=int(hours * 3600)), start=start)


def _check(plan, **kw):
    return validate_drafted_plan(
        plan, today=TODAY, hours_goals=GOAL, race_days=[RACE_DAY], **kw
    )


def test_a_week_inside_the_goal_must_reach_most_of_it():
    assert _check(_hours_week(TODAY, 9)).ok
    short = _check(_hours_week(TODAY, 6.8))
    assert "goal is 10 h a week over these dates, so it needs at least 9.0 h" in (
        short.failures[0]
    )


def test_race_week_and_the_week_after_are_the_races_and_outside_the_goal_is_free():
    race_week = TODAY + timedelta(days=14)
    assert _check(_hours_week(race_week, 2)).ok
    assert _check(_hours_week(race_week + timedelta(days=7), 2)).ok
    assert _check(_hours_week(TODAY + timedelta(days=35), 2)).ok


def test_the_goal_never_asks_for_more_than_the_hours_ceiling_allows():
    # Typical 3 h: the ceiling is 6 h, so a 10 h goal asks for 6 h, not 9.
    assert _check(_hours_week(TODAY, 6), norm_weekly_s=3 * 3600).ok


# --- the draft --------------------------------------------------------------


def _walker(db):
    user = _seed_user(db)
    _seed_history(db, user)
    for offset in range(8, 92):  # a 4.5 km walk every day
        _seed_activity(
            db, user, day=TODAY - timedelta(days=offset), activity_type="Walk",
            distance_m=4500, moving_time_s=3000, effort_score=5.0,
        )
    db.add(GoalRace(
        user_id=user.id, name="10h a week, any activity, zone 2+", priority="C",
        window_start=TODAY, window_end=TODAY + timedelta(days=90),
        weekly_duration_s=36_000,
    ))
    db.commit()
    return user


def test_the_drafting_context_states_both_floors_and_an_amendment_is_not_told_them(db):
    user = _walker(db)
    facts = query_facts(
        db, TODAY - timedelta(days=200), TODAY + timedelta(days=1), user_id=user.id
    )
    # 83 walks of 4.5 km fall in the 84-day baseline window (TODAY-90..TODAY-7).
    assert walking_norm_weekly_m(facts, TODAY) == 83 * 4500 * 7 / 84

    context = build_draft_context(db, user, today=TODAY, weeks=12)
    amending = build_draft_context(db, user, today=TODAY, weeks=1, state_horizon=False)

    assert "10 h a week, every activity together" in context
    assert "plan it at about their usual 31 km. A week under 25 km of committed walking is rejected" in context
    assert "a week under 9.0 h of committed time" in context
    assert "The plan counts time, not heart-rate zone." in context
    assert "km of committed walking" not in amending
    assert "h of committed time, every activity together, is rejected (pro" not in amending


@pytest.mark.asyncio
async def test_a_draft_without_their_walking_is_rewritten_with_their_numbers(db, monkeypatch):
    user = _walker(db)
    walked = _good_plan()
    walked["weeks"][0]["sessions"] += [
        {
            "window_start": day.isoformat(),
            "window_end": day.isoformat(),
            "intent": "easy",
            "discipline": "walk",
            "title": "Walk",
            "target_distance_m": 4500,
            "target_duration_s": 7200,
        }
        for day in (TODAY + timedelta(days=n) for n in range(7))
    ]
    client = _FakeClient([_good_plan(), walked])
    _inject(monkeypatch, client)

    outcome = await draft_plan(
        db, user, store.create_drafting_plan(db, user.id), today=TODAY
    )

    assert outcome.ok, outcome.failures
    retry = client.calls[1]["user"]
    assert "usually walks 31 km a week, so it needs at least 25 km" in retry
    assert "goal is 10 h a week over these dates" in retry


@pytest.mark.asyncio
async def test_a_last_attempt_short_only_of_the_floors_is_written_and_says_so(db, monkeypatch):
    """The floors reject the first attempt so the rewrite gets the feedback, but
    they never cost the runner their plan: a coherent last attempt is kept, and
    the shortfall is said in the summary."""
    user = _walker(db)
    plan = store.create_drafting_plan(db, user.id)
    _inject(monkeypatch, _FakeClient([_good_plan(), _good_plan()]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures
    assert store.get_active_plan(db, user.id).id == plan.id
    assert "Walking is planned at 0 km in the week of 10 Aug, under your usual 31 km." in (
        outcome.summary
    )
    assert "The week of 10 Aug is planned at" in outcome.summary


@pytest.mark.asyncio
async def test_a_last_attempt_with_a_floor_and_a_real_failure_still_fails(db, monkeypatch):
    user = _walker(db)
    broken = _good_plan()
    broken["weeks"][0]["sessions"].append(
        {
            "window_start": THU.isoformat(), "window_end": THU.isoformat(),
            "intent": "rest", "discipline": "run", "title": "Rest",
            "target_duration_s": 1800,
        }
    )
    _inject(monkeypatch, _FakeClient([broken, broken]))

    outcome = await draft_plan(
        db, user, store.create_drafting_plan(db, user.id), today=TODAY
    )

    assert not outcome.ok
    assert any("carries a training target" in f for f in outcome.failures)
    assert store.get_active_plan(db, user.id) is None


def test_the_coach_reads_the_weekly_hours_as_the_runners_goal():
    goal = GoalRace(
        name="10h a week", priority="C", window_start=date(2026, 10, 1),
        window_end=date(2026, 12, 31), weekly_duration_s=36_000,
    )
    assert goals.for_coach(goal, TODAY)["weekly_hours"] == 10.0
    assert goals.weekly_hours_goals([goal]) == [
        (date(2026, 10, 1), date(2026, 12, 31), 36_000)
    ]


# --- an abandoned draft -----------------------------------------------------


def test_a_draft_no_worker_will_finish_reads_as_failed_not_writing(db, client):
    user = _seed_user(db)
    app.dependency_overrides[verify_clerk_session] = lambda: user
    stale = store.create_drafting_plan(db, user.id)
    stale.created_at = datetime.now(timezone.utc) - (
        store.DRAFT_STALE_AFTER + timedelta(minutes=1)
    )
    db.commit()

    body = client.get("/api/schedule/draft").json()

    assert body["status"] == "failed"
    assert body["message"].startswith("Your coach could not write a plan")


def test_a_job_picked_up_after_its_draft_was_abandoned_does_not_write_it(db):
    from app.jobs import generate_schedule as job_mod

    user = _seed_user(db)
    plan = store.create_drafting_plan(db, user.id)
    store.fail_plan(db, plan, "abandoned")

    with patch.object(job_mod, "SessionLocal", return_value=db), patch.object(
        job_mod, "draft_plan"
    ) as drafted:
        job_mod.generate_schedule_job(str(user.id), str(plan.id))

    assert drafted.call_count == 0
    assert store.latest_plan(db, user.id).status == store.FAILED
