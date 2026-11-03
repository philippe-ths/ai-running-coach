"""#830: the conversational route in to marking a session done.

Pins the `complete_session` offer: what the contract refuses, whose session it can
reach, and what is re-checked when the runner confirms.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from unittest.mock import patch

import pytest

from app.services.schedule import completion
from tests._schedule_completion_fixtures import (
    _seed_plan,
    _seed_session,
    _seed_user,
)


# --- the conversational route in -------------------------------------------


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def test_an_offer_with_no_session_named_is_rejected_by_the_validator(db):
    """The model supplies the argument; the contract decides whether there is an
    offer at all. No id, no card, nothing minted."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "complete_session"}
        )

    assert result["ok"] is False
    assert result["error"] == "invalid_action"
    assert frame is None
    assert fake._store == {}


def test_a_confirmed_conversation_completes_the_session_through_the_same_writer(db):
    """The gym and the turbo never reach Strava, so a session the runner mentions
    is often the only record there will be — and it lands on the same columns the
    tap and the auto-match write."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    session = _seed_session(
        db, _seed_plan(db, user), title="Lower body", discipline="strength",
        intent="strength",
    )
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
            },
        )
        assert offer["ok"] is True
        assert "Lower body" in frame["description"]
        # Offering is not doing.
        db.refresh(session)
        assert session.completed_at is None

        result = proposed_actions.consume_and_execute(db, user.id, frame["token"])

    assert result["action_type"] == "complete_session"
    db.refresh(session)
    assert session.completed_at is not None
    assert session.completion_source == completion.CONVERSATION
    assert session.completed_activity_id is None


def test_an_offer_cannot_reach_another_runners_planned_session(db):
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    other = _seed_user(db)
    theirs = _seed_session(db, _seed_plan(db, other), title="Not mine")
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(theirs.id),
            },
        )

    assert result["ok"] is False
    assert result["error"] == "not_found"
    assert frame is None
    assert fake._store == {}, "no token may be minted against another runner's data"


def test_ownership_is_re_resolved_when_the_runner_confirms(db):
    """A token is not a capability over a row. Between the offer and the tap the
    session may have stopped being theirs, and the write must ask again."""
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    other = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), title="Lower body")
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        _offer, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
            },
        )
        session.user_id = other.id
        db.commit()

        with pytest.raises(LookupError):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])

    db.refresh(session)
    assert session.completed_at is None


def _offer_complete(db, user, session):
    from app.services.coach import proposed_actions

    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "complete_session",
                "planned_session_id": str(session.id),
            },
        )
    return fake, result, frame


def test_the_schedule_kill_switch_stops_a_complete_session_offer(db, monkeypatch):
    from app.core.config import settings

    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), title="Lower body")
    monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)

    fake, result, frame = _offer_complete(db, user, session)

    assert result["ok"] is False
    assert frame is None
    assert fake._store == {}


def test_an_already_done_session_is_not_offered_for_completion(db):
    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), title="Lower body")
    completion.complete_planned_session(db, session, source=completion.MANUAL)

    fake, result, frame = _offer_complete(db, user, session)

    assert result["ok"] is False
    assert frame is None
    assert fake._store == {}


def test_the_kill_switch_and_a_tick_are_re_checked_when_the_runner_confirms(
    db, monkeypatch
):
    """The token outlives the state it was built from."""
    from app.core.config import settings
    from app.services.coach import proposed_actions

    user = _seed_user(db)
    session = _seed_session(db, _seed_plan(db, user), title="Lower body")
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _o, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {"action_type": "complete_session", "planned_session_id": str(session.id)},
        )
        monkeypatch.setattr(settings, "SCHEDULE_ENABLED", False)
        with pytest.raises(ValueError, match="unavailable"):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])
        db.refresh(session)
        assert session.completed_at is None

        monkeypatch.setattr(settings, "SCHEDULE_ENABLED", True)
        _o, frame = proposed_actions.mint_proposed_action(
            db, user.id,
            {"action_type": "complete_session", "planned_session_id": str(session.id)},
        )
        completion.complete_planned_session(db, session, source=completion.MANUAL)
        with pytest.raises(ValueError, match="already done"):
            proposed_actions.consume_and_execute(db, user.id, frame["token"])
    db.refresh(session)
    assert session.completion_source == completion.MANUAL
