"""Slice 2a of #764 (#766): the thread conversation API.

The runner's threads (ADR 0027): list for the switcher, read one, rename,
hard-delete, and hold a conversation on one from any screen — including a
brand-new thread anchored to nothing. All endpoints are owner-scoped; the
turn assembles a relationship baseline (runner-and-now), never a stored
report pack.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.services.coach.chat import MEDICAL_REDIRECT_MESSAGE
from app.models import Activity, StravaAccount, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from tests._chat_stubs import chat_tool_loop_stub, chat_turn_stub


def _act_as(user):
    """Inject the acting user (the resolution itself is covered elsewhere)."""
    app.dependency_overrides[verify_clerk_session] = lambda: user


def _seed_user(db, *, athlete_id: int = 42) -> User:
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
            strava_athlete_id=athlete_id,
            access_token="t",
            refresh_token="r",
            expires_at=9999999999,
            scope="read",
        )
    )
    db.commit()
    return user


def _seed_activity(db, user: User, *, strava_id: int = 1000) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_id,
        start_date=datetime(2026, 7, 30, 8, 0, 0),
        type="Run",
        name="Morning run",
        distance_m=8200,
        moving_time_s=2850,
        elapsed_time_s=2900,
        elev_gain_m=40.0,
        avg_hr=141,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _seed_thread(db, user: User, *, activity=None, title=None, messages=()):
    thread = Thread(user_id=user.id, activity_id=activity.id if activity else None, title=title)
    db.add(thread)
    db.commit()
    last_at = None
    for i, (role, content) in enumerate(messages):
        created = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
        db.add(
            CoachChatMessage(
                thread_id=thread.id,
                activity_id=activity.id if activity else None,
                role=role,
                content=content,
                created_at=created,
            )
        )
        last_at = created
    thread.last_message_at = last_at
    db.commit()
    db.refresh(thread)
    return thread


class TestThreadList:
    def test_lists_threads_newest_first_with_display_titles(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _seed_thread(
            db,
            user,
            activity=activity,
            title=None,
            messages=[("user", "why was my heart rate high on this one?"), ("assistant", "Heat, mostly.")],
        )
        _seed_thread(
            db,
            user,
            title="Valencia build",
            messages=[("user", "plan my sixteen weeks"), ("assistant", "Sixteen weeks is enough if the base holds.")],
        )

        _act_as(user)
        resp = client.get("/api/coach/threads")
        assert resp.status_code == 200
        threads = resp.json()["threads"]
        assert len(threads) == 2

        # Newest-first by last message; the seeded second thread is newer only by
        # id order here, so assert on content not position where ambiguous.
        by_title = {t["title"]: t for t in threads}
        # A written title wins; an untitled thread falls back to its first user
        # message, trimmed — an untitled thread needs a name the moment it has
        # one turn in it (design spec).
        assert "Valencia build" in by_title
        untitled = [t for t in threads if t["title"] != "Valencia build"][0]
        assert untitled["title"].startswith("why was my heart rate high")

        # Snippet is the last message, for the switcher row.
        assert by_title["Valencia build"]["snippet"].startswith("Sixteen weeks")
        # Anchored thread carries its anchor summary for the chip; unanchored is null.
        assert untitled["anchor"]["activity_id"] == str(activity.id)
        assert untitled["anchor"]["name"] == "Morning run"
        assert by_title["Valencia build"]["anchor"] is None
        assert untitled["last_message_at"] is not None

    def test_excludes_other_users_threads(self, client, db):
        user = _seed_user(db)
        other = User(email=f"other-{uuid4()}@example.com")
        db.add(other)
        db.commit()
        _seed_thread(db, other, title="not yours", messages=[("user", "hi"), ("assistant", "hello")])
        _act_as(user)
        resp = client.get("/api/coach/threads")
        assert resp.status_code == 200
        assert resp.json()["threads"] == []


class TestThreadDetailRenameDelete:
    def test_detail_returns_messages_in_order(self, client, db):
        user = _seed_user(db)
        thread = _seed_thread(
            db, user, messages=[("user", "plan my week"), ("assistant", "Tuesday easy, Thursday quality.")]
        )
        _act_as(user)
        resp = client.get(f"/api/coach/threads/{thread.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"].startswith("plan my week")
        assert [m["content"] for m in data["messages"]] == [
            "plan my week",
            "Tuesday easy, Thursday quality.",
        ]
        assert data["anchor"] is None

    def test_rename_sticks_and_wins_over_fallback(self, client, db):
        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "plan my week"), ("assistant", "ok")])
        _act_as(user)
        resp = client.patch(f"/api/coach/threads/{thread.id}", json={"title": "Valencia build"})
        assert resp.status_code == 204
        assert client.get(f"/api/coach/threads/{thread.id}").json()["title"] == "Valencia build"

    def test_delete_is_hard(self, client, db):
        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "hi"), ("assistant", "hello")])
        _act_as(user)
        resp = client.delete(f"/api/coach/threads/{thread.id}")
        assert resp.status_code == 204
        assert db.query(Thread).count() == 0
        assert db.query(CoachChatMessage).count() == 0

    def test_cross_user_thread_is_404_everywhere(self, client, db):
        user = _seed_user(db)
        other = User(email=f"other-{uuid4()}@example.com")
        db.add(other)
        db.commit()
        thread = _seed_thread(db, other, messages=[("user", "hi"), ("assistant", "hello")])
        _act_as(user)
        assert client.get(f"/api/coach/threads/{thread.id}").status_code == 404
        assert (
            client.patch(f"/api/coach/threads/{thread.id}", json={"title": "x"}).status_code
            == 404
        )
        assert client.delete(f"/api/coach/threads/{thread.id}").status_code == 404
        # And nothing was touched.
        assert db.query(Thread).count() == 1
        assert db.query(CoachChatMessage).count() == 2


def _parse_sse(raw: str):
    """Reconstruct (text, objects) from the SSE wire the way the frontend does."""
    text_parts, objects = [], []
    for event in raw.split("\n\n"):
        for line in event.split("\n"):
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload == "[DONE]":
                continue
            parsed = json.loads(payload)
            if isinstance(parsed, str):
                text_parts.append(parsed)
            else:
                objects.append(parsed)
    return "".join(text_parts), objects


class TestThreadTurn:
    def test_first_message_creates_unanchored_thread_and_streams(self, client, db):
        user = _seed_user(db)
        _act_as(user)
        capture = {}
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(["Not by ", "your own history."], capture=capture),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "am I ramping too fast?", "asked_from": "load"},
            )
        assert resp.status_code == 200
        text, objects = _parse_sse(resp.text)
        assert text == "Not by your own history."

        # The route announces the (created) thread so the client can adopt it.
        thread_frames = [o for o in objects if o.get("type") == "thread"]
        assert len(thread_frames) == 1

        thread = db.query(Thread).one()
        assert str(thread.id) == thread_frames[0]["thread_id"]
        assert thread.user_id == user.id
        assert thread.activity_id is None
        assert thread.last_message_at is not None

        rows = db.query(CoachChatMessage).all()
        assert sorted(r.role for r in rows) == ["assistant", "user"]
        assert all(r.thread_id == thread.id for r in rows)
        assert all(r.activity_id is None for r in rows)
        assert all(r.asked_from == "load" for r in rows)

        # The turn assembles a relationship baseline (runner-and-now), not a
        # stored report pack: the runner's profile reaches the coach.
        assert "intermediate" in capture["system"]

    def test_second_message_continues_the_thread(self, client, db):
        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "plan my week"), ("assistant", "Easy Tuesday.")])
        _act_as(user)
        capture = {}
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(["Thursday quality."], capture=capture),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "and thursday?", "thread_id": str(thread.id)},
            )
        assert resp.status_code == 200
        assert db.query(Thread).count() == 1
        assert db.query(CoachChatMessage).count() == 4

    def test_anchored_creation_dual_writes_activity(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(["Steady."]),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={
                    "message": "how was this one?",
                    "anchor_activity_id": str(activity.id),
                    "asked_from": "activity",
                },
            )
        assert resp.status_code == 200
        thread = db.query(Thread).one()
        assert thread.activity_id == activity.id
        rows = db.query(CoachChatMessage).all()
        # Dual-write: anchored thread turns keep activity_id populated so the
        # activity-scoped readers see them (slice 1 rule).
        assert all(r.activity_id == activity.id for r in rows)

    def test_cross_user_thread_or_anchor_is_denied_before_any_stream(self, client, db):
        user = _seed_user(db)
        other = User(email=f"other-{uuid4()}@example.com")
        db.add(other)
        db.commit()
        other_thread = _seed_thread(db, other, messages=[("user", "hi"), ("assistant", "hey")])
        other_activity = _seed_activity(db, other, strava_id=2000)
        _act_as(user)
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "x", "thread_id": str(other_thread.id)},
        )
        assert resp.status_code == 404
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": "x", "anchor_activity_id": str(other_activity.id)},
        )
        assert resp.status_code == 404
        # Nothing was written for the acting user.
        assert db.query(Thread).filter(Thread.user_id == user.id).count() == 0

    def test_spend_cap_declines_with_direction_and_writes_nothing(self, client, db):
        user = _seed_user(db)
        _act_as(user)
        with patch("app.services.coach.budget.over_budget", return_value=True):
            resp = client.post(
                "/api/coach/threads/messages", json={"message": "how did last week go?"}
            )
        assert resp.status_code == 200
        text, _ = _parse_sse(resp.text)
        # The cap answer says what still works, not silence.
        assert "coaching allowance" in text
        assert "still sync" in text
        # A declined turn is an answer, not a conversation: nothing persisted.
        assert db.query(Thread).count() == 0
        assert db.query(CoachChatMessage).count() == 0

    def test_llm_history_is_bounded_but_scrollback_is_not(self, client, db):
        user = _seed_user(db)
        many = [("user", f"q{i}") if i % 2 == 0 else ("assistant", f"a{i}") for i in range(60)]
        thread = _seed_thread(db, user, messages=many)
        _act_as(user)
        seen = {}

        def capturing_stub(deltas):
            from app.services.coach.llm import ChatTurnDelta, MessageResult

            async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
                seen["messages"] = messages
                yield ChatTurnDelta(
                    final=MessageResult(
                        content_blocks=[{"type": "text", "text": "".join(deltas)}],
                        stop_reason="end_turn",
                    )
                )

            return _stub

        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=capturing_stub(["ok"]),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "one more", "thread_id": str(thread.id)},
            )
        assert resp.status_code == 200
        from app.services.coach.thread_turn import _MAX_LLM_HISTORY_TURNS

        assert len(seen["messages"]) == _MAX_LLM_HISTORY_TURNS
        # The newest turn (the one just sent) is included.
        assert seen["messages"][-1]["content"] == "one more"
        # Scrollback is untouched: full history still served.
        detail = client.get(f"/api/coach/threads/{thread.id}").json()
        assert len(detail["messages"]) == 62

    def test_thread_turn_streams_a_proposed_action_frame(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)

        class _FakeRedis:
            def __init__(self):
                self._store = {}

            def set(self, key, value, ex=None):
                self._store[key] = value
                return True

            def getdel(self, key):
                return self._store.pop(key, None)

        fake_redis = _FakeRedis()
        rounds = [
            [
                {
                    "name": "offer_proposed_action",
                    "input": {
                        "action_type": "intent",
                        "activity_id": str(activity.id),
                        "user_intent": "Tempo",
                    },
                }
            ],
            "That fits better.",
        ]
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_tool_loop_stub(rounds),
        ), patch("app.services.coach.proposed_actions.redis_conn", fake_redis):
            resp = client.post(
                "/api/coach/threads/messages",
                json={
                    "message": "this wasn't easy",
                    "anchor_activity_id": str(activity.id),
                    "asked_from": "activity",
                },
            )

        assert resp.status_code == 200
        text, objects = _parse_sse(resp.text)
        assert text == "That fits better."
        actions = [o for o in objects if o.get("type") == "proposed_action"]
        assert len(actions) == 1
        assert actions[0]["action_type"] == "intent"
        assert actions[0]["confirm_label"] == "Set it"
        assert "Mark" in actions[0]["description"]
        assert actions[0]["token"]

    def test_a_reply_gated_for_medical_overreach_ships_no_proposed_action(
        self, client, db
    ):
        """#787: the turn's reasoning was judged unsafe and its prose withheld, so
        an offer minted during that same reasoning does not survive it. The runner
        would otherwise get the safe redirect followed by an unexplained card
        offering to write to their record: the prose that would have explained the
        offer is exactly what the floor removed. The report path sets the
        precedent, forcing `is_fallback=True` for the whole artifact rather than
        trimming part of it."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)

        class _FakeRedis:
            def __init__(self):
                self._store = {}

            def set(self, key, value, ex=None):
                self._store[key] = value
                return True

            def getdel(self, key):
                return self._store.pop(key, None)

        fake_redis = _FakeRedis()
        rounds = [
            [
                {
                    "name": "offer_proposed_action",
                    "input": {
                        "action_type": "intent",
                        "activity_id": str(activity.id),
                        "user_intent": "Tempo",
                    },
                }
            ],
            "That knee pain is patellar tendinopathy. Take 400mg of ibuprofen first.",
        ]
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_tool_loop_stub(rounds),
        ), patch("app.services.coach.proposed_actions.redis_conn", fake_redis):
            resp = client.post(
                "/api/coach/threads/messages",
                json={
                    "message": "knee hurts, was this a tempo?",
                    "anchor_activity_id": str(activity.id),
                    "asked_from": "activity",
                },
            )

        assert resp.status_code == 200
        text, objects = _parse_sse(resp.text)

        # The floor still works: unsafe prose is replaced, not merely flagged.
        assert "patellar" not in text
        assert "ibuprofen" not in text
        assert MEDICAL_REDIRECT_MESSAGE in text

        # And the offer minted in the same turn does not ride along behind it.
        assert [o for o in objects if o.get("type") == "proposed_action"] == []

    def test_a_loaded_skill_is_recorded_on_the_turn_and_costs_no_lookup(
        self, client, db
    ):
        """#769: the procedure rides the same round as the fetch beside it, and
        the assistant row records which procedure the turn ran to."""
        user = _seed_user(db)
        _seed_activity(db, user)
        _act_as(user)

        capture = {}
        rounds = [
            [
                {"name": "load_coaching_skill", "input": {"name": "plan_the_week"}},
                {
                    "name": "get_training_summary",
                    "input": {"window": "last_7_days"},
                },
            ],
            "Three easy runs, hold the long run where it is.",
        ]
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_tool_loop_stub(rounds, capture=capture),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "plan my week", "asked_from": "home"},
            )

        assert resp.status_code == 200
        text, objects = _parse_sse(resp.text)
        assert "Three easy runs" in text

        # The load is not a lookup: no status affordance, no trace chip for it.
        traces = [o for o in objects if o.get("type") == "tool_trace"]
        assert all(t["entry"]["tool"] != "load_coaching_skill" for t in traces)
        assert any(t["entry"]["tool"] == "get_training_summary" for t in traces)

        # And the procedure reached the model in the same round as the fetch.
        assert len(capture["tools_seen"]) == 2

        row = (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == "assistant")
            .order_by(CoachChatMessage.created_at.desc())
            .first()
        )
        assert row.skills_used == ["plan_the_week"]

    def test_a_skilled_turn_does_not_lower_the_safety_floor(self, client, db):
        """ADR 0029: a skill cannot widen what the coach may write. A reply that
        overreaches is withheld whether or not a procedure shaped the turn."""
        user = _seed_user(db)
        _seed_activity(db, user)
        _act_as(user)

        rounds = [
            [{"name": "load_coaching_skill", "input": {"name": "plan_the_week"}}],
            "That knee pain is patellar tendinopathy. Take 400mg of ibuprofen first.",
        ]
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_tool_loop_stub(rounds),
        ):
            resp = client.post(
                "/api/coach/threads/messages",
                json={"message": "knee hurts, plan my week", "asked_from": "home"},
            )

        text, _objects = _parse_sse(resp.text)
        assert "patellar" not in text
        assert "ibuprofen" not in text
        assert MEDICAL_REDIRECT_MESSAGE in text

    def test_confirm_endpoint_executes_and_is_single_use(self, client, db):
        from app.services.coach import proposed_actions

        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)

        class _FakeRedis:
            def __init__(self):
                self._store = {}

            def set(self, key, value, ex=None):
                self._store[key] = value
                return True

            def getdel(self, key):
                return self._store.pop(key, None)

        fake_redis = _FakeRedis()
        with patch.object(proposed_actions, "redis_conn", fake_redis):
            _result, frame = proposed_actions.mint_proposed_action(
                db,
                user.id,
                {
                    "action_type": "intent",
                    "activity_id": str(activity.id),
                    "user_intent": "Tempo",
                },
            )
            token = frame["token"]
            with patch("app.services.intents.analysis.analyze"):
                resp = client.post(
                    "/api/coach/threads/actions/confirm",
                    json={"token": token},
                )
            assert resp.status_code == 200
            db.refresh(activity)
            assert activity.user_intent == "Tempo"

            again = client.post(
                "/api/coach/threads/actions/confirm",
                json={"token": token},
            )
            assert again.status_code == 404


