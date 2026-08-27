"""#980: a sketched week carries its long run and what its hard session is for.

A shape used to record a phase name, a weekly running total and two mixes. That
is enough to DRAW a week and not enough to BUILD one, which is the job a shape
actually has once the runner reaches it (#981). A live block agreed as
"13.5 -> 15.5 -> 18 -> 20 km, peak on 6 Sep" was stored as four weekly totals,
and the 20 km peak long run — the whole point of the build — was not recoverable
from anything written down.

Two properties are load-bearing here and each is pinned separately.

**A sketched week STATES its long run.** It is a coaching decision, nothing in
the app can derive it, so it is carried from the coach's own answer through
`_shape_for` into `plan.week_shapes` and back out through `build_horizon`.

**A planned week DERIVES its long run.** The sessions are already there and they
are the answer; a second stored field beside them could disagree with the
sessions underneath it. So the two halves of the horizon say one thing across the
boundary between them without ever holding two copies of the number.

The distinction between `None` and `0.0` is the third thing pinned here, because
it is the one a display gets wrong: a week with no long run has no long run, and
"0.0 km" is a different claim.

All row data is synthetic test setup (exercises code paths; represents no real
runner). The generation seam is a fake client injected at `turn.build_client`;
no test here reaches the network.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import draft as draft_mod
from app.services.schedule import store
from app.services.schedule.draft import draft_plan
from app.services.schedule.horizon import build_horizon

TODAY = date(2026, 8, 10)  # a Monday
TUE = TODAY + timedelta(days=1)
THU = TODAY + timedelta(days=3)
SAT = TODAY + timedelta(days=5)
SUN = TODAY + timedelta(days=6)
NEXT_MON = TODAY + timedelta(days=7)

FAKE_MODEL = "claude-fake-schedule-980"


# --- the fake generation seam ----------------------------------------------


class _FakeClient:
    """Stands in for the `MeteredClient` `turn.build_client` hands a coaching turn."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.model = FAKE_MODEL

    async def generate_structured(self, *, system, user, tool, max_tokens=1024):
        self.calls.append({"system": system, "user": user, "tool": tool})
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _inject(monkeypatch, client):
    monkeypatch.setattr(draft_mod.turn, "build_client", lambda kind, uid: client)
    monkeypatch.setattr(draft_mod.turn, "over_budget", lambda user_id: False)


# --- synthetic fixtures -----------------------------------------------------


