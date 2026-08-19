"""#857: a replaced training plan can be brought back.

Writing a plan supersedes the one the runner was training to. Nothing was ever
destroyed (`activate_plan` only flips a status), but nothing could reach the old
plan either, which made `draft_plan` the one proposed action with no way back.

Three properties are pinned here, in the order they matter.

1. WHICH plan comes back. "The previous plan" has to mean one thing once several
   have been superseded, and the answer is the one most recently STEPPED AWAY
   from. The interesting case is the second restore: order on when a plan was
   written and the second one returns the wrong plan, silently.
2. Restore is SYMMETRIC. The plan being left behind is retained exactly the way
   the restored one was, and is what the next read offers, so going back is
   itself something you can go back from.
3. It refuses two things: another runner's plan (indistinguishable from a
   missing one), and a plan whose horizon has entirely passed, which would leave
   the runner reporting a plan while holding no session.

All row data is synthetic test setup (exercises code paths; represents no real
runner). The claims under test are ordering, symmetry and denial rules; no row's
contents bear on any of them.
"""

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import store

TODAY = date.today()
FUTURE = TODAY + timedelta(days=60)
PAST = TODAY - timedelta(days=30)


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _seed_user(db) -> User:
    user = User(email=f"restore-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=190,
        )
    )
    db.commit()
    db.refresh(user)
    return user


