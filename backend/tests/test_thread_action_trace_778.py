"""A confirmed proposed action leaves a trace in the thread (#778).

The bug was multi-turn: the card is a stream frame, not a message row, so
confirming wrote to the runner's record and nothing to the conversation. On the
next turn the coach could not see what the runner had accepted, and re-offered
things already done.

So these tests assert against the NEXT turn's actually-assembled prompt and
message array, not against a row in the database. A row that no turn reads would
pass a storage assertion and fix nothing.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models import Activity, User, UserProfile
from app.models.coach_chat_message import CoachChatMessage
from app.models.thread import Thread
from app.services.coach import proposed_actions
from app.services.coach import threads as thread_service
from app.services.coach.chat import MEDICAL_REDIRECT_MESSAGE
from tests._chat_stubs import chat_tool_loop_stub

T0 = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _act_as(user):
    app.dependency_overrides[verify_clerk_session] = lambda: user


def _seed_user(db) -> User:
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
    db.commit()
    return user


def _seed_activity(db, user: User, *, strava_id: int = 5000) -> Activity:
    activity = Activity(
        user_id=user.id,
        strava_activity_id=strava_id,
        start_date=datetime(2026, 7, 27, 8, 0, 0),
        type="Run",
        name="Morning run",
        distance_m=9000,
        moving_time_s=3000,
        elapsed_time_s=3050,
        elev_gain_m=30.0,
        avg_hr=140,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _intent_offer_rounds(activity, label="Long Run"):
    """One tool round that offers the intent change, then the coach's reply."""
    return [
        [
            {
                "name": "offer_proposed_action",
                "input": {
                    "action_type": "intent",
                    "activity_id": str(activity.id),
                    "user_intent": label,
                },
            }
        ],
        "That reads like a long run to me.",
    ]


def _offer_and_confirm(client, db, user, activity, fake_redis, *, thread_id=None):
    """Drive a real turn that offers an intent change, then confirm it.

    Returns (thread_id, token). Everything goes through the API, so the thread
    the offer belongs to is the one the tool loop supplied, never a test fixture.
    """
    payload = {"message": "was monday a long run?"}
    if thread_id is not None:
        payload["thread_id"] = thread_id
    with patch.object(proposed_actions, "redis_conn", fake_redis), patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=chat_tool_loop_stub(_intent_offer_rounds(activity)),
    ):
        resp = client.post("/api/coach/threads/messages", json=payload)
    assert resp.status_code == 200

    token = None
    resolved_thread_id = thread_id
    for line in resp.text.split("\n"):
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        parsed = json.loads(line[len("data: "):])
        if isinstance(parsed, dict) and parsed.get("type") == "proposed_action":
            token = parsed["token"]
        if isinstance(parsed, dict) and parsed.get("type") == "thread":
            resolved_thread_id = parsed["thread_id"]
    assert token, "the turn should have offered an action"

    with patch.object(proposed_actions, "redis_conn", fake_redis), patch(
        "app.services.intents.analysis.analyze"
    ):
        confirm = client.post(
            "/api/coach/threads/actions/confirm", json={"token": token}
        )
    assert confirm.status_code == 200
    return resolved_thread_id, token


def _sse_text(raw: str) -> str:
    """The reply the client would render, reassembled from its stream slices."""
    parts = []
    for line in raw.split("\n"):
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        parsed = json.loads(line[len("data: "):])
        if isinstance(parsed, str):
            parts.append(parsed)
    return "".join(parts)


def _capturing_stub(reply: str, seen: dict):
    from app.services.coach.llm import ChatTurnDelta, MessageResult

    async def _stub(self, *, system, messages, tools=None, max_tokens=1024):
        seen["system"] = system
        seen["messages"] = [dict(m) for m in messages]
        yield ChatTurnDelta(
            final=MessageResult(
                content_blocks=[{"type": "text", "text": reply}],
                stop_reason="end_turn",
            )
        )

    return _stub


