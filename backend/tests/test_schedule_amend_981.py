"""#981: amending part of a plan, and keeping the rest.

The plan had one verb: REPLACE. Between "redraft the whole block" and "change one
number on one session" there was nothing, so every real request a runner makes of
a living schedule had to be served by throwing the plan away. That is how a
runner came to confirm a second draft ninety seconds after agreeing the first and
lose the block they had just settled.

This file pins the promise the amendment makes, because the promise is the whole
reason it is a separate verb from a redraft. Four things carry it:

**The window is the boundary.** Sessions outside it are not read, not written and
not touched. Asserted on IDENTITY (same row ids, same values), not on counts: a
delete-and-rewrite that happened to produce the same shape would satisfy a count
and would have silently discarded the runner's agreed sessions.

**A completion is a record, not a plan.** What the runner did survives every
amendment, including which activity it was matched to and how.

**The plan keeps its identity.** Same row, same rules, same race, same
`generated_at`. An amendment that quietly redrafts is the one thing this must
never be.

**The gate sees the whole week.** `validate_amendment` judges the union of what
survives and what is new, because a week amended from Wednesday still contains
Monday's completed long run and the plan's spacing rules span that join. Judging
only the new half would let an amendment write precisely the collision the rules
exist to forbid, and it would look green doing it.

The generation call itself is not exercised here: `_apply`, `resolve_window` and
`validate_amendment` are synchronous and are where the promise actually lives.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models import Activity, User, UserProfile
from app.models.goal_race import GoalRace
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.coach import proposed_actions
from app.services.schedule import store
from app.services.schedule.amend import (
    MAX_AMEND_WEEKS,
    AmendedPlan,
    _apply,
    resolve_window,
)
from app.services.schedule.draft_contract import DraftedWeek
from app.services.schedule.effort import build_load_model
from app.services.schedule.plan_validator import VOLUME_CEILING, validate_amendment
from app.services.weeks import MONDAY, SUNDAY, week_start

# A fixed calendar for everything that takes `today` as an argument. The offer
# path is the exception and is anchored to the real today, because it resolves
# its window against `date.today()` itself.
TODAY = date(2026, 8, 12)  # a Wednesday
WEEK_0 = date(2026, 8, 10)  # the Monday it falls in
WEEK_1 = WEEK_0 + timedelta(days=7)
WEEK_2 = WEEK_0 + timedelta(days=14)
WEEK_3 = WEEK_0 + timedelta(days=21)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db, *, week_starts_on=None) -> User:
    user = User(email=f"amend-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=5,
            max_hr=190,
            week_starts_on=week_starts_on,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _plan(db, user: User, *, week_shapes=None, horizon_end=None, rules=None,
          goal_race_id=None, status="active") -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status=status,
        rules=rules or [],
        week_shapes=week_shapes or [],
        horizon_end=horizon_end,
        goal_race_id=goal_race_id,
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
    db.refresh(row)
    return row


def _activity(db, user: User, *, day: date) -> Activity:
    row = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        start_date=datetime(day.year, day.month, day.day, 9, 0),
        type="Run",
        name="Run",
        distance_m=16000,
        moving_time_s=5400,
        elapsed_time_s=5400,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _amended(*weeks: dict) -> AmendedPlan:
    return AmendedPlan.model_validate({"weeks": list(weeks)})


def _week(week_start_date: date, *sessions: dict, phase=None) -> dict:
    return {
        "week_start": week_start_date.isoformat(),
        "phase": phase,
        "sessions": list(sessions),
    }


def _new_session(day: date, *, intent="easy", title="New session", **kw) -> dict:
    end = kw.pop("end", day)
    payload = {
        "window_start": day.isoformat(),
        "window_end": end.isoformat(),
        "intent": intent,
        "discipline": kw.pop("discipline", "run"),
        "title": title,
    }
    payload.update(kw)
    return payload


def _snapshot(row: PlannedSession) -> dict:
    return {
        "id": row.id,
        "window_start": row.window_start,
        "window_end": row.window_end,
        "intent": row.intent,
        "discipline": row.discipline,
        "commitment": row.commitment,
        "title": row.title,
        "target_distance_m": row.target_distance_m,
        "target_effort_score": row.target_effort_score,
        "completed_at": row.completed_at,
        "completed_activity_id": row.completed_activity_id,
        "completion_source": row.completion_source,
    }


def _rows(db, plan) -> list:
    return (
        db.query(PlannedSession)
        .filter(PlannedSession.plan_id == plan.id)
        .order_by(PlannedSession.window_start.asc())
        .all()
    )


# --- resolve_window: the server owns the dates -------------------------------


def test_this_week_resolves_to_the_runners_own_week_not_the_next_seven_days(db):
    """The coach names WHICH weeks; the server works out what those are. "This
    week" is the week the runner is in, which on a Wednesday started two days
    ago — not a rolling seven days from today."""
    assert resolve_window(TODAY, MONDAY, weeks_from=0, weeks_through=0) == (
        WEEK_0,
        WEEK_0 + timedelta(days=6),
    )


def test_a_multi_week_window_spans_whole_weeks_end_to_end(db):
    assert resolve_window(TODAY, MONDAY, weeks_from=1, weeks_through=2) == (
        WEEK_1,
        WEEK_2 + timedelta(days=6),
    )


def test_a_sunday_start_runner_gets_a_different_week_from_the_same_offsets(db):
    """The runner's own week boundary decides which sessions get overwritten, so
    resolving against a hardcoded Monday would amend the wrong week for every
    Sunday-start runner — silently, since the offsets look identical."""
    monday_start = resolve_window(TODAY, MONDAY, weeks_from=0, weeks_through=0)
    sunday_start = resolve_window(TODAY, SUNDAY, weeks_from=0, weeks_through=0)

    assert sunday_start == (date(2026, 8, 9), date(2026, 8, 15))
    assert sunday_start != monday_start
    assert sunday_start[0] == week_start(TODAY, SUNDAY)


def test_an_inverted_or_negative_window_never_resolves_to_a_backwards_span(db):
    """Defence in depth. The request validator refuses both, but this function
    decides which rows are deleted, so it must not produce a span that reads
    backwards even when handed nonsense."""
    start, end = resolve_window(TODAY, MONDAY, weeks_from=2, weeks_through=0)
    assert start <= end

    start, end = resolve_window(TODAY, MONDAY, weeks_from=-3, weeks_through=0)
    assert start == WEEK_0
    assert end == WEEK_0 + timedelta(days=6)


# --- _apply: the window is the boundary --------------------------------------


def test_only_the_sessions_inside_the_window_are_replaced(db):
    """The promise the card makes, asserted on row IDENTITY.

    A delete-and-rewrite that produced the same titles would satisfy a count and
    would still have thrown away sessions the runner agreed to; the ids are what
    prove the rows were never touched.
    """
    user = _user(db)
    plan = _plan(db, user, horizon_end=WEEK_3 + timedelta(days=6))
    before = _session(db, plan, start=WEEK_0 + timedelta(days=1), intent="easy",
                      title="Before the window")
    inside = _session(db, plan, start=WEEK_1 + timedelta(days=2), intent="quality",
                      title="Inside the window")
    after = _session(db, plan, start=WEEK_2 + timedelta(days=3), intent="long",
                     title="After the window")
    untouched = {_snapshot(before)["id"]: _snapshot(before),
                 _snapshot(after)["id"]: _snapshot(after)}

    written = _apply(
        db,
        user,
        plan,
        _amended(_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=1),
                                            title="Softer Tuesday"))),
        build_load_model([], TODAY),
        start=WEEK_1,
        end=WEEK_1 + timedelta(days=6),
        today=TODAY,
    )

    assert written == 1
    rows = _rows(db, plan)
    titles = [row.title for row in rows]
    assert titles == ["Before the window", "Softer Tuesday", "After the window"]
    # The two outside the window are the SAME rows, field for field.
    for row in rows:
        if row.id in untouched:
            assert _snapshot(row) == untouched[row.id]
    assert inside.id not in {row.id for row in rows}


def test_a_completed_session_inside_the_window_survives_the_amendment(db):
    """Non-negotiable: what the runner did is a record, not a plan. Its match to
    the activity that completed it and how that match was made survive with it,
    because those are the record too.

    The session is pinned FRIDAY and completed today, so its window is still
    open: a completed session that had also passed would survive for the wrong
    reason and this test would prove nothing about completion. Running a session
    before its window closes is ordinary (`complete_planned_session` has no
    window restriction), so this is the real case, not a contrived one.
    """
    user = _user(db)
    plan = _plan(db, user)
    friday = WEEK_0 + timedelta(days=4)
    activity = _activity(db, user, day=TODAY)
    done = _session(
        db,
        plan,
        start=friday,
        intent="long",
        title="Friday long run, run early",
        completed_at=datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc),
        completed_activity_id=activity.id,
        completion_source="matched",
    )
    assert done.window_end >= TODAY, "the window must still be open, or this proves nothing"
    before = _snapshot(done)

    _apply(
        db,
        user,
        plan,
        _amended(_week(WEEK_0, _new_session(WEEK_0 + timedelta(days=3),
                                            title="Thursday easy"))),
        build_load_model([], TODAY),
        start=WEEK_0,
        end=WEEK_0 + timedelta(days=6),
        today=TODAY,
    )

    survivor = db.query(PlannedSession).filter(PlannedSession.id == done.id).first()
    assert survivor is not None
    assert _snapshot(survivor) == before
    assert survivor.completed_activity_id == activity.id
    assert survivor.completion_source == "matched"


def test_a_session_whose_window_has_already_closed_survives(db):
    """History cannot be re-planned, only mis-stated. Monday is gone by
    Wednesday, so a session pinned to it is a record of what was asked for, and
    the gate would refuse a replacement for it as being in the past anyway."""
    user = _user(db)
    plan = _plan(db, user)
    passed = _session(db, plan, start=WEEK_0, intent="easy", title="Monday, gone")
    ahead = _session(db, plan, start=WEEK_0 + timedelta(days=4), intent="quality",
                     title="Friday, still ahead")

    _apply(
        db,
        user,
        plan,
        _amended(_week(WEEK_0, _new_session(WEEK_0 + timedelta(days=5),
                                            title="Saturday instead"))),
        build_load_model([], TODAY),
        start=WEEK_0,
        end=WEEK_0 + timedelta(days=6),
        today=TODAY,
    )

    remaining = {row.title for row in _rows(db, plan)}
    assert "Monday, gone" in remaining
    assert "Friday, still ahead" not in remaining
    assert "Saturday instead" in remaining
    assert passed.id in {row.id for row in _rows(db, plan)}
    assert ahead.id not in {row.id for row in _rows(db, plan)}


def test_the_plan_keeps_its_identity_because_an_amendment_is_not_a_redraft(db):
    """Same row, same rules, same race, same `generated_at`. A restore still
    finds the plan it always did, and nothing downstream reads the block as newly
    written."""
    user = _user(db)
    race = GoalRace(
        user_id=user.id,
        name="Autumn Half",
        race_date=WEEK_3 + timedelta(days=5),
        distance_m=21097.5,
        priority="A",
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    rules = [
        {
            "kind": "rest_day_after",
            "label": "A full rest day after the long run",
            "source": "coach",
            "intent": "long",
        }
    ]
    plan = _plan(db, user, rules=rules, goal_race_id=race.id,
                 horizon_end=WEEK_3 + timedelta(days=6))
    plan.generated_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    db.commit()
    identity = (plan.id, plan.generated_at, plan.goal_race_id, plan.status)

    _apply(
        db,
        user,
        plan,
        _amended(_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=1)))),
        build_load_model([], TODAY),
        start=WEEK_1,
        end=WEEK_1 + timedelta(days=6),
        today=TODAY,
    )

    db.refresh(plan)
    assert (plan.id, plan.generated_at, plan.goal_race_id, plan.status) == identity
    assert store.plan_rules(plan) == store.plan_rules(
        db.query(TrainingPlan).filter(TrainingPlan.id == plan.id).one()
    )
    assert [rule.kind for rule in store.plan_rules(plan)] == ["rest_day_after"]
    assert db.query(TrainingPlan).filter(TrainingPlan.user_id == user.id).count() == 1


def test_a_week_now_holding_real_sessions_loses_its_sketch(db):
    """No week may be both planned and sketched. A leftover shape beside real
    sessions is a second answer about the same week, and the horizon would show
    one of them while the runner trains the other."""
    user = _user(db)
    plan = _plan(
        db,
        user,
        week_shapes=[
            {"week_start": WEEK_1.isoformat(), "phase": "build",
             "long_run_distance_m": 18000.0},
            {"week_start": WEEK_2.isoformat(), "phase": "peak",
             "long_run_distance_m": 20000.0},
        ],
        horizon_end=WEEK_2 + timedelta(days=6),
    )

    _apply(
        db,
        user,
        plan,
        _amended(_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=1)))),
        build_load_model([], TODAY),
        start=WEEK_1,
        end=WEEK_1 + timedelta(days=6),
        today=TODAY,
    )

    db.refresh(plan)
    remaining = store.plan_week_shapes(plan)
    assert [shape.week_start for shape in remaining] == [WEEK_2]
    # The week beyond the window keeps everything it had.
    assert remaining[0].long_run_distance_m == 20000.0


def test_the_plans_reach_only_ever_grows(db):
    """`horizon_end` is a floor on the plan's reach. An amendment writing inside
    it has not shortened anything, and one writing past it has extended the
    plan."""
    user = _user(db)
    far = WEEK_3 + timedelta(days=6)
    plan = _plan(db, user, horizon_end=far)

    _apply(
        db, user, plan,
        _amended(_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=1)))),
        build_load_model([], TODAY),
        start=WEEK_1, end=WEEK_1 + timedelta(days=6), today=TODAY,
    )
    db.refresh(plan)
    assert plan.horizon_end == far, "amending an early week must not shorten the plan"

    beyond = WEEK_3 + timedelta(days=13)
    _apply(
        db, user, plan,
        _amended(_week(WEEK_3 + timedelta(days=7),
                       _new_session(WEEK_3 + timedelta(days=8)))),
        build_load_model([], TODAY),
        start=WEEK_3 + timedelta(days=7), end=beyond, today=TODAY,
    )
    db.refresh(plan)
    assert plan.horizon_end == beyond


def test_the_amended_sessions_carry_what_the_coach_wrote(db):
    """An amended session is held to exactly the contract a drafted one is, down
    to the rep structure, and its load is priced by the app rather than stated by
    the model."""
    user = _user(db)
    plan = _plan(db, user)

    _apply(
        db,
        user,
        plan,
        _amended(
            _week(
                WEEK_1,
                _new_session(
                    WEEK_1 + timedelta(days=2),
                    intent="quality",
                    title="5 x 800m",
                    detail="Threshold work, jog 2 min between.",
                    commitment="committed",
                    target_distance_m=9000,
                    reps_planned=5,
                    rep_distance_m=800,
                    rest_s=120,
                ),
            )
        ),
        build_load_model([], TODAY),
        start=WEEK_1,
        end=WEEK_1 + timedelta(days=6),
        today=TODAY,
    )

    row = _rows(db, plan)[0]
    assert row.title == "5 x 800m"
    assert row.detail == "Threshold work, jog 2 min between."
    assert row.structure == {"reps_planned": 5, "rep_distance_m": 800, "rest_s": 120}
    assert row.plan_id == plan.id and row.user_id == user.id
    # No history to price against, so the app abstains rather than inventing a
    # load — the same abstention the draft makes.
    assert row.target_effort_score is None


def test_another_runners_sessions_are_never_in_the_window(db):
    """The window is a date range, so the owner scoping has to come from the
    query. Two runners training the same week is the ordinary case, not an edge
    one."""
    mine, theirs = _user(db), _user(db)
    my_plan = _plan(db, mine)
    their_plan = _plan(db, theirs)
    _session(db, my_plan, start=WEEK_1 + timedelta(days=1), title="Mine")
    theirs_row = _session(db, their_plan, start=WEEK_1 + timedelta(days=1),
                          title="Theirs")

    _apply(
        db, mine, my_plan,
        _amended(_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=3)))),
        build_load_model([], TODAY),
        start=WEEK_1, end=WEEK_1 + timedelta(days=6), today=TODAY,
    )

    survivor = db.query(PlannedSession).filter(
        PlannedSession.id == theirs_row.id
    ).first()
    assert survivor is not None
    assert survivor.title == "Theirs"


# --- validate_amendment: the gate sees the whole week ------------------------


def _rest_day_after_long():
    from app.schemas.schedule import SpacingRule

    return [
        SpacingRule.model_validate(
            {
                "kind": "rest_day_after",
                "label": "A full rest day after the long run",
                "intent": "long",
            }
        )
    ]


def _drafted_week(week_start_date: date, *sessions: dict) -> DraftedWeek:
    return DraftedWeek.model_validate(
        {"week_start": week_start_date.isoformat(), "sessions": list(sessions)}
    )


def test_the_gate_judges_the_week_the_runner_will_actually_train(db):
    """THE subtle one, and the join the gate could silently miss.

    Monday's long run is done and survives the amendment. The plan's rule says a
    full rest day follows a long run. An amendment that writes Tuesday is
    therefore writing the exact collision the rule exists to forbid — and a gate
    that looked only at the new sessions would see one easy run in an empty week
    and pass it.
    """
    user = _user(db)
    plan = _plan(db, user)
    surviving = _session(
        db,
        plan,
        start=WEEK_0 + timedelta(days=1),
        intent="long",
        title="Monday long run",
        completed_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )
    amendment = [
        _drafted_week(
            WEEK_0,
            _new_session(WEEK_0 + timedelta(days=2), intent="easy",
                         title="Tuesday easy", target_distance_m=8000),
        )
    ]

    rejected = validate_amendment(
        amendment,
        rules=_rest_day_after_long(),
        surviving_by_week={WEEK_0: [surviving]},
        today=TODAY,
        starts_on=MONDAY,
    )

    assert rejected.ok is False
    assert any("rest day" in failure for failure in rejected.failures)

    # The proof that the rejection came from the JOIN and not from the new
    # session on its own: the identical amendment with nothing surviving passes.
    accepted = validate_amendment(
        amendment,
        rules=_rest_day_after_long(),
        surviving_by_week={},
        today=TODAY,
        starts_on=MONDAY,
    )
    assert accepted.ok is True, accepted.failures


def test_the_volume_ceiling_counts_the_week_not_the_amendment(db):
    """An amendment adding one 20 km run beside two surviving ones would
    otherwise be measured on its own contribution and pass a week that is
    absurd."""
    user = _user(db)
    plan = _plan(db, user)
    surviving = [
        _session(db, plan, start=WEEK_0 + timedelta(days=1), intent="long",
                 title="Kept long run", target_distance_m=20000,
                 completed_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)),
        _session(db, plan, start=WEEK_0 + timedelta(days=2), intent="easy",
                 title="Kept easy run", target_distance_m=20000,
                 completed_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)),
    ]
    amendment = [
        _drafted_week(
            WEEK_0,
            _new_session(WEEK_0 + timedelta(days=4), intent="easy",
                         title="One more", target_distance_m=20000),
        )
    ]
    # Typical week: 25 km, so the concrete ceiling is 50 km. The amendment alone
    # is 20 km and is nowhere near it; the week it actually creates is 40 km of
    # surviving running plus 20 more.
    norm = 25000.0

    alone = validate_amendment(
        amendment, rules=[], surviving_by_week={}, today=TODAY,
        starts_on=MONDAY, norm_weekly_running_m=norm,
    )
    assert alone.ok is True, alone.failures

    whole_week = validate_amendment(
        amendment, rules=[], surviving_by_week={WEEK_0: surviving}, today=TODAY,
        starts_on=MONDAY, norm_weekly_running_m=norm,
    )

    assert whole_week.ok is False
    assert VOLUME_CEILING in whole_week.codes
    assert "60 km" in " ".join(whole_week.failures)
    assert "typical 25 km" in " ".join(whole_week.failures)


def test_a_surviving_suggestion_does_not_constrain_the_amendment(db):
    """A suggestion the runner can decline with no trace is not part of the week
    the rules are checked against, the same reading every other surface applies
    to a suggestion."""
    user = _user(db)
    plan = _plan(db, user)
    suggestion = _session(
        db, plan, start=WEEK_0 + timedelta(days=1), intent="long",
        title="Optional long run", commitment="suggested",
        completed_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )

    check = validate_amendment(
        [
            _drafted_week(
                WEEK_0,
                _new_session(WEEK_0 + timedelta(days=2), intent="easy",
                             title="Tuesday easy", target_distance_m=8000),
            )
        ],
        rules=_rest_day_after_long(),
        surviving_by_week={WEEK_0: [suggestion]},
        today=TODAY,
        starts_on=MONDAY,
    )

    assert check.ok is True, check.failures


def test_an_amendment_with_no_weeks_is_refused(db):
    """"Change nothing" reaching the store as an amendment would delete the
    window and write nothing back."""
    check = validate_amendment(
        [], rules=[], surviving_by_week={}, today=TODAY, starts_on=MONDAY
    )
    assert check.ok is False
    assert "no weeks at all" in " ".join(check.failures)


def test_a_week_that_is_not_a_week_boundary_is_refused(db):
    """A week starting on a Wednesday would place its sessions across two of the
    runner's weeks, and every roll-up downstream buckets by week start."""
    check = validate_amendment(
        [_drafted_week(TODAY, _new_session(TODAY + timedelta(days=1)))],
        rules=[], surviving_by_week={}, today=TODAY, starts_on=MONDAY,
    )
    assert check.ok is False
    assert "week boundary" in " ".join(check.failures)


