"""#1044: a plan is the runner's whole week, in hours, with walks in it.

The schedule measured and bounded running km alone, so a runner whose week is
mostly walking got a plan with one optional walk in it, and a time goal across
activities ("10h a week") could be written down but not planned or checked.

Pinned here: the typical week split by activity, the hours ceiling (stated in the
drafting context, enforced by the gate, wired into both the draft and the
amendment), and the horizon and coach tool carrying the week's hours, walking km
and a long run given only as a time (#985).

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.activity_facts import query_facts
from app.services.coach import query_tools
from app.services.schedule import amend, store
from app.services.schedule.draft import build_draft_context, draft_plan
from app.services.schedule.draft_contract import (
    DraftedPlan,
    DraftedSession,
    DraftedWeek,
    SketchedWeek,
)
from app.services.schedule.horizon import build_horizon
from app.services.schedule.norms import (
    weekly_hours_norm_s,
    weekly_norms_by_discipline,
)
from app.services.schedule.plan_validator import (
    VOLUME_CEILING,
    hours_ceilings,
    validate_amendment,
    validate_drafted_plan,
)
from tests.test_schedule_draft import (
    TODAY,
    _FakeClient,
    _inject,
    _seed_activity,
    _seed_history,
    _seed_user,
)

TUE = TODAY + timedelta(days=1)
WED = TODAY + timedelta(days=2)
THU = TODAY + timedelta(days=3)
SAT = TODAY + timedelta(days=5)
NEXT_MON = TODAY + timedelta(days=7)

# `_seed_history` is 3.5 runs of 2400 s and one 3600 s gym session a week.
NORM_S = 3.5 * 2400 + 3600


def _facts(db, user):
    return query_facts(
        db, TODAY - timedelta(days=200), TODAY + timedelta(days=1), user_id=user.id
    )


def _timed(day, seconds, *, discipline="strength", intent="strength", **kw):
    return DraftedSession(
        window_start=day,
        window_end=day,
        intent=intent,
        discipline=discipline,
        title=f"{discipline} {seconds}s",
        target_duration_s=seconds,
        **kw,
    )


def _week(*sessions, start=TODAY):
    return DraftedWeek(week_start=start, sessions=list(sessions))


# --- the typical week, by activity -------------------------------------------


def test_the_typical_week_is_split_by_activity_in_time_sessions_and_km(db):
    user = _seed_user(db)
    _seed_history(db, user)
    for offset in range(10, 90, 14):  # a fortnightly walk, rarer than the gate
        _seed_activity(
            db, user, day=TODAY - timedelta(days=offset), activity_type="Walk",
            distance_m=6000, moving_time_s=4200,
        )

    norms = {n.discipline: n for n in weekly_norms_by_discipline(_facts(db, user), TODAY)}

    days = 84  # TODAY-90 .. TODAY-7, clamped to the first activity
    assert norms["run"].moving_time_s == 42 * 2400 * 7 / days
    assert norms["run"].distance_m == 42 * 8000 * 7 / days
    assert norms["strength"].sessions == 12 * 7 / days
    # Six walks in the window is under the per-window minimum on its own, and it
    # still shows: the history gate is over the whole window, not each activity.
    assert norms["walk"].sessions == 6 * 7 / days
    assert norms["walk"].distance_m == 6 * 6000 * 7 / days
    assert list(norms)[0] == "run"  # largest time first


def test_a_runner_with_too_little_history_has_no_typical_week():
    assert weekly_norms_by_discipline([], TODAY) == []
    assert weekly_hours_norm_s([], TODAY) is None


def test_the_drafting_context_states_the_hours_limit_the_gate_enforces(db):
    user = _seed_user(db)
    _seed_history(db, user)
    # Three minutes a week of rowing is noise, not a habit, and is not listed.
    for offset in range(10, 90, 7):
        _seed_activity(
            db, user, day=TODAY - timedelta(days=offset), activity_type="Rowing",
            distance_m=0, moving_time_s=180,
        )

    context = build_draft_context(db, user, today=TODAY, weeks=12)

    assert "Typical week, by activity (3.4 h moving in all):" in context
    assert "  - run: 2.3 h over 3.5 sessions, 28.0 km" in context
    # Strength states no km: it is not measured in km.
    assert "  - strength: 1.0 h over 1.0 sessions\n" in context
    assert "  - row:" not in context
    # 3.4 h typical: the gate rejects above 6.77 h and 10.15 h. Stated ROUNDED
    # DOWN, so a week the coach writes under the stated limit always passes.
    norm = weekly_hours_norm_s(_facts(db, user), TODAY)
    assert hours_ceilings(norm)[0] / 3600 > 6.7
    assert (
        "above 6.7 h of committed time, every activity together, is rejected "
        "outright; a sketched week may reach 10.1 h"
    ) in context
    at_stated = DraftedPlan(
        rules=[], weeks=[_week(_timed(TUE, int(6.7 * 3600)))], sketch_weeks=[]
    )
    assert validate_drafted_plan(at_stated, today=TODAY, norm_weekly_s=norm).ok


# --- the hours ceiling -------------------------------------------------------


def test_a_week_over_twice_the_runners_usual_time_is_rejected():
    plan = DraftedPlan(rules=[], weeks=[_week(_timed(TUE, 3600 * 7))], sketch_weeks=[])

    over = validate_drafted_plan(plan, today=TODAY, norm_weekly_s=3 * 3600)
    at = validate_drafted_plan(plan, today=TODAY, norm_weekly_s=3.5 * 3600)

    assert not over.ok and over.codes == [VOLUME_CEILING]
    assert "7.0 h of training, every activity together" in over.failures[0]
    assert at.ok, at.failures  # exactly twice is a bold week, not an absurd one


def test_every_activity_counts_and_a_suggestion_does_not():
    week = _week(
        _timed(TUE, 3600, discipline="walk", intent="easy", target_distance_m=5000),
        _timed(WED, 3600, discipline="bike", intent="easy"),
        _timed(THU, 3600, discipline="run", intent="easy", target_distance_m=10000),
        _timed(SAT, 3600 * 5, discipline="walk", intent="easy", commitment="suggested",
               target_distance_m=20000),
    )
    plan = DraftedPlan(rules=[], weeks=[week], sketch_weeks=[])

    assert not validate_drafted_plan(plan, today=TODAY, norm_weekly_s=1.4 * 3600).ok
    assert validate_drafted_plan(plan, today=TODAY, norm_weekly_s=1.5 * 3600).ok


def test_the_race_is_not_training_time():
    race = _timed(SAT, 3600 * 4, discipline="run", intent="long", target_distance_m=42195)
    plan = DraftedPlan(
        rules=[], weeks=[_week(_timed(TUE, 3600), race)], sketch_weeks=[]
    )

    with_race = validate_drafted_plan(
        plan, today=TODAY, norm_weekly_s=3600, races=[(SAT, 42195)]
    )
    without = validate_drafted_plan(plan, today=TODAY, norm_weekly_s=3600)

    assert with_race.ok, with_race.failures
    assert not without.ok


def test_a_shakeout_on_race_day_is_still_training():
    race = _timed(SAT, 3600 * 4, discipline="run", intent="long", target_distance_m=42195)
    shakeout = _timed(SAT, 3600, discipline="run", intent="easy", target_distance_m=3000)
    plan = DraftedPlan(rules=[], weeks=[_week(_timed(TUE, 3600), race, shakeout)],
                       sketch_weeks=[])

    check = validate_drafted_plan(
        plan, today=TODAY, norm_weekly_s=1800, races=[(SAT, 42195)]
    )

    assert not check.ok  # 2 h of training against a 1 h ceiling


def test_a_sketched_week_may_reach_three_times_but_not_beyond():
    def plan(hours):
        return DraftedPlan(
            rules=[],
            weeks=[],
            sketch_weeks=[SketchedWeek(week_start=NEXT_MON, target_duration_s=hours * 3600)],
        )

    assert validate_drafted_plan(plan(9), today=TODAY, norm_weekly_s=3 * 3600).ok
    over = validate_drafted_plan(plan(9.5), today=TODAY, norm_weekly_s=3 * 3600)
    assert not over.ok and over.codes == [VOLUME_CEILING]
    assert "sketched week" in over.failures[0]


def test_no_typical_week_means_no_hours_ceiling():
    plan = DraftedPlan(rules=[], weeks=[_week(_timed(TUE, 3600 * 20))], sketch_weeks=[])
    assert validate_drafted_plan(plan, today=TODAY, norm_weekly_s=None).ok


def test_an_amendment_is_judged_on_the_whole_week_kept_sessions_included(db):
    user = _seed_user(db)
    kept = PlannedSession(
        plan_id=None, user_id=user.id, window_start=TODAY, window_end=TODAY,
        intent="easy", discipline="walk", commitment="committed", title="Kept walk",
        target_duration_s=3600 * 3,
    )
    week = _week(_timed(TUE, 3600 * 2))

    alone = validate_amendment(
        [week], rules=[], surviving_by_week={}, today=TODAY, norm_weekly_s=3 * 3600
    )
    whole = validate_amendment(
        [week], rules=[], surviving_by_week={TODAY: [kept]}, today=TODAY,
        norm_weekly_s=2 * 3600,
    )

    assert alone.ok, alone.failures
    assert not whole.ok and VOLUME_CEILING in whole.codes


# --- wired into both writers -------------------------------------------------


def _too_many_hours_week(start) -> dict:
    """Three 2.5 h gym sessions: no running at all, so only the hours gate can
    see it, and 7.5 h is past twice `_seed_history`'s 3.3 h week."""
    return {
        "week_start": start.isoformat(),
        "sessions": [
            {
                "window_start": (start + timedelta(days=d)).isoformat(),
                "window_end": (start + timedelta(days=d)).isoformat(),
                "intent": "strength",
                "discipline": "strength",
                "title": "Long gym session",
                "target_duration_s": 9000,
            }
            for d in (1, 3, 5)
        ],
    }