def _next_turn(client, message: str, thread_id: str, seen: dict, reply="Sure."):
    with patch(
        "app.services.coach.llm.AnthropicClient.stream_chat_turn",
        new=_capturing_stub(reply, seen),
    ):
        resp = client.post(
            "/api/coach/threads/messages",
            json={"message": message, "thread_id": str(thread_id)},
        )
    assert resp.status_code == 200
    return resp


class TestTheNextTurnCanSeeIt:
    def test_a_confirmed_action_reaches_the_next_turns_assembled_prompt(
        self, client, db
    ):
        """The bug itself: the coach offered, the runner confirmed, and the next
        turn was assembled as if nothing had happened."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        thread_id, _token = _offer_and_confirm(
            client, db, user, activity, fake_redis
        )

        seen = {}
        _next_turn(client, "actually scrap that, it was an easy run", thread_id, seen)

        system = seen["system"]
        assert "ALREADY IN THEIR RECORD" in system
        # The card's own words, so what the coach reads is what the runner agreed
        # to rather than a second description of it. This is the live #778
        # sentence: "Mark Monday's 9.0 km run as Long Run".
        assert "Mark Monday's 9.0 km run as Long Run" in system
        # And it is unmistakably a done change, not something still on offer.
        assert "coach from them as facts" in system

    def test_the_trace_is_never_sent_as_a_conversational_turn(self, client, db):
        """The transcript and the model's history are not the same thing. An
        action event is neither side speaking, so it must not arrive as a turn
        wearing a role the Messages API has never heard of."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        thread_id, _token = _offer_and_confirm(
            client, db, user, activity, fake_redis
        )

        seen = {}
        _next_turn(client, "and the week?", thread_id, seen)

        roles = {m["role"] for m in seen["messages"]}
        assert roles <= {"user", "assistant"}, roles
        # It is in the thread, though — this is the same thread that just proved
        # it, so a filter that dropped the row entirely could not pass both.
        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 1
        )

    def test_the_runner_reads_it_in_their_scrollback(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        thread_id, _token = _offer_and_confirm(
            client, db, user, activity, fake_redis
        )

        detail = client.get(f"/api/coach/threads/{thread_id}").json()
        events = [m for m in detail["messages"] if m["role"] == "event"]
        assert len(events) == 1
        assert "Long Run" in events[0]["content"]

    def test_an_untouched_conversation_carries_no_ledger(self, client, db):
        """Byte-stable when nothing was confirmed: the block is absent, not an
        empty heading the coach has to interpret."""
        user = _seed_user(db)
        _act_as(user)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()

        seen = {}
        _next_turn(client, "how was my week?", thread.id, seen)
        assert "ALREADY IN THEIR RECORD" not in seen["system"]


class TestOnlyRealWritesLeaveATrace:
    def test_an_offer_that_is_never_confirmed_leaves_nothing(self, client, db):
        """A dismissal changes nothing in the runner's record, so it is not a
        thing that happened to their record. Recording declines is also how a
        coach ends up narrating compliance, which is the failure ADR 0025 exists
        to remove."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        with patch.object(proposed_actions, "redis_conn", fake_redis), patch(
            "app.services.coach.llm.AnthropicClient.stream_chat_turn",
            new=chat_tool_loop_stub(_intent_offer_rounds(activity)),
        ):
            resp = client.post(
                "/api/coach/threads/messages", json={"message": "was it long?"}
            )
        assert resp.status_code == 200
        # The card went up and was left alone.
        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 0
        )

    def test_an_expired_or_spent_token_writes_no_second_trace(self, client, db):
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        _thread_id, token = _offer_and_confirm(
            client, db, user, activity, fake_redis
        )

        with patch.object(proposed_actions, "redis_conn", fake_redis):
            again = client.post(
                "/api/coach/threads/actions/confirm", json={"token": token}
            )
        assert again.status_code == 404
        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 1
        )

    def test_a_confirm_whose_write_is_refused_leaves_no_trace(self, db, monkeypatch):
        """The trace records writes, not taps. A token stays valid for half an
        hour, so the world can move between the card going up and the runner
        tapping it — here the schedule's kill switch is thrown in between. The
        confirm is refused, and a conversation that says the plan was written
        would be worse than one that says nothing."""
        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        fake_redis = _FakeRedis()

        monkeypatch.setattr(
            "app.services.coach.proposed_actions.settings.SCHEDULE_ENABLED", True
        )
        with patch.object(proposed_actions, "redis_conn", fake_redis):
            result, frame = proposed_actions.mint_proposed_action(
                db, user.id, {"action_type": "draft_plan"}, thread_id=thread.id
            )
            assert result["ok"] is True, result
            monkeypatch.setattr(
                "app.services.coach.proposed_actions.settings.SCHEDULE_ENABLED",
                False,
            )
            try:
                proposed_actions.consume_and_execute(db, user.id, frame["token"])
                assert False, "a refused confirm should raise"
            except ValueError:
                pass

        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 0
        )

    def test_a_cross_user_confirm_writes_neither_change_nor_trace(self, db):
        """Ownership is unchanged, and the trace does not become a way around
        it: a stranger's redeem must leave no mark in the owner's thread."""
        user = _seed_user(db)
        stranger = _seed_user(db)
        activity = _seed_activity(db, user)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        fake_redis = _FakeRedis()

        with patch.object(proposed_actions, "redis_conn", fake_redis):
            _result, frame = proposed_actions.mint_proposed_action(
                db,
                user.id,
                {
                    "action_type": "intent",
                    "activity_id": str(activity.id),
                    "user_intent": "Long Run",
                },
                thread_id=thread.id,
            )
            try:
                proposed_actions.consume_and_execute(
                    db, stranger.id, frame["token"]
                )
                assert False, "cross-user confirm should not execute"
            except LookupError:
                pass

        db.refresh(activity)
        assert activity.user_intent is None
        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 0
        )

    def test_a_token_minted_before_this_shipped_still_confirms(self, db):
        """A token already in Redis at deploy time carries no thread and no
        wording. The change must still be made; only the trace is lost."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        fake_redis = _FakeRedis()

        legacy = proposed_actions.StoredProposedAction(
            owner_user_id=user.id,
            action_type="intent",
            activity_id=activity.id,
            user_intent="Long Run",
        )
        with patch.object(proposed_actions, "redis_conn", fake_redis):
            token = proposed_actions._mint_token(user.id, legacy)
            with patch("app.services.intents.analysis.analyze"):
                result = proposed_actions.consume_and_execute(db, user.id, token)

        assert result["action_type"] == "intent"
        db.refresh(activity)
        assert activity.user_intent == "Long Run"
        assert (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .count()
            == 0
        )


class TestTheLedgerIsBounded:
    def test_it_keeps_the_most_recent_and_stays_chronological(self, db):
        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        cap = thread_service._MAX_LEDGER_EVENTS
        for i in range(cap + 4):
            db.add(
                CoachChatMessage(
                    thread_id=thread.id,
                    role=thread_service.ACTION_EVENT_ROLE,
                    content=f"change {i}",
                    created_at=T0 + timedelta(minutes=i),
                )
            )
        db.commit()

        events = thread_service.recent_action_events(db, thread.id)
        assert len(events) == cap
        assert events[0] == f"change {4}"
        assert events[-1] == f"change {cap + 3}"

    def test_another_conversations_confirmations_are_not_in_this_ledger(self, db):
        user = _seed_user(db)
        here = Thread(user_id=user.id)
        elsewhere = Thread(user_id=user.id)
        db.add_all([here, elsewhere])
        db.commit()
        db.add(
            CoachChatMessage(
                thread_id=elsewhere.id,
                role=thread_service.ACTION_EVENT_ROLE,
                content="OTHERTHREAD change",
            )
        )
        db.commit()

        assert thread_service.recent_action_events(db, here.id) == []


class TestTheTraceDisturbsNothingElse:
    def test_the_safety_floor_still_runs_over_a_turn_carrying_a_ledger(
        self, client, db
    ):
        """The ledger is context, never a licence. A reply that oversteps medical
        scope is still withheld on a turn whose prompt carries confirmations."""
        user = _seed_user(db)
        activity = _seed_activity(db, user)
        _act_as(user)
        fake_redis = _FakeRedis()

        thread_id, _token = _offer_and_confirm(
            client, db, user, activity, fake_redis
        )

        seen = {}
        resp = _next_turn(
            client,
            "my knee hurts",
            thread_id,
            seen,
            reply="You have patellar tendinopathy. Take 400mg of ibuprofen twice a day.",
        )
        assert "ALREADY IN THEIR RECORD" in seen["system"]
        served = _sse_text(resp.text)
        assert "patellar" not in served
        assert "ibuprofen" not in served
        assert served == MEDICAL_REDIRECT_MESSAGE

    def test_a_confirmation_is_not_the_switcher_snippet_or_the_thread_title(
        self, client, db
    ):
        """The switcher names what the conversation is about, which is what was
        said in it.

        Timestamps are explicit and the event is unambiguously the NEWEST row,
        because the snippet is "the last message": leaving the rows to share a
        commit-time default would decide this on a UUID tiebreak and the check
        would pass whether the filter existed or not.
        """
        user = _seed_user(db)
        _act_as(user)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        rows = [
            ("user", "was monday a long run?", 0),
            ("assistant", "It reads like one to me.", 1),
            (thread_service.ACTION_EVENT_ROLE, "Mark Monday's 9.0 km run as Long Run", 2),
        ]
        for role, content, minute in rows:
            db.add(
                CoachChatMessage(
                    thread_id=thread.id,
                    role=role,
                    content=content,
                    created_at=T0 + timedelta(minutes=minute),
                )
            )
        thread.last_message_at = T0 + timedelta(minutes=2)
        db.commit()

        listed = client.get("/api/coach/threads").json()["threads"]
        assert len(listed) == 1
        assert listed[0]["snippet"] == "It reads like one to me."
        assert listed[0]["title"] == "was monday a long run?"

    def test_a_confirmation_does_not_disarm_the_quiet_memory_debounce(self, db):
        """The debounce compares a turn count taken when it was armed against the
        count when it fires. A confirmation tapped in between must not read as
        the thread still being busy, or the memory pass never happens."""
        from app.jobs import thread_maintenance

        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        db.add_all(
            [
                CoachChatMessage(thread_id=thread.id, role="user", content="hi"),
                CoachChatMessage(
                    thread_id=thread.id, role="assistant", content="hello"
                ),
            ]
        )
        db.commit()

        armed = thread_maintenance._conversational_count(db, thread)
        db.add(
            CoachChatMessage(
                thread_id=thread.id,
                role=thread_service.ACTION_EVENT_ROLE,
                content="Mark Morning run as Long Run",
            )
        )
        db.commit()

        assert thread_maintenance._conversational_count(db, thread) == armed


class _NoCloseSession:
    """The test session, lent to a job that owns its own and closes it.

    Everything delegates; `close` does not, because the fixture still needs the
    session afterwards to assert against.
    """

    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def close(self):
        return None


class TestADeferredWriteIsNotRecordedUntilItHappens:
    """#778's contract is that the ledger records WRITES, not taps: "written only
    after the change has actually been made".

    Two of the nine actions do not write when they are confirmed. `draft_plan`
    and `amend_plan` hand the work to the worker and return, so recording at
    confirm time records an intention as an outcome. The coach then reads it back
    under "ALREADY IN THEIR RECORD - what this conversation has written" and
    coaches from a change that has not happened, and may never happen.

    Observed live: a runner confirmed a hill session into next week, the
    transcript showed it done, the job sat unprocessed in the queue, and the week
    held no hill session.
    """

    def _plan(self, db, user):
        from app.models.training_plan import TrainingPlan

        plan = TrainingPlan(
            user_id=user.id, status="active", rules=[], week_shapes=[]
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    def _stored_amendment(self, user, thread, plan):
        return proposed_actions.StoredProposedAction(
            owner_user_id=user.id,
            action_type="amend_plan",
            weeks_from=1,
            weeks_through=1,
            amend_reason="replace one easy run with a hill rep session",
            plan_id_at_offer=plan.id,
            thread_id=thread.id,
            description="Replace one easy run with a hill rep session (31 Aug to 6 Sep).",
        )

    def _events(self, db):
        return (
            db.query(CoachChatMessage)
            .filter(CoachChatMessage.role == thread_service.ACTION_EVENT_ROLE)
            .all()
        )

    def test_confirming_an_amendment_records_nothing_until_the_work_lands(
        self, db
    ):
        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
        plan = self._plan(db, user)
        fake_redis = _FakeRedis()
        stored = self._stored_amendment(user, thread, plan)

        with patch.object(proposed_actions, "redis_conn", fake_redis), patch(
            "app.jobs.amend_schedule.enqueue_amendment"
        ) as enqueue:
            token = proposed_actions._mint_token(user.id, stored)
            result = proposed_actions.consume_and_execute(db, user.id, token)

        assert result["action_type"] == "amend_plan"
        assert enqueue.called, "the confirm must still hand the work to the worker"
        # The change has been ASKED FOR. Nothing has been written.
        assert self._events(db) == []

    def test_the_amendment_job_records_the_trace_once_it_has_written(self, db):
        from app.jobs import amend_schedule
        from app.services.schedule.amend import AmendOutcome

        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
        plan = self._plan(db, user)
        description = "Replace one easy run with a hill rep session (31 Aug to 6 Sep)."

        # The job opens its OWN session, so the test one is handed to it with a
        # no-op close. Without this the job's DB work fails, the job swallows the
        # error by design, and an assertion that "nothing was recorded" passes
        # for entirely the wrong reason.
        with patch.object(
            amend_schedule, "SessionLocal", return_value=_NoCloseSession(db)
        ), patch.object(
            amend_schedule,
            "amend_plan",
            return_value=AmendOutcome(
                ok=True, weeks_touched=1, sessions_written=6
            ),
        ):
            amend_schedule.amend_schedule_job(
                str(user.id), str(plan.id), 1, 1, "reason",
                str(thread.id), description,
            )

        events = self._events(db)
        assert [e.content for e in events] == [description]

    def test_an_amendment_that_does_not_land_records_nothing(self, db):
        from app.jobs import amend_schedule
        from app.services.schedule.amend import AmendOutcome

        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
        plan = self._plan(db, user)

        with patch.object(
            amend_schedule, "SessionLocal", return_value=_NoCloseSession(db)
        ), patch.object(
            amend_schedule,
            "amend_plan",
            return_value=AmendOutcome(
                ok=False, failures=["the coach could not be reached"]
            ),
        ):
            amend_schedule.amend_schedule_job(
                str(user.id), str(plan.id), 1, 1, "reason",
                str(thread.id), "Replace one easy run with a hill rep session.",
            )

        # The runner's plan is untouched, so the conversation says nothing
        # happened to it. Telling them it FAILED is #984, and is not this.
        assert self._events(db) == []

    def test_the_entry_carries_the_real_change_beside_the_card(self, db):
        """The card is what they AGREED to; the changes are what HAPPENED.

        A live amendment made them differ: the card promised an easy run would
        become hill reps and the rewrite removed the week's interval session
        instead, because the plan's rules could not fit a second quality day. The
        card alone was all the runner and the coach ever saw.
        """
        from app.jobs import amend_schedule
        from app.services.schedule.amend import AmendOutcome

        user = _seed_user(db)
        thread = Thread(user_id=user.id)
        db.add(thread)
        db.commit()
        db.refresh(thread)
        plan = self._plan(db, user)
        card = "Replace one easy run with a hill rep session (31 Aug to 6 Sep)."

        with patch.object(
            amend_schedule, "SessionLocal", return_value=_NoCloseSession(db)
        ), patch.object(
            amend_schedule,
            "amend_plan",
            return_value=AmendOutcome(
                ok=True,
                weeks_touched=1,
                sessions_written=2,
                changes=["Removed: Wed Threshold Intervals",
                         "Added: Wed Hill Reps"],
            ),
        ):
            amend_schedule.amend_schedule_job(
                str(user.id), str(plan.id), 1, 1, "reason",
                str(thread.id), card,
            )

        entries = self._events(db)
        assert len(entries) == 1
        content = entries[0].content
        assert card in content, "the runner has to recognise what they agreed to"
        assert "Threshold Intervals" in content, "the session that actually went"
        assert "Hill Reps" in content
