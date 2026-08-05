"""COACH_THREADS_ENABLED (#784): the whole thread surface, off from the env.

The conventional #522 kill switch — default True so the live code path is
byte-identical, off reached by setting the flag False in the environment. Unlike
the pack-section switches this one gates a SURFACE: the routes refuse and the
frontend stops rendering the affordance, so a runner is never offered a way in
that leads nowhere.
"""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
from app.models import Activity, StravaAccount, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread


def _act_as(user):
    """Inject the acting user (the resolution itself is covered elsewhere)."""
    app.dependency_overrides[verify_clerk_session] = lambda: user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


@pytest.fixture
def _threads_off(monkeypatch):
    monkeypatch.setattr(settings, "COACH_THREADS_ENABLED", False)


def _seed(db) -> tuple[User, Activity, Thread]:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=int(uuid4().int % 10**8),
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=int(uuid4().int % 10**9),
        start_date=datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc), type="Run",
        name="Test run", distance_m=9000, moving_time_s=3000, elapsed_time_s=3000,
        elev_gain_m=10.0, avg_hr=140, raw_summary={},
    )
    db.add(activity)
    db.commit()
    thread = Thread(user_id=user.id, activity_id=activity.id)
    db.add(thread)
    db.commit()
    return user, activity, thread


class TestSurfaceRefusesWhenOff:
    def test_every_thread_route_refuses(self, client, db, _threads_off):
        """One flag takes the whole surface down — reads included, since a
        switcher the runner cannot send from is not a surface worth serving."""
        user, activity, thread = _seed(db)
        _act_as(user)

        calls = [
            client.get("/api/coach/threads"),
            client.get(f"/api/coach/threads/{thread.id}"),
            client.patch(f"/api/coach/threads/{thread.id}", json={"title": "x"}),
            client.delete(f"/api/coach/threads/{thread.id}"),
            client.post("/api/coach/threads/messages", json={"message": "hi"}),
            client.post("/api/coach/threads/actions/confirm", json={"token": "x"}),
        ]

        for resp in calls:
            assert resp.status_code == 503, resp.request.url
            assert "unavailable" in resp.json()["detail"].lower()

    def test_a_refused_turn_writes_nothing(self, client, db, _threads_off):
        """The decline must land before any row: a stored question with no answer
        is the shape #772 exists to clean up, and the switch must not create it."""
        user, _activity, thread = _seed(db)
        _act_as(user)
        before = db.query(CoachChatMessage).count()

        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "plan my week", "thread_id": str(thread.id)},
        )

        assert resp.status_code == 503
        assert db.query(CoachChatMessage).count() == before
        assert db.query(Thread).count() == 1  # no new thread either

    def test_the_llm_is_never_called(self, client, db, _threads_off):
        user, _activity, _thread = _seed(db)
        _act_as(user)

        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            side_effect=AssertionError("the LLM must not be reached"),
        ):
            resp = client.post("/api/coach/threads/messages", json={"message": "hi"})

        assert resp.status_code == 503

    def test_the_runners_history_is_not_destroyed(self, client, db, _threads_off):
        """Off hides the surface; it does not delete or orphan what was said. The
        activity-scoped history read is a different endpoint and keeps working."""
        user, activity, thread = _seed(db)
        db.add(CoachChatMessage(
            thread_id=thread.id, activity_id=activity.id,
            role="user", content="an earlier question",
        ))
        db.commit()
        _act_as(user)

        resp = client.get(f"/api/activities/{activity.id}/coach-chat")

        assert resp.status_code == 200
        assert resp.json()["messages"][0]["content"] == "an earlier question"


class TestFlagReachesTheFrontend:
    def test_feature_flags_carries_threads(self, client, db):
        user, _activity, _thread = _seed(db)
        _act_as(user)

        assert client.get("/api/coach/feature-flags").json()["threads"] is True

    def test_feature_flags_reflects_the_switch(self, client, db, _threads_off):
        user, _activity, _thread = _seed(db)
        _act_as(user)

        # The flags endpoint itself must NOT be gated by the switch it reports:
        # the client learns the surface is down by asking.
        resp = client.get("/api/coach/feature-flags")
        assert resp.status_code == 200
        assert resp.json()["threads"] is False


def test_default_is_on_so_the_live_path_is_unchanged():
    assert settings.COACH_THREADS_ENABLED is True