def _seed_plan(
    db,
    user: User,
    *,
    status: str = "active",
    horizon_end: date = FUTURE,
    generated_at: datetime = None,
) -> TrainingPlan:
    plan = TrainingPlan(
        user_id=user.id,
        status=status,
        horizon_end=horizon_end,
        rules=[],
        week_shapes=[],
        generated_at=generated_at,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _seed_session(db, user: User, plan: TrainingPlan, *, day: date) -> PlannedSession:
    session = PlannedSession(
        plan_id=plan.id,
        user_id=user.id,
        window_start=day,
        window_end=day,
        intent="easy",
        discipline="run",
        commitment="committed",
        title="Easy run",
        target_distance_m=8000,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _activate(db, plan: TrainingPlan) -> TrainingPlan:
    """Activate through the real writer: the transition under test.

    Seeding statuses by hand would prove the reader can sort rows somebody
    already labelled, which is not the claim.
    """
    return store.activate_plan(db, plan)


# --- which plan is "the previous plan" -------------------------------------


def test_a_runner_with_one_plan_has_nothing_to_go_back_to(db):
    user = _seed_user(db)
    _activate(db, _seed_plan(db, user, status="drafting"))

    assert store.previous_plan(db, user.id) is None


def test_the_previous_plan_is_the_one_just_replaced(db):
    """Two supersessions deep, the answer is the LAST one stepped away from."""
    user = _seed_user(db)
    first = _activate(db, _seed_plan(db, user, status="drafting"))
    second = _activate(db, _seed_plan(db, user, status="drafting"))
    third = _activate(db, _seed_plan(db, user, status="drafting"))

    previous = store.previous_plan(db, user.id)

    assert previous is not None
    assert previous.id == second.id
    # Not merely "some superseded plan": the older one is still there and is not
    # the answer.
    db.refresh(first)
    assert first.status == "superseded"
    db.refresh(third)
    assert third.status == "active"


def test_the_previous_plan_is_never_the_active_one(db):
    user = _seed_user(db)
    _activate(db, _seed_plan(db, user, status="drafting"))
    current = _activate(db, _seed_plan(db, user, status="drafting"))

    previous = store.previous_plan(db, user.id)

    assert previous is not None and previous.id != current.id


def test_a_restore_makes_the_plan_stepped_away_from_the_next_previous(db):
    """The symmetry the acceptance criteria ask for, at the service level."""
    user = _seed_user(db)
    old = _activate(db, _seed_plan(db, user, status="drafting"))
    new = _activate(db, _seed_plan(db, user, status="drafting"))

    store.restore_plan(db, old)

    db.refresh(old)
    db.refresh(new)
    assert old.status == "active"
    assert new.status == "superseded"
    back = store.previous_plan(db, user.id)
    assert back is not None and back.id == new.id


def test_going_back_twice_returns_to_where_you_were_each_time(db):
    """The case that separates "most recently superseded" from every proxy for
    it. Ordering on when a plan was WRITTEN gets this wrong on the second hop:
    after restoring the older plan, the newest-written superseded row is the one
    the runner has just come back from, not the one they are on."""
    user = _seed_user(db)
    first = _activate(db, _seed_plan(db, user, status="drafting"))
    second = _activate(db, _seed_plan(db, user, status="drafting"))
    third = _activate(db, _seed_plan(db, user, status="drafting"))

    # On `third`; step back to `second`.
    step_back = store.previous_plan(db, user.id)
    assert step_back.id == second.id
    store.restore_plan(db, step_back)

    # Now on `second`. The way back is `third`, the plan just stepped away from,
    # and NOT `first`, which is the older of the two superseded rows.
    step_forward = store.previous_plan(db, user.id)
    assert step_forward.id == third.id, (
        "the second hop returned the wrong plan: 'previous' has to mean the last "
        "one stepped away from, not the most recently written"
    )
    store.restore_plan(db, step_forward)

    db.refresh(third)
    assert third.status == "active"
    assert store.previous_plan(db, user.id).id == second.id
    db.refresh(first)
    assert first.status == "superseded"


def test_a_new_plan_written_after_a_restore_leaves_the_restored_one_as_previous(db):
    """The case where "most recently superseded" and "most recently written"
    genuinely disagree, and the reason this needs a recorded transition rather
    than a proxy read off the rows.

    Go back a plan, then ask the coach for a new one. The plan the runner was
    training to when that new one landed is the RESTORED older plan, but it is
    not the newest-written row in the superseded set, and ordering on when a plan
    was written offers them the wrong one with no sign anything is wrong.
    """
    user = _seed_user(db)
    first = _activate(db, _seed_plan(db, user, status="drafting"))
    second = _activate(db, _seed_plan(db, user, status="drafting"))
    third = _activate(db, _seed_plan(db, user, status="drafting"))

    # Back to `second`, which is now current and `third` is history.
    store.restore_plan(db, second)
    # The coach writes a new plan while the runner is on the restored one.
    fourth = _activate(db, _seed_plan(db, user, status="drafting"))

    previous = store.previous_plan(db, user.id)

    assert previous.id == second.id, (
        "the plan replaced was the restored one; ordering on when a plan was "
        "WRITTEN offers the newer row that was replaced earlier"
    )
    db.refresh(fourth)
    assert fourth.status == "active"
    for plan in (first, third):
        db.refresh(plan)
        assert plan.status == "superseded"


def test_a_plan_superseded_before_the_column_existed_sorts_last(db):
    """Legacy rows carry a null `superseded_at`. Unknown is older than anything
    known, so such a row must never outrank a recorded transition."""
    user = _seed_user(db)
    legacy = _seed_plan(db, user, status="superseded")
    legacy.superseded_at = None
    db.commit()
    recorded = _activate(db, _seed_plan(db, user, status="drafting"))
    _activate(db, _seed_plan(db, user, status="drafting"))

    previous = store.previous_plan(db, user.id)

    assert previous.id == recorded.id
    db.refresh(legacy)
    assert legacy.superseded_at is None


def test_restoring_keeps_the_plans_own_provenance(db):
    """`generated_at` says when the plan's thinking was written. A plan brought
    back is not newly written, and re-dating it would report a three-week-old
    plan as fresh while destroying the only field that said otherwise."""
    user = _seed_user(db)
    written = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    old = _seed_plan(db, user, status="drafting", generated_at=written)
    old = store.activate_plan(db, old, stamp_generated=False)
    _activate(db, _seed_plan(db, user, status="drafting"))

    store.restore_plan(db, old)

    db.refresh(old)
    assert old.generated_at.replace(tzinfo=timezone.utc) == written
    # And the plan that is current is not sitting in the history it just left.
    assert old.superseded_at is None


def test_a_restored_plan_keeps_its_identity_and_its_sessions(db):
    """Nothing is copied. The runner asked for the plan they were training to,
    and a duplicate of it is a different plan, one whose ticked sessions would
    have to be either re-ticked or silently invented."""
    user = _seed_user(db)
    old = _activate(db, _seed_plan(db, user, status="drafting"))
    session = _seed_session(db, user, old, day=TODAY + timedelta(days=2))
    _activate(db, _seed_plan(db, user, status="drafting"))

    store.restore_plan(db, old)

    assert db.query(TrainingPlan).filter(TrainingPlan.user_id == user.id).count() == 2
    assert db.query(PlannedSession).filter(PlannedSession.user_id == user.id).count() == 1
    db.refresh(session)
    assert session.plan_id == old.id


# --- the refusals ----------------------------------------------------------


@pytest.mark.parametrize("status", ["active", "drafting", "failed"])
def test_only_a_superseded_plan_can_be_restored(db, status):
    user = _seed_user(db)
    plan = _seed_plan(db, user, status=status)

    assert store.restore_blocker(plan) is not None
    with pytest.raises(ValueError):
        store.restore_plan(db, plan)


def test_a_plan_whose_horizon_has_passed_is_refused(db):
    """Restoring it would be worse than doing nothing: the week would report a
    plan while holding no session, and free mode (a real answer, not an empty
    state) would be replaced by a plan that says nothing."""
    user = _seed_user(db)
    stale = _activate(db, _seed_plan(db, user, status="drafting", horizon_end=PAST))
    current = _activate(db, _seed_plan(db, user, status="drafting"))

    with pytest.raises(ValueError):
        store.restore_plan(db, stale)

    db.refresh(stale)
    db.refresh(current)
    assert stale.status == "superseded"
    assert current.status == "active"


def test_a_plan_with_no_recorded_horizon_is_not_presumed_stale(db):
    user = _seed_user(db)
    plan = _activate(db, _seed_plan(db, user, status="drafting", horizon_end=None))
    _activate(db, _seed_plan(db, user, status="drafting"))

    assert store.restore_blocker(plan) is None


# --- the API ---------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/schedule/plans/previous"),
        ("post", f"/api/schedule/plans/{uuid4()}/restore"),
    ],
)
def test_the_restore_routes_refuse_while_the_schedule_is_switched_off(
    db, client, monkeypatch, method, path
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
    _act_as(_seed_user(db))

    assert getattr(client, method)(path).status_code == 503


def test_the_read_answers_with_a_sentence_when_there_is_nothing_to_go_back_to(
    db, client
):
    """Not a 404. "You have no earlier plan" is an answer, and the surface that
    asks needs a sentence for it either way."""
    _act_as(_seed_user(db))

    resp = client.get("/api/schedule/plans/previous")

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_id"] is None
    assert body["restorable"] is False
    assert body["message"].strip()


def test_the_read_names_the_plan_and_what_going_back_would_give(db, client):
    user = _seed_user(db)
    old = _activate(db, _seed_plan(db, user, status="drafting"))
    _seed_session(db, user, old, day=TODAY + timedelta(days=3))
    _seed_session(db, user, old, day=TODAY + timedelta(days=5))
    # Behind the runner: intact, but not something going back would give them.
    _seed_session(db, user, old, day=TODAY - timedelta(days=3))
    _activate(db, _seed_plan(db, user, status="drafting"))
    _act_as(user)

    body = client.get("/api/schedule/plans/previous").json()

    assert body["plan_id"] == str(old.id)
    assert body["restorable"] is True
    assert body["sessions_ahead"] == 2
    assert body["superseded_at"] is not None
    assert body["horizon_end"] == FUTURE.isoformat()


def test_a_tap_brings_the_previous_plan_back_and_keeps_the_current_one(db, client):
    user = _seed_user(db)
    old = _activate(db, _seed_plan(db, user, status="drafting"))
    new = _activate(db, _seed_plan(db, user, status="drafting"))
    _act_as(user)

    resp = client.post(f"/api/schedule/plans/{old.id}/restore")

    # 204: a restore changes the headline, the sessions, the rules and the
    # horizon at once, so the client refetches rather than patching.
    assert resp.status_code == 204
    assert resp.content == b""
    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == old.id).one().status == (
        "active"
    )
    assert db.query(TrainingPlan).filter(TrainingPlan.id == new.id).one().status == (
        "superseded"
    )


