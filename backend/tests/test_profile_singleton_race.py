"""Race-safety of the per-user singleton get-or-create (#598).

A new user's first authenticated screen fires /profile, /coach/voice and
/coach/stance concurrently. All three land in ``get_current_user_profile``,
miss the SELECT for the singleton rows (CoachingRelationship, UserProfile), and
attempt the INSERT. The losers violate the unique/PK constraint and would 500
on the user's very first screen. The fix mirrors the User recovery in
``resolve_user_by_email``: roll back the failed INSERT and reuse the row the
winner committed.

These tests drive ``get_current_user_profile`` directly against a session with
real commit isolation and force the initial lookup to miss (the race window
between the SELECT and our own INSERT), so the recovery path is exercised.
"""

import pytest

from app.api import profile as profile_module
from app.api.profile import get_current_user_profile
from app.models import CoachingRelationship, User, UserProfile


@pytest.fixture
def committing_db():
    """A session whose commits actually persist.

    The shared ``db`` fixture wraps everything in one outer transaction that is
    rolled back at teardown, so an inner ``db.commit()`` does not truly persist
    and the recovery path's ``db.rollback()`` would revert the winner's row too.
    The race-recovery path needs real commit isolation.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _flaky_once(real):
    """Wrap a lookup so its FIRST call misses (returns None), then behaves."""
    calls = {"n": 0}

    def flaky(session, user_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session, user_id)

    return flaky


def _make_user(db, email):
    user = User(email=email)
    db.add(user)
    db.commit()
    return user


class TestSingletonGetOrCreateRace:
    def test_relationship_create_recovers_from_race(self, committing_db, monkeypatch):
        db = committing_db
        user = _make_user(db, "racer1@example.com")
        # A competing request already won the relationship INSERT.
        db.add(CoachingRelationship(user_id=user.id))
        db.commit()

        # ...but our request's initial lookup missed it (the race window).
        monkeypatch.setattr(
            profile_module,
            "_lookup_relationship",
            _flaky_once(profile_module._lookup_relationship),
        )

        # Must recover, not 500 on the unique-user_id violation.
        result = get_current_user_profile(db, user)

        assert result.user_id == user.id
        assert db.query(CoachingRelationship).count() == 1  # no duplicate/orphan

    def test_profile_create_recovers_from_race(self, committing_db, monkeypatch):
        db = committing_db
        user = _make_user(db, "racer2@example.com")
        # A competing request already won both singleton INSERTs.
        db.add(CoachingRelationship(user_id=user.id))
        db.add(
            UserProfile(
                user_id=user.id,
                goal_type="general",
                experience_level="intermediate",
                weekly_days_available=4,
                current_weekly_km=20,
                upcoming_races=[],
                max_hr=190,
            )
        )
        db.commit()

        # Force our profile lookup to miss the first time, so we attempt the
        # INSERT and hit the primary-key (user_id) violation.
        monkeypatch.setattr(
            profile_module,
            "_lookup_profile",
            _flaky_once(profile_module._lookup_profile),
        )

        result = get_current_user_profile(db, user)

        assert result.user_id == user.id
        assert db.query(UserProfile).count() == 1  # reused the winner's row

    def test_happy_path_creates_both_rows(self, committing_db):
        """First caller with no rows creates the relationship and the profile."""
        db = committing_db
        user = _make_user(db, "fresh@example.com")

        result = get_current_user_profile(db, user)

        assert result.user_id == user.id
        assert result.goal_type == "general"
        assert db.query(CoachingRelationship).count() == 1
        assert db.query(UserProfile).count() == 1