def test_a_week_in_the_past_is_refused(db):
    """Amending a week that has been and gone rewrites what the runner was asked
    to do after they have done it."""
    check = validate_amendment(
        [
            _drafted_week(
                WEEK_0 - timedelta(days=7),
                _new_session(WEEK_0 - timedelta(days=6), target_distance_m=8000),
            )
        ],
        rules=[], surviving_by_week={}, today=TODAY, starts_on=MONDAY,
    )
    assert check.ok is False
    assert "is in the past" in " ".join(check.failures)


def test_the_same_week_twice_is_refused(db):
    """Two answers for one week. Applied, the second silently wins and the
    coach's first set of sessions is lost without anything saying so."""
    check = validate_amendment(
        [
            _drafted_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=1),
                                              target_distance_m=8000)),
            _drafted_week(WEEK_1, _new_session(WEEK_1 + timedelta(days=2),
                                              target_distance_m=8000)),
        ],
        rules=[], surviving_by_week={}, today=TODAY, starts_on=MONDAY,
    )
    assert check.ok is False
    assert "appears twice" in " ".join(check.failures)


def test_a_coherent_amendment_passes_the_gate(db):
    """The control. Without it every rejection above could be a gate that refuses
    everything."""
    check = validate_amendment(
        [
            _drafted_week(
                WEEK_1,
                _new_session(WEEK_1 + timedelta(days=1), intent="easy",
                             title="Easy", target_distance_m=8000),
                _new_session(WEEK_1 + timedelta(days=5), intent="long",
                             title="Long", target_distance_m=16000),
            )
        ],
        rules=_rest_day_after_long(),
        surviving_by_week={},
        today=TODAY,
        starts_on=MONDAY,
        norm_weekly_running_m=30000.0,
    )
    assert check.ok is True, check.failures