def test_restoring_is_itself_reversible_over_http(db, client):
    """The acceptance criterion, end to end through the API: the plan stepped
    away from is retained the same way and is what the read offers next."""
    user = _seed_user(db)
    old = _activate(db, _seed_plan(db, user, status="drafting"))
    new = _activate(db, _seed_plan(db, user, status="drafting"))
    _act_as(user)

    assert client.post(f"/api/schedule/plans/{old.id}/restore").status_code == 204
    offered = client.get("/api/schedule/plans/previous").json()
    assert offered["plan_id"] == str(new.id)
    assert offered["restorable"] is True

    assert client.post(f"/api/schedule/plans/{new.id}/restore").status_code == 204
    db.expire_all()
    assert db.query(TrainingPlan).filter(TrainingPlan.id == new.id).one().status == (
        "active"
    )
    assert client.get("/api/schedule/plans/previous").json()["plan_id"] == str(old.id)


def test_restoring_a_plan_that_is_not_superseded_is_refused_not_silently_ignored(
    db, client
):
    user = _seed_user(db)
    current = _activate(db, _seed_plan(db, user, status="drafting"))
    _act_as(user)

    resp = client.post(f"/api/schedule/plans/{current.id}/restore")

    assert resp.status_code == 422
    db.refresh(current)
    assert current.status == "active"


