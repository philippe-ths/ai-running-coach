"""#945: the `revise_max_hr` proposed action -- mint, security boundary, and
the anti-nag property.

Covers what test_max_hr_calibration_945.py cannot: the action never lets the
model supply a number (the server derives it fresh from the runner's own
activities), ownership is re-resolved from the authenticated caller at every
step, nothing is written to the profile until the runner confirms, and a
second offer attempt right after the first does not re-raise the same
evidence.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.services.coach import proposed_actions

NOW = datetime.now(timezone.utc)


class _FakeRedis:
    def __init__(self):
        self._store = {}

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def getdel(self, key):
        return self._store.pop(key, None)


def _user(db, *, max_hr=180) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=max_hr,
            max_hr_source="user_entered",
        )
    )
    db.commit()
    return user


def _activity(db, user: User, *, days_ago: float, max_hr) -> Activity:
    start = NOW - timedelta(days=days_ago)
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=start,
        type="Run",
        name="Run",
        distance_m=8000,
        moving_time_s=2400,
        elapsed_time_s=2400,
        elev_gain_m=0.0,
        max_hr=max_hr,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def _seed_qualifying_evidence(db, user):
    """Three recent runs, two of them clearing the stated 180 bpm max by more
    than the noise margin -- the minimum shape that raises a finding."""
    _activity(db, user, days_ago=1, max_hr=193)
    _activity(db, user, days_ago=5, max_hr=190)
    _activity(db, user, days_ago=10, max_hr=175)


def test_offer_fails_with_no_pending_evidence(db):
    user = _user(db)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
    assert result["ok"] is False
    assert frame is None
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 180  # untouched


def test_the_model_cannot_inject_its_own_proposed_value(db):
    """`proposed_max_hr` exists only on the server-internal `StoredProposedAction`,
    never on the model-facing `ProposedActionRequest` -- so a model (or a crafted
    tool call) that tries to smuggle one in is rejected outright by the
    request schema's `extra=\"forbid\"`, not merely ignored."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db,
            user.id,
            {"action_type": "revise_max_hr", "proposed_max_hr": 250},
        )
    assert result["ok"] is False
    assert result["error"] == "invalid_action"
    assert frame is None
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 180


def test_offer_derives_the_number_from_the_runners_own_data(db):
    """The model supplies nothing but the action type -- there is no field for
    it to invent a number into -- and the card names the real evidence."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
    assert result["ok"] is True
    assert frame["action_type"] == "revise_max_hr"
    assert "180" in frame["description"]
    assert "193" in frame["description"]


def test_no_write_until_the_runner_confirms(db):
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 180, "minting an offer must never write the profile"
    assert frame is not None


def test_confirm_writes_the_exact_offered_number_and_marks_provenance(db):
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        token = frame["token"]
        result = proposed_actions.consume_and_execute(db, user.id, token)
    assert result["action_type"] == "revise_max_hr"
    assert result["max_hr"] == 193
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 193
    assert profile.max_hr_source == "runner_confirmed"
    # The anti-nag stamp is cleared once the fact itself has moved.
    assert profile.max_hr_revision_last_surfaced_value is None
    assert profile.max_hr_revision_last_surfaced_at is None


def test_execute_cannot_reach_another_runners_profile(db):
    """Ownership is re-resolved from the authenticated caller at execute time
    -- there is no id in the payload for a cross-user token to abuse, but the
    token itself is scoped to the minting user and cannot be redeemed by
    another."""
    user = _user(db)
    other = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        token = frame["token"]
        try:
            proposed_actions.consume_and_execute(db, other.id, token)
            assert False, "cross-user token should not execute"
        except LookupError:
            pass
    other_profile = db.query(UserProfile).filter(UserProfile.user_id == other.id).one()
    assert other_profile.max_hr == 180, "the other runner's own profile must be untouched"
    user_profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert user_profile.max_hr == 180, "the token being misused must not touch the real owner either"


def test_offer_is_single_use(db):
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        token = frame["token"]
        proposed_actions.consume_and_execute(db, user.id, token)
        try:
            proposed_actions.consume_and_execute(db, user.id, token)
            assert False, "token should have been consumed"
        except LookupError:
            pass


# --------------------------------------------------------------------------- #
# Confirm-time staleness (#945 review fix 1, CRITICAL)
# --------------------------------------------------------------------------- #


def test_confirm_refuses_when_the_runner_edited_their_profile_since_the_offer(db):
    """Demonstrated by a review: stated 180 -> offer minted at 193 -> the
    runner edits their own profile to 150 (the ordinary PUT /api/profile
    path, simulated here by writing the column directly) -> confirming the
    stale offer must NOT silently overwrite that deliberate correction with
    193. "does some revision still hold against the CURRENT profile" is the
    wrong question -- it is satisfied even here, since 150 is even further
    below the observed peaks. The only safe question is an exact match
    against what the offer was minted against."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        token = frame["token"]

        # The runner's own deliberate edit lands before they confirm the card.
        profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
        profile.max_hr = 150
        db.commit()

        try:
            proposed_actions.consume_and_execute(db, user.id, token)
            assert False, "a stale offer must be refused, not silently written"
        except ValueError:
            pass

    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 150, "the runner's own newer edit must survive untouched"
    assert profile.max_hr_source != "runner_confirmed"


def test_confirm_succeeds_when_the_profile_is_unchanged_since_the_offer(db):
    """The positive control for the fix above: when nothing has moved, the
    exact-match check does not itself become a new way to refuse a valid
    confirm."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        _result, frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        token = frame["token"]
        result = proposed_actions.consume_and_execute(db, user.id, token)
    assert result["max_hr"] == 193
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).one()
    assert profile.max_hr == 193
    assert profile.max_hr_source == "runner_confirmed"


# --------------------------------------------------------------------------- #
# Anti-nag (#945 AC5)
# --------------------------------------------------------------------------- #


def test_a_second_offer_right_after_the_first_does_not_reraise_the_same_evidence(db):
    """Minting the offer stamps the anti-nag bookkeeping immediately -- a coach
    that (correctly or not) tries to offer the same fact again in the same
    session, before the runner has responded, gets no pending revision."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        first, _frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        assert first["ok"] is True
        second, second_frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
    assert second["ok"] is False
    assert second_frame is None


def test_materially_higher_new_evidence_still_offers_within_the_cooldown(db):
    """The anti-nag rule suppresses re-raising the SAME evidence, not evidence
    that has genuinely moved."""
    user = _user(db)
    _seed_qualifying_evidence(db, user)
    fake = _FakeRedis()
    with patch.object(proposed_actions, "redis_conn", fake):
        first, _frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
        assert first["ok"] is True
        # A materially higher peak lands.
        _activity(db, user, days_ago=0.5, max_hr=205)
        second, second_frame = proposed_actions.mint_proposed_action(
            db, user.id, {"action_type": "revise_max_hr"}
        )
    assert second["ok"] is True
    assert "205" in second_frame["description"]