# --- the offer the runner confirms -------------------------------------------
#
# Anchored to the REAL today: `_build_offer` resolves its window against
# `date.today()` itself, so a fixed calendar date here would rot at midnight.

REAL_TODAY = date.today()


def _offer(db, user, **overrides) -> tuple:
    payload = {
        "action_type": "amend_plan",
        "weeks_from": 0,
        "weeks_through": 1,
        "amend_reason": "drop one hard session, right calf is sore",
    }
    payload.update(overrides)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        return proposed_actions.mint_proposed_action(db, user.id, payload)


def _live_plan(db, user) -> TrainingPlan:
    plan = _plan(db, user, horizon_end=REAL_TODAY + timedelta(days=60))
    _session(db, plan, start=REAL_TODAY + timedelta(days=1), title="Something ahead")
    return plan


def test_an_amendment_without_a_reason_is_not_offered(db):
    """The card has to say WHY as well as what. "Change your next two weeks" is
    not something a runner can agree to."""
    user = _user(db)
    _live_plan(db, user)

    result, frame = _offer(db, user, amend_reason=None)

    assert result["ok"] is False
    assert frame is None


@pytest.mark.parametrize(
    "overrides, because",
    [
        ({"weeks_from": None}, "no first week"),
        ({"weeks_through": None}, "no last week"),
        ({"weeks_from": 2, "weeks_through": 1}, "the window runs backwards"),
        ({"weeks_from": 0, "weeks_through": MAX_AMEND_WEEKS},
         "wider than an amendment may reach"),
    ],
)
def test_a_window_that_is_not_a_window_is_not_offered(db, overrides, because):
    """The bound is what keeps "amend" from quietly becoming "redraft". A window
    wide enough to swallow the block is not making the promise the card makes."""
    user = _user(db)
    _live_plan(db, user)

    result, frame = _offer(db, user, **overrides)

    assert result["ok"] is False, because
    assert frame is None


