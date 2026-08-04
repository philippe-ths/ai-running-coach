"""Thread proposed actions (#768): mint, scope, and execute.

All row data is synthetic test setup. These tests cover the security boundary
and the narrow reversible action set: check-in, stated intent, block split, and
block merge.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, CheckIn, User
from app.services.blocks import assign_activity_to_block
from app.services.coach import proposed_actions

T0 = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)
GAP = 1800


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    return user


def _activity(db, user: User, *, start=T0, elapsed=1500, type="Run", distance_m=5000):
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=start,
        type=type,
        name=type,
        distance_m=distance_m,
        moving_time_s=elapsed,
        elapsed_time_s=elapsed,
        elev_gain_m=0.0,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def test_check_in_offer_executes_once_and_is_user_scoped(db):
    user = _user(db)
    other = _user(db)
    activity = _activity(db, user)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "check_in",
                "activity_id": str(activity.id),
                "rpe": 7,
            },
        )
        assert offer["ok"] is True
        token = offer["frame"]["token"]

        # Another user cannot redeem or burn the token.
        try:
            proposed_actions.consume_and_execute(db, other.id, token)
            assert False, "cross-user token should not execute"
        except LookupError:
            pass

        with patch("app.services.checkins.analysis.analyze"), patch(
            "app.jobs.process_new_activity.maybe_enqueue_fuller_turn"
        ):
            result = proposed_actions.consume_and_execute(db, user.id, token)
        assert result["action_type"] == "check_in"
        checkin = db.query(CheckIn).one()
        assert checkin.activity_id == activity.id
        assert checkin.rpe == 7

        # Single-use.
        try:
            proposed_actions.consume_and_execute(db, user.id, token)
            assert False, "token should have been consumed"
        except LookupError:
            pass


def test_intent_offer_rejects_invalid_intent_for_activity_type(db):
    user = _user(db)
    walk = _activity(db, user, type="Walk", elapsed=900, distance_m=1200)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "intent",
                "activity_id": str(walk.id),
                "user_intent": "Tempo",
            },
        )
    assert offer["ok"] is False
    assert offer["error"] == "invalid_action"


def test_intent_offer_executes_through_shared_write_path(db):
    user = _user(db)
    activity = _activity(db, user)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "intent",
                "activity_id": str(activity.id),
                "user_intent": "Tempo",
            },
        )
        token = offer["frame"]["token"]
        with patch("app.services.intents.analysis.analyze"):
            result = proposed_actions.consume_and_execute(db, user.id, token)
    assert result["action_type"] == "intent"
    db.refresh(activity)
    assert activity.user_intent == "Tempo"


def test_split_block_offer_executes_existing_service(db):
    user = _user(db)
    walk = _activity(db, user, start=T0, elapsed=720, type="Walk", distance_m=1200)
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run = _activity(db, user, start=T0 + timedelta(seconds=900), elapsed=2400, distance_m=8200)
    assign_activity_to_block(db, run, gap_seconds=GAP)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "split_block",
                "block_id": str(block.id),
                "split_at_activity_id": str(run.id),
            },
        )
        assert offer["ok"] is True
        token = offer["frame"]["token"]
        result = proposed_actions.consume_and_execute(db, user.id, token)
    assert result["action_type"] == "split_block"
    db.refresh(walk)
    db.refresh(run)
    assert walk.block_id != run.block_id


def test_merge_blocks_offer_executes_existing_service(db):
    user = _user(db)
    walk = _activity(db, user, start=T0, elapsed=720, type="Walk", distance_m=1200)
    block_a = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run = _activity(
        db,
        user,
        start=T0 + timedelta(seconds=walk.elapsed_time_s + GAP * 2),
        elapsed=2400,
        distance_m=8200,
    )
    block_b = assign_activity_to_block(db, run, gap_seconds=GAP)
    fake = _FakeRedis()

    with patch.object(proposed_actions, "redis_conn", fake):
        offer = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {
                "action_type": "merge_blocks",
                "block_id": str(block_b.id),
                "other_block_id": str(block_a.id),
            },
        )
        assert offer["ok"] is True
        token = offer["frame"]["token"]
        result = proposed_actions.consume_and_execute(db, user.id, token)
    assert result["action_type"] == "merge_blocks"
    db.refresh(walk)
    db.refresh(run)
    assert walk.block_id == run.block_id
