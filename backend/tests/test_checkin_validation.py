"""Server-side range validation at the check-in write boundary (#156).

Tappable RPE/pain input (I1a) is a new write path for these exact fields, so the
schema must reject out-of-range values (422) rather than store them and let the
downstream perceived-effort code clamp silently. Domain ranges per CONTEXT.md:
RPE 1-10 (Borg), pain_score 0-10.
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.models import Activity, User


def _user(db):
    u = User(id=uuid4(), email=f"u-{uuid4()}@example.com")
    db.add(u)
    db.flush()
    return u.id


def _activity(db, uid):
    a = Activity(
        id=uuid4(), user_id=uid, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Run",
        distance_m=5000, moving_time_s=1500, elapsed_time_s=1500, elev_gain_m=10.0,
        avg_hr=140, raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


@pytest.fixture
def activity_id(db):
    a = _activity(db, _user(db))
    db.commit()
    return a.id


@pytest.mark.parametrize(
    "payload",
    [
        {"rpe": 0},          # below Borg floor
        {"rpe": 11},         # above Borg ceiling
        {"rpe": -3},
        {"pain_score": -1},  # below floor
        {"pain_score": 11},  # above ceiling
    ],
)
def test_out_of_range_is_rejected(db, client, activity_id, payload):
    resp = client.post(f"/api/activities/{activity_id}/checkin", json=payload)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"rpe": 1},
        {"rpe": 10},
        {"pain_score": 0},
        {"pain_score": 10},
        {"rpe": 7, "pain_score": 2},
        {"rpe": None, "pain_score": None},  # null is still allowed (no check-in value)
        {"notes": "felt easy"},             # other fields unaffected
    ],
)
def test_in_range_is_accepted(db, client, activity_id, payload):
    # Isolate the validation boundary from the endpoint's downstream side effects
    # (re-analysis + reply-path enqueue), which are exercised elsewhere.
    with patch("app.api.activities.analysis.analyze"), patch(
        "app.jobs.process_new_activity.maybe_enqueue_fuller_turn"
    ):
        resp = client.post(f"/api/activities/{activity_id}/checkin", json=payload)
    assert resp.status_code == 200