def test_the_widest_legal_window_is_still_offered(db):
    """The other side of the bound, so the parametrized refusals above are not
    passing because everything is refused."""
    user = _user(db)
    _live_plan(db, user)

    result, frame = _offer(db, user, weeks_from=0, weeks_through=MAX_AMEND_WEEKS - 1)

    assert result["ok"] is True
    assert frame["action_type"] == "amend_plan"


def test_a_runner_with_no_plan_is_refused_before_the_card_goes_up(db):
    """A card the runner taps only to be told there is nothing to amend is worse
    than no card. The coach still has `draft_plan`, which is the right offer for
    a runner with no plan, and the refusal says so."""
    user = _user(db)

    result, frame = _offer(db, user)

    assert result["ok"] is False
    assert frame is None
    assert "draft_plan" in result["detail"]


def test_the_card_names_the_window_and_the_promise(db):
    """#883's lesson applied to the smaller verb. A runner who cannot judge the
    scope of a card is how a plan agreed ninety seconds earlier was lost, so the
    card states the reason, the dates it covers, and what it leaves alone."""
    user = _user(db)
    _live_plan(db, user)
    start, end = resolve_window(REAL_TODAY, MONDAY, weeks_from=0, weeks_through=1)

    result, frame = _offer(db, user)

    assert result["ok"] is True
    described = frame["description"]
    assert described.startswith("Drop one hard session, right calf is sore")
    assert end.strftime("%b") in described
    assert str(int(start.strftime("%d"))) in described
    assert str(int(end.strftime("%d"))) in described
    assert "The rest of your plan, its rules and your race stay as they are." in described
    assert frame["confirm_label"] == "Update my plan"
    # The model reads back that nothing is written yet, and never the token.
    assert "token" not in result