def _seed_user(db) -> User:
    user = User(email=f"sketch-980-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=5,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_history(db, user: User) -> None:
    """Twelve weeks of identical running, so the load model has a rate to price
    a sketched week against and the volume ceiling has a norm to abstain-or-judge
    from."""
    for offset in range(8, 92, 2):
        activity = Activity(
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
            start_date=datetime.combine(
                TODAY - timedelta(days=offset), datetime.min.time()
            ).replace(hour=9),
            type="Run",
            name="Run",
            distance_m=8000,
            moving_time_s=2400,
            elapsed_time_s=2400,
            elev_gain_m=0.0,
            raw_summary={},
        )
        db.add(activity)
        db.commit()
        db.add(
            DerivedMetric(
                activity_id=activity.id, effort_score=30.0, confidence="high"
            )
        )
        db.commit()


def _plan(db, user: User, *, week_shapes=None, horizon_end=None) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status="active",
        rules=[],
        week_shapes=week_shapes or [],
        horizon_end=horizon_end,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _session(db, plan, *, start, intent="easy", **kw) -> PlannedSession:
    payload = {
        "plan_id": plan.id,
        "user_id": plan.user_id,
        "window_start": start,
        "window_end": kw.pop("end", start),
        "intent": intent,
        "discipline": kw.pop("discipline", "run"),
        "commitment": kw.pop("commitment", "committed"),
        "title": kw.pop("title", f"{intent} session"),
        "target_effort_score": kw.pop("target_effort_score", 40.0),
    }
    payload.update(kw)
    row = PlannedSession(**payload)
    db.add(row)
    db.commit()
    return row


def _drafted_answer(**sketch) -> dict:
    """A minimal accepted plan whose one sketched week carries whatever is given."""
    week = {
        "week_start": NEXT_MON.isoformat(),
        "sessions_by_discipline": {"run": 4},
        "intent_counts": {"easy": 3, "long": 1},
    }
    week.update(sketch)
    return {
        "rules": [],
        "weeks": [
            {
                "week_start": TODAY.isoformat(),
                "phase": "base",
                "sessions": [
                    {
                        "window_start": TUE.isoformat(),
                        "window_end": TUE.isoformat(),
                        "intent": "easy",
                        "discipline": "run",
                        "title": "Easy hour",
                        "target_duration_s": 3600,
                    }
                ],
            }
        ],
        "sketch_weeks": [week],
        "summary": "A base week and a sketch beyond it.",
    }


def _week(horizon, week_start: date):
    return next(w for w in horizon.weeks if w.week_start == week_start)


# --- the round trip: coach -> stored shape -> horizon ------------------------


@pytest.mark.asyncio
async def test_a_sketched_weeks_long_run_and_focus_reach_the_horizon_unchanged(
    db, monkeypatch
):
    """The whole point of #980, end to end.

    The coach settled a 20 km long run and a race-pace tempo for a week seven days
    out. Both are coaching decisions nothing in the app can re-derive, so if they
    do not survive the write they are gone: the week reads as a bare weekly total
    and the peak the runner agreed to is unrecoverable. This walks the real path —
    the drafting tool's answer, `_shape_for`, the JSON column, `plan_week_shapes`
    coercion, `build_horizon` — rather than any one hop of it.
    """
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    _inject(
        monkeypatch,
        _FakeClient(
            [
                _drafted_answer(
                    phase="build",
                    target_running_distance_m=37000,
                    long_run_distance_m=20000,
                    quality_focus="race-pace tempo",
                )
            ]
        ),
    )

    outcome = await draft_plan(db, user, plan, today=TODAY)

    assert outcome.ok is True
    db.refresh(plan)
    shape = next(
        s for s in store.plan_week_shapes(plan) if s.week_start == NEXT_MON
    )
    assert shape.long_run_distance_m == 20000
    assert shape.quality_focus == "race-pace tempo"

    week = _week(build_horizon(db, user, today=TODAY), NEXT_MON)
    assert week.coverage == "sketched"
    assert week.long_run_distance_m == 20000
    assert week.quality_focus == "race-pace tempo"


@pytest.mark.asyncio
async def test_a_sketch_that_states_neither_stores_neither_rather_than_a_zero(
    db, monkeypatch
):
    """A week the coach said nothing about is not a week with a 0 km long run.

    The coach is told to leave the field out for a week that genuinely holds no
    long run, so the absence has to survive as an absence: stored as `0.0` it
    would draw a "0.0 km" line for a week nobody made that claim about.
    """
    user = _seed_user(db)
    _seed_history(db, user)
    plan = store.create_drafting_plan(db, user.id)
    _inject(
        monkeypatch,
        _FakeClient([_drafted_answer(target_running_distance_m=20000)]),
    )

    assert (await draft_plan(db, user, plan, today=TODAY)).ok is True

    db.refresh(plan)
    shape = next(
        s for s in store.plan_week_shapes(plan) if s.week_start == NEXT_MON
    )
    assert shape.long_run_distance_m is None
    assert shape.quality_focus is None

    week = _week(build_horizon(db, user, today=TODAY), NEXT_MON)
    assert week.long_run_distance_m is None
    assert week.quality_focus is None


# --- a planned week derives its own long run --------------------------------


def test_a_planned_weeks_long_run_is_read_off_its_own_sessions(db):
    """Derived, never stored. The sessions are already the answer, and a second
    field beside them is a second answer that can disagree with the first."""
    user = _seed_user(db)
    plan = _plan(db, user, horizon_end=SUN)
    _session(db, plan, start=TUE, intent="easy", target_distance_m=8000)
    _session(db, plan, start=SAT, intent="long", target_distance_m=18000)

    week = _week(build_horizon(db, user, today=TODAY), TODAY)

    assert week.coverage == "planned"
    assert week.long_run_distance_m == 18000
    # And not the week's running total, which is the number it is most likely to
    # be confused with.
    assert week.running_distance_m == 26000


def test_a_week_holding_two_long_runs_reports_the_longer_one(db):
    """A back-to-back weekend is a real thing a coach writes, and the week's long
    run is the long one. Taking the first written would make the answer depend on
    insertion order, which is not a fact about the training."""
    user = _seed_user(db)
    plan = _plan(db, user, horizon_end=SUN)
    # Written longest-LAST so "the first one seen" and "the longest" differ, and
    # again in the other order below so neither is passing by luck.
    _session(db, plan, start=SAT, intent="long", target_distance_m=12000)
    _session(db, plan, start=SUN, intent="long", target_distance_m=21000)

    assert _week(build_horizon(db, user, today=TODAY), TODAY).long_run_distance_m == 21000

    other = _seed_user(db)
    other_plan = _plan(db, other, horizon_end=SUN)
    _session(db, other_plan, start=SAT, intent="long", target_distance_m=21000)
    _session(db, other_plan, start=SUN, intent="long", target_distance_m=12000)

    assert _week(build_horizon(db, other, today=TODAY), TODAY).long_run_distance_m == 21000


def test_a_planned_week_with_no_long_run_reports_none_and_never_zero(db):
    """The distinction a display gets wrong. `None` renders as nothing; `0.0`
    renders as "0.0 km", which is a claim about the week that nobody made."""
    user = _seed_user(db)
    plan = _plan(db, user, horizon_end=SUN)
    _session(db, plan, start=TUE, intent="easy", target_distance_m=8000)
    _session(db, plan, start=THU, intent="quality", target_distance_m=9000)

    week = _week(build_horizon(db, user, today=TODAY), TODAY)

    assert week.coverage == "planned"
    assert week.long_run_distance_m is None
    # Pinned on the serialised shape too, since that is what the screen and the
    # coach's tool actually read.
    assert week.model_dump()["long_run_distance_m"] is None


def test_a_long_run_the_runner_never_agreed_to_is_not_the_weeks_long_run(db):
    """A `suggested` session is an optional extra the runner can decline with no
    trace, so it does not get to set the week's backbone — the same reading that
    keeps a week of suggestions from counting as a planned week at all."""
    user = _seed_user(db)
    plan = _plan(db, user, horizon_end=SUN)
    _session(db, plan, start=TUE, intent="easy", target_distance_m=8000)
    _session(
        db,
        plan,
        start=SAT,
        intent="long",
        commitment="suggested",
        target_distance_m=18000,
    )

    week = _week(build_horizon(db, user, today=TODAY), TODAY)

    assert week.coverage == "planned"
    assert week.long_run_distance_m is None


def test_quality_focus_is_a_sketchs_field_and_is_never_claimed_for_a_written_week(db):
    """A written week states its quality work in the sessions themselves. A
    summary line on top would be prose about real sessions that nothing checks
    against them, which is the shape a hallucination takes."""
    user = _seed_user(db)
    plan = _plan(
        db,
        user,
        horizon_end=NEXT_MON + timedelta(days=6),
        week_shapes=[
            {
                "week_start": TODAY.isoformat(),
                "phase": "build",
                "quality_focus": "hill strength",
                "long_run_distance_m": 16000,
            },
            {
                "week_start": NEXT_MON.isoformat(),
                "phase": "build",
                "quality_focus": "cruise intervals",
                "long_run_distance_m": 18000,
            },
        ],
    )
    # This week holds real sessions, so it reads as planned even though a shape
    # for it also exists.
    _session(db, plan, start=SAT, intent="long", target_distance_m=15000)

    horizon = build_horizon(db, user, today=TODAY)

    written = _week(horizon, TODAY)
    assert written.coverage == "planned"
    assert written.quality_focus is None
    # The long run comes from the SESSION, not the leftover shape beside it.
    assert written.long_run_distance_m == 15000

    sketched = _week(horizon, NEXT_MON)
    assert sketched.coverage == "sketched"
    assert sketched.quality_focus == "cruise intervals"


# --- the weeks stored before either field existed ---------------------------


def test_a_shape_written_before_these_fields_existed_still_loads(db):
    """`week_shapes` is a JSON column and no migration ran, so every shape in
    production predates both fields. Coercion is strict (`extra="forbid"`) and
    drops anything off-shape silently, so a shape that failed to load would take
    the week off the runner's horizon rather than error — this is what makes the
    absence of a migration safe rather than merely untested."""
    user = _seed_user(db)
    old_shape = {
        "week_start": NEXT_MON.isoformat(),
        "phase": "build",
        "target_running_distance_m": 30000.0,
        "target_effort_score": 210.0,
        "discipline_mix": {"run": 0.8, "strength": 0.2},
        "intent_mix": {"easy": 0.6, "long": 0.4},
    }
    plan = _plan(
        db, user, week_shapes=[old_shape], horizon_end=NEXT_MON + timedelta(days=6)
    )

    shapes = store.plan_week_shapes(plan)

    assert len(shapes) == 1, "an old shape must not be dropped as off-contract"
    assert shapes[0].week_start == NEXT_MON
    assert shapes[0].target_running_distance_m == 30000.0
    assert shapes[0].long_run_distance_m is None
    assert shapes[0].quality_focus is None

    week = _week(build_horizon(db, user, today=TODAY), NEXT_MON)
    assert week.coverage == "sketched"
    assert week.running_distance_m == 30000.0
    assert week.long_run_distance_m is None
    assert week.quality_focus is None
