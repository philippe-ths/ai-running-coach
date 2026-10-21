"""#969: the guards in front of account deletion, now that a button reaches it.

The coaching history deletion destroys exists nowhere else, and the owner
routinely holds test accounts that look identical on screen. So the request must
carry the account's own email, and the deployment owner's account is never
deleted through the app.
"""

from unittest.mock import patch

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.main import app
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


def test_the_owners_account_is_never_deleted_here(client, db, monkeypatch):
    owner, _ = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", "Owner@Del.dev")
    before = _residual_rows(db, owner)

    resp, clerk = _delete(client, owner, owner.email)

    assert resp.status_code == 403
    clerk.assert_not_called()
    assert _residual_rows(db, owner) == before


def test_the_only_runner_of_an_unconfigured_deployment_is_its_owner(client, db, monkeypatch):
    monkeypatch.setattr(settings, "OWNER_EMAIL", "")
    only = _seed_full_user(db, email="solo@del.dev", athlete_id=3, strava_activity_id=33, hash_suffix="s")

    resp, clerk = _delete(client, only, only.email)

    assert resp.status_code == 403
    clerk.assert_not_called()


def test_the_wrong_accounts_email_deletes_nothing(client, db, monkeypatch):
    """The failure #969 names: signed into one account, thinking it is the other."""
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner.email)
    before = _residual_rows(db, test)

    resp, clerk = _delete(client, test, owner.email)

    assert resp.status_code == 400
    clerk.assert_not_called()
    assert _residual_rows(db, test) == before


def test_the_typed_email_is_matched_as_the_runner_would_type_it(client, db, monkeypatch):
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner.email)

    resp, clerk = _delete(client, test, "  TEST@del.dev ")

    assert resp.status_code == 204
    clerk.assert_called_once_with(test.email)
    assert _residual_rows(db, test) == 0
    assert _residual_rows(db, owner) > 10


def test_a_delete_without_the_typed_email_is_refused(client, db, monkeypatch):
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner.email)
    app.dependency_overrides[verify_clerk_session] = lambda: test.user

    resp = client.delete("/api/account")

    assert resp.status_code == 422
    assert _residual_rows(db, test) > 10


def test_the_account_read_says_whose_it_is_and_whether_it_can_go(client, db, monkeypatch):
    owner, test = _two_runners(db)
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner.email)

    app.dependency_overrides[verify_clerk_session] = lambda: owner.user
    as_owner = client.get("/api/account").json()
    app.dependency_overrides[verify_clerk_session] = lambda: test.user
    as_test = client.get("/api/account").json()

    assert as_owner == {"email": owner.email, "deletable": False}
    assert as_test == {"email": test.email, "deletable": True}
