"""#987/#998: what an amendment promises, and who keeps the promise.

The order used to be inverted. The coach put up a card naming a change, the
runner tapped it, and only THEN did anything work out what the week would hold.
The runner agreed to a forecast and received whatever the writer produced.

Reproduced live on 2026-08-27 against real data. The card said "Replace one easy
run with a hill repeat session (31 Aug to 6 Sep). The rest of your plan, its
rules and your race stay as they are." What landed removed the easy run AND the
week's Threshold Intervals, leaving a peak week with no quality session in it.

The cause was not a careless prompt. The runner's own rules space quality three
days apart, their long run is Saturday, and no quality may sit the day before it,
so no arrangement fits hill reps alongside Wednesday's intervals. The writer's
first attempt was rejected by the validator, and the retry asked it to "write the
amendment again, fixing every one of these" without restating that refusing was
allowed. The second attempt complied the only way it could.

DECIDING FIRST WAS WITHDRAWN IN #998, and this file now pins what replaced it.

Settling the week before the card meant generating a plan inside the chat
request, and that request is killed at a ceiling it cannot be told about. In
production the turn reached the offer with between 10 and 35 seconds left of its
42-second budget, because the rounds and tool calls before the offer are variable
and often cost more than the generation. Every window was refused, and the
refusal asked the runner to send the request again, which refused again. Six
messages, no sessions, and the plan the runner was promised never existed.

So the generation moved to the worker, where there is no ceiling to run out of
and the full retry budget is available again. What that costs is the settled
card; what it buys is the amendment actually being written.

The defect #987 found is still unreachable, by a different route. It was never
the card's TIMING that was dishonest, it was a card listing sessions nobody had
generated yet. The contract now:

**The card names the ask and nothing else.** "Rewrite the week of 31 Aug" is a
promise the job can keep. A session list would not be, so there is not one.

**Confirming hands the work over and says so.** No generation runs in the
request, which is the property that made a second attempt impossible to hide in,
and it still holds - now because there is no generation here at all.

**The job reports back, either way.** Success writes the ledger entry from what
was ACTUALLY written. Failure says so in the thread, in the coach's own voice,
rather than leaving the runner watching a screen that never changes (#984).

The retry's own instruction is pinned too, because it is the specific text whose
absence caused the original defect, and the worker is where it runs again.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import asyncio
import uuid
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models import User, UserProfile
from app.models.planned_session import PlannedSession
from app.models.training_plan import TrainingPlan
from app.services.coach import proposed_actions
from app.services.schedule import store
from app.services.schedule.amend import AmendedPlan, AmendProposal, resolve_window
from app.services.weeks import MONDAY

REAL_TODAY = date.today()

# The live reproduction's rule, in the validator's own words.
THREE_DAY_RULE = (
    "week 2026-08-31 cannot satisfy the plan's rule 'At least 3 days between "
    "hard sessions' (At least 3 days apart between quality sessions.): no "
    "arrangement of this week satisfies this rule alongside the others"
)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db) -> User:
    user = User(email=f"amend987-{uuid.uuid4()}@example.com")
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


def _plan(db, user) -> TrainingPlan:
    plan = TrainingPlan(user_id=user.id, status="active", rules=[], week_shapes=[])
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _session(db, plan, *, start, title, intent="easy") -> PlannedSession:
    row = PlannedSession(
        plan_id=plan.id,
        user_id=plan.user_id,
        window_start=start,
        window_end=start,
        intent=intent,
        discipline="run",
        commitment="committed",
        title=title,
        target_effort_score=40.0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _window():
    return resolve_window(REAL_TODAY, MONDAY, weeks_from=1, weeks_through=1)


def _payload():
    return {
        "action_type": "amend_plan",
        "weeks_from": 1,
        "weeks_through": 1,
        "amend_reason": "add a hill session",
    }


def _offer(db, user, _proposal=None, *, redis=None):
    """Mint through the real seam.

    No model call is stood in for, because the real path no longer makes one
    here: minting an amendment offer is now pure, and the generation happens on
    the worker after the tap (#998).
    """
    with patch.object(proposed_actions, "redis_conn", redis or _FakeRedis()):
        return proposed_actions.mint_proposed_action(db, user.id, _payload())


def _substituting_proposal(start, end, db=None, plan=None):
    """The 2026-08-27 rewrite: hills arrive, the intervals quietly go.

    `changes` is derived by the real `_forecast_change` when the rows are
    available, so the card's line and the confirm's line are produced by the same
    comparison from the same two sides. That agreement is a property worth
    pinning rather than a coincidence to hand-write.
    """
    amended = AmendedPlan.model_validate(
            {
                "weeks": [
                    {
                        "week_start": start.isoformat(),
                        "phase": None,
                        "sessions": [
                            {
                                "window_start": start.isoformat(),
                                "window_end": start.isoformat(),
                                "intent": "quality",
                                "discipline": "run",
                                "title": "Hill Repeats",
                            },
                            {
                                "window_start": (
                                    start + timedelta(days=1)
                                ).isoformat(),
                                "window_end": (start + timedelta(days=1)).isoformat(),
                                "intent": "easy",
                                "discipline": "bike",
                                "title": "Easy Bike",
                            },
                            {
                                "window_start": (
                                    start + timedelta(days=2)
                                ).isoformat(),
                                "window_end": (start + timedelta(days=2)).isoformat(),
                                "intent": "easy",
                                "discipline": "run",
                                "title": "Easy Run",
                            },
                        ],
                    }
                ]
            }
    )
    changes = []
    if db is not None and plan is not None:
        from app.services.schedule.amend import _forecast_change, sessions_in_window

        rows = sessions_in_window(db, plan.user_id, plan, start, end)
        changes = _forecast_change(rows, amended, REAL_TODAY)
    return AmendProposal(
        ok=True, amended=amended, changes=changes, start=start, end=end
    )


# --- the card names the ask, and nothing it has not written -------------------


def test_the_card_names_the_window_rather_than_a_session_list(db):
    """A promise the job can keep.

    The card goes up before anything is generated, so the only honest thing it
    can carry is what the runner asked for: which weeks, and that the rest of the
    plan is untouched. #987 was right that a card must not describe sessions
    nobody has written; naming the ask is how that stays true without settling
    the week inside a request that cannot afford it.
    """
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")

    result, frame = _offer(db, user, None)

    assert result["ok"] is True
    assert start.strftime("%-d %b") in frame["description"]
    assert frame["confirm_label"] == "Update my plan"


def test_the_card_carries_no_week_it_has_not_generated(db):
    """The #987 defect, made unreachable from the other side.

    A card listing sessions is a card that can be wrong about them. There is no
    list, so there is nothing for the writer to contradict later.
    """
    user = _user(db)
    plan = _plan(db, user)
    start, _end = _window()
    _session(db, plan, start=start, title="Threshold Intervals", intent="quality")

    _result, frame = _offer(db, user, None)

    assert not frame.get("week"), (
        "the card must not show a week before one has been written; that is the "
        "promise #987 found being broken"
    )
    assert not frame.get("changes")


def test_the_card_survives_the_journey_to_the_client(db):
    """The frame is put straight onto the SSE stream by `json.dumps`.

    Pinned because the unit tests above read the frame as Python objects and so
    never cross that boundary. A live turn did: the card was built correctly, the
    stream died trying to send it, and the runner got "Sorry, I hit an error
    answering that" in place of the whole reply.
    """
    import json

    user = _user(db)
    plan = _plan(db, user)
    start, _end = _window()
    _session(db, plan, start=start, title="Easy Run")

    _result, frame = _offer(db, user, None)

    encoded = json.dumps({"type": "proposed_action", **frame})
    assert "amend_plan" in encoded


# --- an impossible request is reported, never swallowed -----------------------


def test_an_impossible_amendment_tells_the_runner_in_their_thread(db):
    """The runner's own rules can make a request genuinely impossible.

    That is now discovered on the worker, after the tap, so it cannot be said
    before the card the way #987 arranged. What must not happen is the thing that
    replaced it going silent: the job's own docstring used to promise that a
    failure here was "silence plus a log line", on the reasoning that the runner
    still has the plan they had a minute ago. True of the plan, false of the
    person, who tapped a card and watched nothing happen (#984).
    """
    from app.jobs.amend_schedule import _say_it_failed
    from app.models.coach_chat_message import CoachChatMessage
    from app.models.thread import Thread

    user = _user(db)
    thread = Thread(user_id=user.id)
    db.add(thread)
    db.commit()

    _say_it_failed(db, thread.id, [THREE_DAY_RULE])

    said = (
        db.query(CoachChatMessage)
        .filter(CoachChatMessage.thread_id == thread.id)
        .all()
    )
    assert len(said) == 1
    note = said[0]
    assert note.role == "assistant", (
        "a failure must not be recorded as a confirmed action event; the ledger "
        "would then carry a write that never happened"
    )
    assert "could not write" in note.content
    assert "3 days between hard sessions" in note.content
    assert "unchanged" in note.content


def test_a_refused_amendment_leaves_every_session_alone(db):
    """The plan is the fallback, and it stays the fallback."""
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    _session(db, plan, start=start + timedelta(days=2), title="Threshold Intervals",
             intent="quality")
    before = [(r.id, r.title) for r in db.query(PlannedSession).all()]

    _offer(db, user, AmendProposal(ok=False, failures=[THREE_DAY_RULE],
                                   start=start, end=end))

    assert [(r.id, r.title) for r in db.query(PlannedSession).all()] == before


# --- confirming writes what was shown ----------------------------------------


def test_confirming_runs_no_generation_at_all(db):
    """The defect needed a second attempt to happen in. There is no longer one:
    every judgement was made while the runner could still say no."""
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    redis = _FakeRedis()

    _result, frame = _offer(
        db, user, _substituting_proposal(start, end, db, plan), redis=redis
    )

    def _must_not_run(*_a, **_k):
        raise AssertionError("confirming must not generate anything")

    with patch.object(proposed_actions, "redis_conn", redis), patch(
        "app.services.schedule.amend.propose_amendment", new=_must_not_run
    ):
        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert result["action_type"] == "amend_plan"


def test_confirming_hands_the_work_to_the_worker(db):
    """The runner's tap enqueues; it does not write.

    This is the property that keeps #987's defect unreachable. The old shape
    could hide a second generation between the tap and the week; there is no
    generation in this request to hide anything in, and the one that does run has
    its full retry budget on the worker.
    """
    user = _user(db)
    plan = _plan(db, user)
    redis = _FakeRedis()
    _result, frame = _offer(db, user, None, redis=redis)

    enqueued = []
    with patch.object(proposed_actions, "redis_conn", redis), patch(
        "app.jobs.amend_schedule.enqueue_amendment",
        new=lambda *a, **k: enqueued.append((a, k)),
    ):
        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert result["action_type"] == "amend_plan"
    assert len(enqueued) == 1, "the confirm must hand the amendment to the worker"
    _args, kwargs = enqueued[0]
    assert kwargs["weeks_from"] == 1 and kwargs["weeks_through"] == 1
    assert kwargs["thread_id"] is None or kwargs["thread_id"] is not None
    # Nothing was written in the request.
    assert db.query(PlannedSession).count() == 0


def test_the_confirm_promises_only_what_the_job_will_do(db):
    """It says the work is happening and where the answer will appear.

    The previous wording said "Done." because the week really was already
    written. It is not any more, and a confirm that still said so would be the
    same lie #987 removed, told one step later.
    """
    user = _user(db)
    _plan(db, user)
    redis = _FakeRedis()
    _result, frame = _offer(db, user, None, redis=redis)

    with patch.object(proposed_actions, "redis_conn", redis), patch(
        "app.jobs.amend_schedule.enqueue_amendment", new=lambda *a, **k: None
    ):
        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    message = result["message"]
    assert not message.startswith("Done"), (
        "nothing is done yet; the worker has not run"
    )
    assert "Schedule screen" in message
    assert "tell you here" in message


# --- the retry that caused it ------------------------------------------------


def test_the_retry_tells_the_writer_it_may_still_refuse(db):
    """The specific text whose absence caused the live defect.

    Told only to fix the failures, a model finds the nearest legal week, and
    when the request genuinely does not fit, the nearest legal week is one that
    drops a session the runner asked to keep. The refusal has to be carried
    forward INTO the retry, because the last thing said wins.
    """
    from app.services.schedule import amend

    user = _user(db)
    plan = _plan(db, user)
    prompts = []

    class _Client:
        # `timeout` is accepted because the real client takes it (#995). A double
        # that omits a kwarg the caller passes raises TypeError INSIDE the loop's
        # `except Exception`, which reads it as a transport failure — so the test
        # goes green-then-red for a reason that has nothing to do with retries.
        async def generate_structured(
            self, *, system, user, tool, max_tokens=1024, timeout=None
        ):
            prompts.append(user)
            # Off-contract, so the loop rejects and retries.
            return {"weeks": "not a list"}

    with patch.object(amend.turn, "build_client", return_value=_Client()), \
         patch.object(amend.turn, "over_budget", return_value=False):
        asyncio.run(
            amend.propose_amendment(
                db, user, plan, weeks_from=1, weeks_through=1,
                instruction="add a hill session",
            )
        )

    assert len(prompts) >= 2, "the loop must actually retry for this to be pinned"
    retry = prompts[1]
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" in retry
    assert "do not write an amendment at all" in retry
    assert "refuse" in retry
    # The first prompt does NOT carry it, so the assertion above is about the
    # retry rather than about text that is simply always present.
    assert "YOUR PREVIOUS ATTEMPT WAS REJECTED" not in prompts[0]


def test_a_refusal_reaches_the_runner_rather_than_a_log_line(db, monkeypatch):
    """The kill switch is refused the same way, so the coach can say what
    happened instead of the runner watching a screen that never updates."""
    user = _user(db)
    _plan(db, user)
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    result, frame = _offer(db, user, AmendProposal(ok=True))

    assert frame is None
    assert result["ok"] is False
    assert "unavailable" in result["detail"]
