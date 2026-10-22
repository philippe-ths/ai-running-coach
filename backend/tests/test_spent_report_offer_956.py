"""#956: confirming a report's offer spends the offer, not only its token.

Single use was a property of the Redis token, and every read of the report
minted a new one: confirm with token 1, reload, and token 2 applied the same
change again. The spend is now recorded on the report row, beside the stored
report rather than inside it, so it survives a Redis flush and a re-read does not
bring the card back.
"""

from unittest.mock import patch

import pytest

from app.core.clerk_auth import verify_clerk_session
from app.main import app
from app.models.coach_report import CoachReport
from app.services.coach import proposed_actions
from tests import test_report_offer_944 as offers
from tests.test_report_offer_944 import _FakeRedis


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(verify_clerk_session, None)


def _report_url(activity):
    return f"/api/activities/{activity.id}/coach-report"


def _row(db, activity) -> CoachReport:
    row = db.query(CoachReport).filter(CoachReport.activity_id == activity.id).one()
    db.refresh(row)
    return row


def _confirm(client, token):
    return client.post("/api/coach/threads/actions/confirm", json={"token": token})


def test_a_confirmed_offer_is_not_offered_again_even_after_a_redis_flush(client, db):
    _user, activity, _upcoming = offers.TestConfirm()._setup(client, db)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        token = client.get(_report_url(activity)).json()["offer"]["token"]
        assert _confirm(client, token).status_code == 200

    # A fresh token store: nothing about the spend may live in Redis.
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        reread = client.get(_report_url(activity)).json()

    assert reread["offer"] is None
    row = _row(db, activity)
    assert row.offer_confirmed_at is not None
    # The stored report is still what the coach wrote, offer included.
    assert row.report["offer"]["action_type"] == "adjust_session"


def test_a_second_card_for_the_same_offer_cannot_be_confirmed(client, db):
    """Two reads before either confirm: two live tokens for one offer."""
    _user, activity, _upcoming = offers.TestConfirm()._setup(client, db)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        first = client.get(_report_url(activity)).json()["offer"]["token"]
        second = client.get(_report_url(activity)).json()["offer"]["token"]

        assert _confirm(client, first).status_code == 200
        again = _confirm(client, second)

    assert again.status_code == 409
    assert "already confirmed" in again.json()["detail"]


def test_an_offer_never_confirmed_is_untouched(client, db):
    _user, activity, _upcoming = offers.TestConfirm()._setup(client, db)
    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        first = client.get(_report_url(activity)).json()["offer"]
        second = client.get(_report_url(activity)).json()["offer"]

    assert first["token"] and second["token"]
    assert _row(db, activity).offer_confirmed_at is None


def test_a_confirm_whose_change_fails_leaves_the_offer_live(client, db, monkeypatch):
    _user, activity, _upcoming = offers.TestConfirm()._setup(client, db)

    def _refused(*_a, **_k):
        raise ValueError("That session has already passed.")

    with patch.object(proposed_actions, "redis_conn", _FakeRedis()):
        token = client.get(_report_url(activity)).json()["offer"]["token"]
        monkeypatch.setattr(proposed_actions, "_execute", _refused)
        assert _confirm(client, token).status_code == 422
        monkeypatch.undo()
        reread = client.get(_report_url(activity)).json()

    assert _row(db, activity).offer_confirmed_at is None
    assert reread["offer"]["token"]
