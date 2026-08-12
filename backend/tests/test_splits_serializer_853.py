"""#853: `GET /api/activities/{id}` warned "serialized value may not be as
expected" for `splits` because `calculate_splits` returns plain dicts and the
endpoint assigned them straight onto `ActivityDetailRead.splits` (a declared
`List[SplitRead]`) without validation — plain attribute assignment on a
Pydantic model does not validate unless `validate_assignment` is set, so the
field held a `dict` at serialization time against a schema that says
`SplitRead`.

This reproduces the warning through the real endpoint (not a mirror of the
validators), and pins that the fix (validating each split dict against
`SplitRead` before assignment) both silences the warning AND changes no
value in the response — the #853 finding that the mismatch was cosmetic: the
dicts `calculate_splits` builds already carry exactly `SplitRead`'s fields.
"""

import uuid
import warnings
from datetime import datetime, timezone

from app.models import Activity, ActivityStream, DerivedMetric, User

_BASE = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)


def _seed_activity_with_distance_stream(db) -> Activity:
    user = User(email=f"splits-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()

    activity = Activity(
        user_id=user.id,
        strava_activity_id=int(_BASE.timestamp()) + uuid.uuid4().int % 100_000,
        start_date=_BASE, type="Run", name="Morning run",
        distance_m=2500, moving_time_s=1000, elapsed_time_s=1000,
        elev_gain_m=5.0, avg_hr=150, average_speed_mps=2.5, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort_score=30.0, confidence="high",
        flags=[], confidence_reasons=[],
    ))
    # A constant-pace 1 Hz stream long enough to produce two full 1 km splits
    # plus a partial (mirrors test_distance_splits.py's ground truth).
    time = list(range(1001))
    distance = [2.5 * t for t in time]
    heartrate = [150] * len(time)
    db.add(ActivityStream(activity_id=activity.id, stream_type="time", data=time))
    db.add(ActivityStream(activity_id=activity.id, stream_type="distance", data=distance))
    db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=heartrate))
    db.commit()
    db.refresh(activity)
    return activity


def test_activity_detail_splits_serialize_without_warning(client, db):
    """The declared `SplitRead` shape and what is actually served must agree."""
    activity = _seed_activity_with_distance_stream(db)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resp = client.get(f"/api/activities/{activity.id}")

    assert resp.status_code == 200, resp.text

    serializer_warnings = [
        w for w in caught
        if "PydanticSerializationUnexpectedValue" in str(w.message)
        or "serialized value may not be as expected" in str(w.message)
    ]
    assert not serializer_warnings, (
        f"splits serialized against an unexpected shape: {serializer_warnings}"
    )


def test_activity_detail_splits_values_unchanged_by_validation(client, db):
    """Validating each split against `SplitRead` must not drop or reshape a
    field — pinning the #853 finding that the mismatch was cosmetic."""
    activity = _seed_activity_with_distance_stream(db)

    resp = client.get(f"/api/activities/{activity.id}")
    assert resp.status_code == 200, resp.text
    splits = resp.json()["splits"]

    assert len(splits) == 3  # two full 1 km splits + a 500 m partial
    full = splits[0]
    assert full["split"] == 1
    assert full["split_type"] == "distance"
    assert abs(full["distance"] - 997.5) < 1e-6
    assert full["elapsed_time"] == 399
    assert full["avg_hr"] == 150
    # Every field SplitRead declares must survive, even when null.
    for key in (
        "split", "split_type", "distance", "elapsed_time", "pace", "speed",
        "avg_hr", "avg_grade", "avg_cadence", "avg_watts", "elev_gain",
    ):
        assert key in full, f"{key} missing from a served split"