def test_the_schedule_kill_switch_closes_the_offer(db, monkeypatch):
    """`SCHEDULE_ENABLED` off is the fast lever when the schedule misbehaves. A
    write into schedule rows that stays open through it is not a kill switch."""
    user = _user(db)
    _live_plan(db, user)
    # The control first: with the switch on, this exact offer is minted. Without
    # it, a refusal below could be a refusal of everything.
    allowed, allowed_frame = _offer(db, user)
    assert allowed["ok"] is True and allowed_frame is not None

    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
    result, frame = _offer(db, user)

    assert result["ok"] is False
    assert frame is None


def test_the_schedule_kill_switch_closes_the_confirm_too(db, monkeypatch):
    """A token minted before the switch was thrown must not still write. The
    offer and the execute are separated by up to half an hour."""
    user = _user(db)
    _live_plan(db, user)
    redis = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", redis):
        _, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "amend_plan",
                "weeks_from": 0,
                "weeks_through": 0,
                "amend_reason": "soften this week",
            },
        )
        assert frame is not None, "the offer must succeed before the switch flips"
        monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
        enqueued = []
        with patch(
            "app.jobs.amend_schedule.enqueue_amendment",
            side_effect=lambda *a, **k: enqueued.append((a, k)),
        ):
            with pytest.raises(ValueError, match="unavailable"):
                proposed_actions.consume_and_execute(db, user.id, frame["token"])

    # Nothing was enqueued, and the confirm that DOES enqueue is pinned in
    # `test_a_confirm_lands_on_the_plan_it_was_offered_against`, so this is a
    # refusal rather than a path that never fires.
    assert enqueued == []