@pytest.mark.asyncio
async def test_the_draft_rejects_a_week_of_too_many_hours(db, monkeypatch):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    week = {"rules": [], "weeks": [_too_many_hours_week(TODAY)], "sketch_weeks": []}
    _inject(monkeypatch, _FakeClient([week, week]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is False
    assert outcome.failure_kind == store.FAILURE_TOO_BIG_A_JUMP


@pytest.mark.asyncio
async def test_a_sketched_weeks_hours_and_walking_are_stored_with_it(db, monkeypatch):
    """A sketch is what later sessions are written from (#981), so the hours and
    walking it was agreed on have to survive being stored."""
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    drafted = {
        "rules": [],
        "weeks": [],
        "sketch_weeks": [
            {
                "week_start": NEXT_MON.isoformat(),
                "target_duration_s": 18000,
                "target_walking_distance_m": 12000,
                "sessions_by_discipline": {"walk": 3},
            }
        ],
    }
    _inject(monkeypatch, _FakeClient([drafted]))

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok, outcome.failures
    (shape,) = store.plan_week_shapes(db.get(TrainingPlan, outcome.plan_id))
    assert shape.target_duration_s == 18000
    assert shape.target_walking_distance_m == 12000


def test_an_amendment_rejects_a_week_of_too_many_hours(db):
    user = _seed_user(db)
    _seed_history(db, user)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()

    class _Client:
        model = "fake"

        async def generate_structured(
            self, *, system, user, tool, max_tokens=1024, timeout=None
        ):
            return {"weeks": [_too_many_hours_week(NEXT_MON)]}

    with patch.object(amend.turn, "build_client", return_value=_Client()), \
         patch.object(amend.turn, "over_budget", return_value=False):
        proposal = asyncio.run(
            amend.propose_amendment(
                db, user, plan, weeks_from=1, weeks_through=1,
                instruction="more gym", today=TODAY,
            )
        )

    assert proposal.ok is False
    assert proposal.failure_kind == store.FAILURE_TOO_BIG_A_JUMP


# --- what the runner and the coach read --------------------------------------


def test_the_horizon_and_the_coach_read_hours_walking_and_a_timed_long_run(db):
    user = _seed_user(db)
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=[
            {
                "week_start": NEXT_MON.isoformat(),
                "phase": "Build",
                "target_running_distance_m": 30000,
                "target_duration_s": 36000,
                "target_walking_distance_m": 25000,
            }
        ],
    )
    db.add(plan)
    db.commit()

    def add(day, discipline, intent, *, commitment="committed", **kw):
        db.add(
            PlannedSession(
                plan_id=plan.id, user_id=user.id, window_start=day, window_end=day,
                intent=intent, discipline=discipline, commitment=commitment,
                title=f"{discipline} {intent}", **kw,
            )
        )

    add(TUE, "walk", "easy", target_distance_m=5000, target_duration_s=3600)
    add(WED, "bike", "easy", target_distance_m=20000, target_duration_s=2700)
    add(SAT, "run", "long", target_duration_s=5400)  # "90 minutes easy" (#985)
    add(THU, "walk", "easy", commitment="suggested", target_distance_m=9000,
        target_duration_s=7200)
    db.commit()

    weeks = build_horizon(db, user, weeks=2, today=TODAY).weeks
    planned, sketched = weeks[0], weeks[1]

    assert planned.duration_s == 3600 + 2700 + 5400
    assert planned.walking_distance_m == 5000
    assert planned.long_run_distance_m is None
    assert planned.long_run_duration_s == 5400
    assert sketched.duration_s == 36000
    assert sketched.walking_distance_m == 25000

    tool_weeks = query_tools.get_training_plan(db, user.id, today=TODAY)["weeks"]
    assert tool_weeks[0]["hours_all_activities"] == 3.2
    assert tool_weeks[0]["walking_km"] == 5.0
    assert tool_weeks[0]["long_run_minutes"] == 90
    assert "long_run_km" not in tool_weeks[0]
    assert tool_weeks[1]["hours_all_activities"] == 10.0


def test_a_week_with_an_untimed_session_states_no_hours_rather_than_a_fraction(db):
    """A plan written before sessions carried both: runs by km, gym by time.
    Headlining the 45 minutes of gym as the week's hours would undercount it
    roughly fivefold, so the week falls back to its running km."""
    user = _seed_user(db)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    for day, kw in (
        (TUE, {"discipline": "run", "intent": "easy", "target_distance_m": 10000}),
        (THU, {"discipline": "strength", "intent": "strength", "target_duration_s": 2700}),
    ):
        db.add(PlannedSession(plan_id=plan.id, user_id=user.id, window_start=day,
                              window_end=day, commitment="committed", title="s", **kw))
    db.commit()

    week = build_horizon(db, user, weeks=1, today=TODAY).weeks[0]

    assert week.duration_s is None
    assert week.running_distance_m == 10000
    assert "hours_all_activities" not in (
        query_tools.get_training_plan(db, user.id, today=TODAY)["weeks"][0]
    )


def test_the_coachs_week_view_states_both_distance_and_time():
    from types import SimpleNamespace

    from app.services.schedule.coach_view import _target

    walk = SimpleNamespace(structure=None, target_distance_m=6000, target_duration_s=4200)
    assert _target(walk) == "6.0 km, 70 min"


def test_a_committed_rest_day_does_not_blank_the_weeks_hours(db):
    """A rest day has no time to state, and the contract asks for 0 there."""
    user = _seed_user(db)
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    for day, kw in (
        (TUE, {"discipline": "run", "intent": "easy", "target_duration_s": 3600}),
        (THU, {"discipline": "other", "intent": "rest", "target_duration_s": 0}),
    ):
        db.add(PlannedSession(plan_id=plan.id, user_id=user.id, window_start=day,
                              window_end=day, commitment="committed", title="s", **kw))
    db.commit()

    assert build_horizon(db, user, weeks=1, today=TODAY).weeks[0].duration_s == 3600
