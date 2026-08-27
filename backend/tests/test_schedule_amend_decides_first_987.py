"""#987: the amendment decides before it offers.

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

This file pins the three things that make that unreachable, in the order they
matter:

**The card carries the real difference.** Not what was asked for: what the
settled amendment would actually do. A rewrite that drops a session the runner
meant to keep says so BEFORE the tap. This is structural and holds whatever the
prompt does, which is why it is the first test here rather than the last.

**An impossible request never becomes a card.** It comes back as a refusal the
coach must answer in the conversation, carrying the rule that blocked it, so the
runner gets alternatives instead of silence.

**Confirming writes what was shown.** No generation runs at confirm time, so
there is no second attempt in which a refusal can turn into a substitution.

The retry's own instruction is pinned too, because it is the specific text whose
absence caused the defect.

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


def _offer(db, user, proposal, *, redis=None):
    """Mint through the real seam, with only the model call stood in for."""

    async def _settled(*_a, **_k):
        return proposal

    with patch.object(
        proposed_actions, "redis_conn", redis or _FakeRedis()
    ), patch("app.services.schedule.amend.propose_amendment", new=_settled):
        prepared = asyncio.run(
            proposed_actions.prepare_offer(db, user.id, _payload())
        )
        return proposed_actions.mint_proposed_action(
            db, user.id, _payload(), prepared=prepared
        )


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


# --- the card carries the real difference ------------------------------------


def test_the_card_shows_the_session_a_substitution_would_remove(db):
    """The structural guarantee, and the one that does not depend on the prompt.

    Given the exact rewrite that cost a runner their intervals, the card the
    runner reads names that removal. They can decline it. Under the old order
    there was nothing to decline: the removal was decided after the tap.
    """
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    _session(db, plan, start=start + timedelta(days=2), title="Threshold Intervals",
             intent="quality")

    result, frame = _offer(db, user, _substituting_proposal(start, end, db, plan))

    assert result["ok"] is True
    shown = " ".join(frame["changes"])
    assert "Threshold Intervals" in shown, (
        "a rewrite that removes the runner's quality session must say so on the "
        "card, not in a ledger line they read afterwards"
    )
    assert shown.startswith("Removed:")


def test_the_card_carries_the_week_it_is_proposing(db):
    """The runner reads the week, not a sentence about the week. Each session is
    marked for whether it is new to the window, so the change is visible in
    place rather than only in the summary line."""
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    _session(db, plan, start=start + timedelta(days=1), title="Easy Bike")
    _session(db, plan, start=start + timedelta(days=2), title="Threshold Intervals",
             intent="quality")

    _result, frame = _offer(db, user, _substituting_proposal(start, end, db, plan))

    week = frame["week"]
    assert [row["title"] for row in week] == ["Hill Repeats", "Easy Bike", "Easy Run"]
    assert all(isinstance(row["date"], str) for row in week)
    by_title = {row["title"]: row for row in week}
    # New to the window, so it is marked.
    assert by_title["Hill Repeats"]["changed"] is True
    # Untouched on the same day, so it is not dressed up as a change.
    assert by_title["Easy Bike"]["changed"] is False
    # Moved from Monday to Wednesday, which IS a change to that day.
    assert by_title["Easy Run"]["changed"] is True
    assert by_title["Hill Repeats"]["intent"] == "quality"


def test_the_card_survives_the_journey_to_the_client(db):
    """The frame is put straight onto the SSE stream by `json.dumps`, which has
    no encoder for a `date`.

    Pinned because the unit tests above read the frame as Python objects and so
    never crossed that boundary. A live turn did: the card was built correctly,
    the stream died trying to send it, and the runner got "Sorry, I hit an error
    answering that" in place of the whole reply.
    """
    import json

    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")

    _result, frame = _offer(db, user, _substituting_proposal(start, end, db, plan))

    encoded = json.dumps({"type": "proposed_action", **frame})
    assert start.isoformat() in encoded


# --- an impossible request never becomes a card ------------------------------


def test_an_impossible_amendment_is_refused_instead_of_offered(db):
    """The runner's own rules can make a request genuinely impossible. The
    honest answer is that it does not fit, said in the conversation while they
    are still in it — not a card, and not silence half a minute later."""
    user = _user(db)
    _plan(db, user)
    start, end = _window()
    refused = AmendProposal(
        ok=False, failures=[THREE_DAY_RULE], start=start, end=end
    )

    result, frame = _offer(db, user, refused)

    assert frame is None, "an amendment that cannot be written must not be offered"
    assert result["ok"] is False
    assert result["error"] == "cannot_amend"
    # And the coach is told WHY, in terms it can turn into alternatives.
    assert "3 days between hard sessions" in result["detail"]


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


def test_what_lands_is_what_the_card_said(db):
    """The whole point, asserted end to end across the offer and the confirm."""
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    _session(db, plan, start=start + timedelta(days=2), title="Threshold Intervals",
             intent="quality")
    redis = _FakeRedis()

    _result, frame = _offer(
        db, user, _substituting_proposal(start, end, db, plan), redis=redis
    )
    with patch.object(proposed_actions, "redis_conn", redis):
        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    landed = {
        (row.window_start, row.title)
        for row in db.query(PlannedSession).filter(
            PlannedSession.plan_id == plan.id,
            PlannedSession.window_start >= start,
            PlannedSession.window_start <= end,
        )
    }
    assert {
        (date.fromisoformat(row["date"]), row["title"]) for row in frame["week"]
    } == landed
    # And the confirm reports it in the past tense, because it has happened.
    assert result["message"].startswith("Done.")
    assert result["changes"] == frame["changes"]


def test_the_confirm_reports_the_change_rather_than_promising_a_screen(db):
    """The old answer was "your Schedule screen will show them in a minute",
    which the request had no way to keep: a crashed work-horse, a refusal or a
    substitution each turned it into something else and nothing came back."""
    user = _user(db)
    plan = _plan(db, user)
    start, end = _window()
    _session(db, plan, start=start, title="Easy Run")
    redis = _FakeRedis()

    _result, frame = _offer(
        db, user, _substituting_proposal(start, end, db, plan), redis=redis
    )
    with patch.object(proposed_actions, "redis_conn", redis):
        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert "in a minute" not in result["message"]
    assert "Everything else in your plan is as it was." in result["message"]


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
        async def generate_structured(self, *, system, user, tool, max_tokens=1024):
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