def test_a_confirm_lands_on_the_plan_it_was_offered_against(db):
    """The happy path, and the evidence the guard below is not simply refusing
    everything."""
    user = _user(db)
    plan = _live_plan(db, user)
    redis = _FakeRedis()
    enqueued = []

    with patch.object(proposed_actions, "redis_conn", redis):
        _, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "amend_plan",
                "weeks_from": 0,
                "weeks_through": 1,
                "amend_reason": "write the next block from the agreed shape",
            },
        )
        with patch(
            "app.jobs.amend_schedule.enqueue_amendment",
            side_effect=lambda *a, **k: enqueued.append((a, k)),
        ):
            result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert result["action_type"] == "amend_plan"
    assert result["plan_id"] == str(plan.id)
    assert "Everything else in your plan stays as it is." in result["message"]
    assert enqueued and enqueued[0][0][1] == plan.id
    assert enqueued[0][1] == {
        "weeks_from": 0,
        "weeks_through": 1,
        "instruction": "write the next block from the agreed shape",
    }


def test_a_confirm_is_refused_when_the_plan_changed_under_it(db):
    """A token lives half an hour, and a draft or a restore in that window makes
    a DIFFERENT plan current. Amending that one would apply a change the runner
    agreed for one block to a block they never saw it described against."""
    user = _user(db)
    original = _live_plan(db, user)
    redis = _FakeRedis()
    enqueued = []

    with patch.object(proposed_actions, "redis_conn", redis):
        _, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "amend_plan",
                "weeks_from": 0,
                "weeks_through": 0,
                "amend_reason": "soften this week",
            },
        )
        # A new plan becomes current between the card going up and the tap.
        replacement = _plan(db, user, status="drafting")
        store.activate_plan(db, replacement)
        assert store.get_active_plan(db, user.id).id != original.id

        with patch(
            "app.jobs.amend_schedule.enqueue_amendment",
            side_effect=lambda *a, **k: enqueued.append((a, k)),
        ):
            with pytest.raises(ValueError, match="plan changed"):
                proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert enqueued == []


