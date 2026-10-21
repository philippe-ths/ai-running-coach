"""#1125: account deletion removes every row tied to the runner, and fails
before the sign-in is gone when it cannot.

A runner who had opened the schedule's week view held a `week_recommendations`
row the cascade did not know, so on Postgres the cascade raised after Clerk had
already deleted the sign-in: the sign-in gone, every row kept, and a "nothing
has changed" message that a retry could not make true. SQLite does not enforce
foreign keys, so the suite could not see it; the coverage test below reads the
schema instead. The owner guard also failed open when the owner could not be
identified.
"""

from datetime import date
from unittest.mock import patch

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.models import User, WeekRecommendation
from app.services.account_deletion import delete_user_account
from tests.test_account_deletion import _residual_rows, _seed_full_user


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _two_runners(db):
    owner = _seed_full_user(db, email="owner@del.dev", athlete_id=1, strava_activity_id=11, hash_suffix="o")
    test = _seed_full_user(db, email="test@del.dev", athlete_id=2, strava_activity_id=22, hash_suffix="t")
    return owner, test


def _delete(client, ctx, email):
    app.dependency_overrides[verify_clerk_session] = lambda: ctx.user
    with patch("app.api.account.delete_clerk_user_by_email", return_value=True) as clerk:
        resp = client.request("DELETE", "/api/account", json={"confirm_email": email})
    return resp, clerk


def test_the_cascade_covers_every_table_that_points_at_a_runner_or_their_activities(db):
    pointing = {
        table.name
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name in ("users", "activities")
    }
    user = User(email="empty@del.dev")
    db.add(user)
    db.commit()

    covered = set(delete_user_account(db, user.id))

    assert pointing - covered == set()


def test_a_runners_week_recommendation_goes_with_the_account(db):
    _, test = _two_runners(db)
    db.add(WeekRecommendation(user_id=test.user_id, week_start=date(2026, 10, 19)))
    db.commit()

    delete_user_account(db, test.user_id)

    assert db.query(WeekRecommendation).filter_by(user_id=test.user_id).count() == 0


def test_a_cascade_that_fails_leaves_the_sign_in_and_every_row(client, db, monkeypatch):
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner.email)
    before = _residual_rows(db, test)

    with patch("app.api.account.delete_user_account", side_effect=RuntimeError("fk")):
        resp, clerk = _delete(client, test, test.email)

    assert resp.status_code == 500
    clerk.assert_not_called()
    assert _residual_rows(db, test) == before


@pytest.mark.parametrize("owner_email", ["", "philippe.marr@gmial.com", "owner@del.dev,"])
def test_no_account_is_deleted_while_the_owner_cannot_be_identified(
    client, db, monkeypatch, owner_email
):
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner_email)

    resp, clerk = _delete(client, owner, owner.email)
    assert resp.status_code == 503
    clerk.assert_not_called()
    assert _residual_rows(db, owner) > 10

    app.dependency_overrides[verify_clerk_session] = lambda: test.user
    assert client.get("/api/account").json()["deletable"] is False
