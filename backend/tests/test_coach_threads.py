"""Slice 1 of #764 (#765): chat reads and writes through the Thread unit.

A `Thread` (CONTEXT.md) is runner-initiated, relationship-scoped, and optionally
anchored to an activity (ADR 0027). This slice changes nothing the runner sees:
the activity chat box keeps working, but its rows now belong to the thread
anchored to that activity, and each turn records provenance (where it was asked
from, tools run, skill loaded).

These tests pin the write-through / read-through behaviour at the public API and
service seams. The alembic backfill itself is verified against real
pre-migration data on a seeded local Postgres (the Migrate oracle), not here —
the unit suite runs on create_all and never executes migrations.

All row data below is synthetic test setup (exercises code paths; represents no
real runner).
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, StravaAccount, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from tests._chat_stubs import chat_turn_stub


def _seed_activity(db, *, strava_id: int = 42) -> Activity:
    user = User(email=f"u-{uuid4()}@example.com")
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
    db.add(
        StravaAccount(
            user_id=user.id,
            strava_athlete_id=strava_id,
            access_token="t",
            refresh_token="r",
            expires_at=9999999999,
            scope="read",
        )
    )
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_id,
        start_date=datetime(2026, 5, 27, 10, 0, 0),
        type="Run",
        name="Test run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=10.0,
        avg_hr=140,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _post_chat(client, activity, message: str = "how was my pacing?", thread_id=None):
    body = {"message": message, "asked_from": "activity"}
    if thread_id is None:
        body["anchor_activity_id"] = str(activity.id)
    else:
        body["thread_id"] = str(thread_id)
    with patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=chat_turn_stub(["Looked ", "steady."]),
    ):
        resp = client.post("/api/coach/threads/messages", json=body)
    assert resp.status_code == 200
    # Drain the SSE body so the generator runs to completion and persists rows.
    resp.read()
    return resp


class TestTurnsWriteThroughThread:
    def test_first_message_creates_activity_anchored_thread(self, client, db):
        activity = _seed_activity(db)

        _post_chat(client, activity)

        threads = db.query(Thread).all()
        assert len(threads) == 1
        thread = threads[0]
        assert thread.user_id == activity.user_id
        assert thread.activity_id == activity.id
        assert thread.last_message_at is not None

        rows = db.query(CoachChatMessage).all()
        # One user turn, one assistant turn (ordering is not asserted here:
        # SQLite's second-precision CURRENT_TIMESTAMP ties same-second rows).
        assert sorted(r.role for r in rows) == ["assistant", "user"]
        assert all(r.thread_id == thread.id for r in rows)
        # Every turn records where it was asked from (ADR 0028: past turns
        # retain the label only).
        assert all(r.asked_from == "activity" for r in rows)
        # Dual-write: activity_id stays populated so the activity-scoped readers
        # (memory sources, adherence pushback, fuller-turn continuity, and the
        # activity-scoped history read) keep working.
        assert all(r.activity_id == activity.id for r in rows)

    def test_continuing_the_thread_keeps_one_conversation(self, client, db):
        """The surface carries the thread id forward, so a follow-up lands in the
        same conversation rather than starting a second one on the same run."""
        activity = _seed_activity(db)

        _post_chat(client, activity, "first question")
        thread = db.query(Thread).one()
        _post_chat(client, activity, "second question", thread_id=thread.id)

        assert db.query(Thread).count() == 1
        thread = db.query(Thread).first()
        rows = db.query(CoachChatMessage).all()
        assert len(rows) == 4
        assert all(r.thread_id == thread.id for r in rows)


class TestHistoryReadsThroughThread:
    def test_legacy_orphan_rows_stay_visible_and_are_adopted(self, client, db):
        """Rows written by pre-thread code (thread_id null) must never vanish
        from the chat box: the read path materialises the thread and adopts
        them (the #515 reconcile idiom)."""
        activity = _seed_activity(db)
        for role, content in [("user", "old question"), ("assistant", "old answer")]:
            db.add(
                CoachChatMessage(
                    activity_id=activity.id, role=role, content=content
                )
            )
        db.commit()

        resp = client.get(f"/api/activities/{activity.id}/coach-chat")
        assert resp.status_code == 200
        contents = [m["content"] for m in resp.json()["messages"]]
        assert set(contents) == {"old question", "old answer"}

        thread = db.query(Thread).one()
        assert thread.user_id == activity.user_id
        assert thread.activity_id == activity.id
        assert all(m.thread_id == thread.id for m in db.query(CoachChatMessage).all())

    def test_no_chat_means_no_history_and_no_thread(self, client, db):
        activity = _seed_activity(db)

        resp = client.get(f"/api/activities/{activity.id}/coach-chat")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []
        assert db.query(Thread).count() == 0


class TestDeleteClearsThread:
    def test_delete_endpoint_removes_thread_and_messages(self, client, db):
        activity = _seed_activity(db)
        _post_chat(client, activity)
        assert db.query(Thread).count() == 1

        resp = client.delete(f"/api/activities/{activity.id}/coach-chat")
        assert resp.status_code == 204
        assert db.query(CoachChatMessage).count() == 0
        assert db.query(Thread).count() == 0

    def test_delete_endpoint_also_clears_orphan_rows(self, client, db):
        activity = _seed_activity(db)
        db.add(CoachChatMessage(activity_id=activity.id, role="user", content="legacy"))
        db.commit()

        resp = client.delete(f"/api/activities/{activity.id}/coach-chat")
        assert resp.status_code == 204
        assert db.query(CoachChatMessage).count() == 0
        assert db.query(Thread).count() == 0
