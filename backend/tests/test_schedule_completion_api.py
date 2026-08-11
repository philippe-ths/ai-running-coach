"""#830: the completion endpoints — tick, untick, decline.

Two of the three ways to mark a planned session done are not HTTP at all (the
auto-match from a synced activity and telling the coach), so what this file pins
is the surface: the tap writes through the same writer, the untick undoes it, a
commitment cannot be declined by a button, a cross-tenant id is indistinguishable
from a missing one, and the router-level kill switch answers before any of it.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.schedule import completion

TODAY = date.today()


def _act_as(user):
    """Inject the acting user (the resolution itself is covered elsewhere)."""
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _seed_user(db) -> User:
    user = User(email=f"sched-{uuid4()}@example.com")
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


def _seed_session(
    db,
    user: User,
    *,
    commitment: str = "committed",
    intent: str = "easy",
    discipline: str = "run",
    title: str = "Easy run",
    start: date = None,
    end: date = None,
) -> PlannedSession:
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    session = PlannedSession(
        plan_id=plan.id,
        user_id=user.id,
        # Today onwards, so an untick reads as `upcoming` rather than `missed`.
        window_start=start or TODAY,
        window_end=end or TODAY,
        intent=intent,
        discipline=discipline,
        commitment=commitment,
        title=title,
        target_distance_m=8000,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


# --- the tap ---------------------------------------------------------------


def test_a_tap_ticks_the_session_off(db, client):
    """Strava never sees the gym, so the tap is not a fallback for the matcher
    failing; it is how a whole class of session gets recorded at all."""
    user = _seed_user(db)
    session = _seed_session(db, user, title="Lower body", discipline="strength",
                            intent="strength")
    _act_as(user)

    resp = client.post(f"/api/schedule/sessions/{session.id}/complete")

    # 204: a tick changes the week's headline, done count and mix, so the client
    # refetches the week rather than patching one session it cannot patch.
    assert resp.status_code == 204
    assert resp.content == b""
    db.refresh(session)
    assert session.completed_at is not None
    assert session.completion_source == completion.MANUAL
    assert session.completed_activity_id is None


def test_the_tap_settles_a_suggestion_it_had_declined(db, client):
    """Finishing something settles it — the same write, through the endpoint."""
    user = _seed_user(db)
    session = _seed_session(db, user, commitment="suggested")
    _act_as(user)
    client.post(f"/api/schedule/sessions/{session.id}/dismiss")

    resp = client.post(f"/api/schedule/sessions/{session.id}/complete")

    assert resp.status_code == 204
    db.refresh(session)
    assert session.completed_at is not None
    assert session.dismissed_at is None


def test_an_untick_puts_the_session_back(db, client):
    """The runner is allowed to be wrong about their own week."""
    user = _seed_user(db)
    session = _seed_session(db, user)
    _act_as(user)
    client.post(f"/api/schedule/sessions/{session.id}/complete")

    resp = client.delete(f"/api/schedule/sessions/{session.id}/complete")

    assert resp.status_code == 204
    db.refresh(session)
    assert session.completed_at is None
    assert session.completion_source is None
    assert session.completed_activity_id is None


def test_unticking_something_that_was_never_ticked_is_harmless(db, client):
    user = _seed_user(db)
    session = _seed_session(db, user)
    _act_as(user)

    resp = client.delete(f"/api/schedule/sessions/{session.id}/complete")

    assert resp.status_code == 204
    db.refresh(session)
    assert session.completed_at is None


# --- declining -------------------------------------------------------------


def test_a_suggestion_can_be_declined(db, client):
    user = _seed_user(db)
    session = _seed_session(db, user, commitment="suggested")
    _act_as(user)

    resp = client.post(f"/api/schedule/sessions/{session.id}/dismiss")

    assert resp.status_code == 204
    db.refresh(session)
    assert session.dismissed_at is not None


def test_a_commitment_cannot_be_declined_by_a_button(db, client):
    """Declining something you agreed to is a plan change, and plan changes go
    through the coach — otherwise the schedule quietly becomes a to-do list."""
    user = _seed_user(db)
    session = _seed_session(db, user, commitment="committed")
    _act_as(user)

    resp = client.post(f"/api/schedule/sessions/{session.id}/dismiss")

    assert resp.status_code == 422
    db.refresh(session)
    assert session.dismissed_at is None


def test_an_off_vocabulary_session_tapped_done_still_answers(db, client):
    """KNOWN DEFECT, left failing deliberately (see the report for this slice).

    The plan is LLM-written, so a row can carry a discipline outside the closed
    set — the week read is built around exactly that and drops the card with a
    warning rather than taking the screen down. These endpoints share that
    serializer, so on such a row it returns None into a non-optional
    `response_model`: the completion COMMITS and then the response blows up, so
    the runner sees a failure for a write that happened.

    Whether the right answer is a 404, a 409 or the session rendered anyway is a
    product call, so this is reported rather than patched. The part that is not
    ambiguous is that a committed write must not surface as a server error.
    """
    user = _seed_user(db)
    session = _seed_session(db, user, discipline="swim", title="Swim")
    _act_as(user)

    resp = client.post(f"/api/schedule/sessions/{session.id}/complete")

    assert resp.status_code < 500


# --- the tenant boundary ---------------------------------------------------


@pytest.mark.parametrize(
    "method, suffix",
    [
        ("post", "complete"),
        ("delete", "complete"),
        ("post", "dismiss"),
    ],
)
def test_another_runners_session_is_indistinguishable_from_a_missing_one(
    db, client, method, suffix
):
    theirs = _seed_session(db, _seed_user(db), commitment="suggested")
    _act_as(_seed_user(db))

    cross_tenant = getattr(client, method)(
        f"/api/schedule/sessions/{theirs.id}/{suffix}"
    )
    absent = getattr(client, method)(f"/api/schedule/sessions/{uuid4()}/{suffix}")

    assert cross_tenant.status_code == 404
    assert absent.status_code == 404
    assert cross_tenant.json() == absent.json()
    db.refresh(theirs)
    assert theirs.completed_at is None
    assert theirs.dismissed_at is None


# --- the kill switch -------------------------------------------------------


@pytest.mark.parametrize(
    "method, suffix",
    [
        ("post", "complete"),
        ("delete", "complete"),
        ("post", "dismiss"),
    ],
)
def test_every_completion_route_refuses_while_the_schedule_is_switched_off(
    db, client, monkeypatch, method, suffix
):
    """The switch is a ROUTER-level dependency, so it answers before the session
    is even looked up — and a route added later cannot forget it."""
    user = _seed_user(db)
    session = _seed_session(db, user, commitment="suggested")
    _act_as(user)
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    resp = getattr(client, method)(
        f"/api/schedule/sessions/{session.id}/{suffix}"
    )

    assert resp.status_code == 503
    assert "schedule" in resp.json()["detail"].lower()
    db.refresh(session)
    assert session.completed_at is None
    assert session.dismissed_at is None