def test_a_stale_horizon_is_refused_over_http_with_a_reason(db, client):
    user = _seed_user(db)
    stale = _activate(db, _seed_plan(db, user, status="drafting", horizon_end=PAST))
    _activate(db, _seed_plan(db, user, status="drafting"))
    _act_as(user)

    read = client.get("/api/schedule/plans/previous").json()
    resp = client.post(f"/api/schedule/plans/{stale.id}/restore")

    assert read["plan_id"] == str(stale.id)
    assert read["restorable"] is False
    assert PAST.isoformat() in read["message"]
    assert resp.status_code == 422


# --- the tenant boundary ---------------------------------------------------


def test_another_runners_plan_is_indistinguishable_from_a_missing_one(db, client):
    """A cross-tenant id and an id that does not exist get the same answer, and
    neither moves a row."""
    theirs = _seed_user(db)
    their_old = _activate(db, _seed_plan(db, theirs, status="drafting"))
    _activate(db, _seed_plan(db, theirs, status="drafting"))
    _act_as(_seed_user(db))

    cross_tenant = client.post(f"/api/schedule/plans/{their_old.id}/restore")
    absent = client.post(f"/api/schedule/plans/{uuid4()}/restore")

    assert cross_tenant.status_code == 404
    assert absent.status_code == 404
    assert cross_tenant.json() == absent.json()
    db.refresh(their_old)
    assert their_old.status == "superseded", (
        "a cross-tenant restore moved another runner's plan"
    )


def test_another_runners_previous_plan_never_appears_in_the_read(db, client):
    theirs = _seed_user(db)
    _activate(db, _seed_plan(db, theirs, status="drafting"))
    _activate(db, _seed_plan(db, theirs, status="drafting"))
    mine = _seed_user(db)
    _act_as(mine)

    body = client.get("/api/schedule/plans/previous").json()

    assert body["plan_id"] is None
    assert body["restorable"] is False