@pytest.mark.parametrize(
    "action_type, extra",
    [
        ("draft_plan", {}),
        ("revise_max_hr", {}),
    ],
)
def test_the_window_and_the_reason_belong_to_amend_plan_alone(db, action_type, extra):
    """An argument riding along on another action is silently dropped, and an
    instruction the coach wrote and nothing stored is the shape #878 was raised
    for."""
    user = _user(db)
    _live_plan(db, user)

    for rogue in (
        {"weeks_from": 0},
        {"weeks_through": 1},
        {"amend_reason": "soften this week"},
    ):
        payload = {"action_type": action_type, **extra, **rogue}
        with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
            result, frame = proposed_actions.mint_proposed_action(db, user.id, payload)

        assert result["ok"] is False, f"{action_type} accepted {rogue}"
        assert frame is None


def test_the_action_is_declared_where_the_coach_can_reach_it(db):
    """A tool the model cannot name is a verb that does not exist, however
    complete the machinery behind it."""
    tool = proposed_actions.PROPOSED_ACTION_TOOL
    assert "amend_plan" in tool["input_schema"]["properties"]["action_type"]["enum"]
    for field in ("weeks_from", "weeks_through", "amend_reason"):
        assert field in tool["input_schema"]["properties"]
    # And the description steers away from the destructive neighbour.
    assert "amend_plan" in tool["description"]
    assert "draft_plan throws away" in tool["description"]