class TestThreadMaintenance:
    def test_first_exchange_enqueues_title_generation(self, client, db):
        user = _seed_user(db)
        _act_as(user)
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(["A reply."]),
        ), patch("app.core.queue.queue") as queue_mock:
            resp = client.post("/api/coach/threads/messages", json={"message": "plan my week"})
        assert resp.status_code == 200
        from app.jobs.thread_maintenance import generate_thread_title_job, thread_quiet_job

        enqueued = [c.args[0] for c in queue_mock.enqueue.call_args_list]
        assert generate_thread_title_job in enqueued
        # The quiet-thread debounce is armed too.
        deferred = [c.args[1] for c in queue_mock.enqueue_in.call_args_list]
        assert thread_quiet_job in deferred

    def test_second_exchange_does_not_reenqueue_title(self, client, db):
        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "q"), ("assistant", "a")])
        _act_as(user)
        with patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_turn_stub(["Another."]),
        ), patch("app.core.queue.queue") as queue_mock:
            client.post(
                "/api/coach/threads/messages",
                json={"message": "more", "thread_id": str(thread.id)},
            )
        from app.jobs.thread_maintenance import generate_thread_title_job

        enqueued = [c.args[0] for c in queue_mock.enqueue.call_args_list]
        assert generate_thread_title_job not in enqueued

    def test_title_job_writes_title_and_never_overwrites_runner_title(self, db):
        from app.jobs import thread_maintenance

        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "should I race Valencia?"), ("assistant", "Let's see.")])

        async def fake_gen(self, *, system, user, max_tokens=1024):
            from app.services.coach.llm import Usage

            return "Valencia decision", Usage(input_tokens=10, output_tokens=5)

        with patch("app.services.coach.llm.AnthropicClient.generate_json_with_usage", new=fake_gen), patch.object(
            thread_maintenance, "SessionLocal", return_value=db
        ), patch.object(db, "close"):
            thread_maintenance.generate_thread_title_job(str(thread.id))
        db.refresh(thread)
        assert thread.title == "Valencia decision"

        # A runner-written title is never overwritten.
        thread.title = "My own name"
        db.commit()
        with patch("app.services.coach.llm.AnthropicClient.generate_json_with_usage", new=fake_gen), patch.object(
            thread_maintenance, "SessionLocal", return_value=db
        ), patch.object(db, "close"):
            thread_maintenance.generate_thread_title_job(str(thread.id))
        db.refresh(thread)
        assert thread.title == "My own name"

    def test_quiet_job_fires_memory_pass_only_when_actually_quiet(self, db):
        from app.jobs import thread_maintenance

        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "q"), ("assistant", "a")])

        # Not quiet: count moved on since the check was armed.
        with patch.object(thread_maintenance, "SessionLocal", return_value=db), patch.object(
            db, "close"
        ), patch("app.core.queue.queue") as queue_mock, patch(
            "app.jobs.batch_chain.acquire_enqueue_slot", return_value=True
        ):
            thread_maintenance.thread_quiet_job(str(thread.id), 1)
            assert queue_mock.enqueue.call_count == 0

            # Quiet: counts match -> one memory pass enqueued.
            thread_maintenance.thread_quiet_job(str(thread.id), 2)
            from app.jobs.memory_update import update_memory_job

            assert queue_mock.enqueue.call_args.args[0] is update_memory_job

    def test_quiet_job_respects_per_runner_cooldown(self, db):
        from app.jobs import thread_maintenance

        user = _seed_user(db)
        thread = _seed_thread(db, user, messages=[("user", "q"), ("assistant", "a")])
        with patch.object(thread_maintenance, "SessionLocal", return_value=db), patch.object(
            db, "close"
        ), patch("app.core.queue.queue") as queue_mock, patch(
            "app.jobs.batch_chain.acquire_enqueue_slot", return_value=False
        ):
            thread_maintenance.thread_quiet_job(str(thread.id), 2)
            assert queue_mock.enqueue.call_count == 0


class TestChatModelConfig:
    def test_chat_model_defaults_to_coach_model(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "COACH_CHAT_MODEL_ID", "")
        assert settings.chat_model_id == settings.COACH_MODEL_ID

    def test_chat_model_override_wins(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "COACH_CHAT_MODEL_ID", "claude-haiku-4-5")
        assert settings.chat_model_id == "claude-haiku-4-5"
