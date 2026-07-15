"""#684: the two average-cadence figures on the activity page must agree.

The detail read-model normalizes cadence (single-leg spm -> two-leg spm) in
`@model_validator(mode="after")` methods. Those validators mutate `avg_cadence`
and the raw cadence stream and then re-read them, so they are NOT idempotent: the
`< 130 spm -> double` rule doubles an already-doubled value again. The endpoint
validated the model twice (an explicit `model_validate` plus FastAPI's
`response_model` re-validation), so `avg_cadence` was doubled twice (63 -> 126 ->
252) while the guarded `smoothed_cadence` recompute stayed at one doubling (126).
Result: Metrics panel showed 254 spm, the chart header 127 spm — exactly 2x.

These tests hit the real endpoint through the FastAPI test client, so they
exercise the actual double-validation, not a mirror of the validators.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, ActivityStream, DerivedMetric, User
from app.models.user_profile import UserProfile

_BASE = datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc)

# A realistic single-leg walking cadence stream (~63 spm one foot -> ~126 spm
# both feet). Constant so the expected two-leg value is unambiguous.
_SINGLE_LEG_SPM = 63.0
_N = 30


def _seed_activity_with_cadence(db) -> Activity:
    user = User(email=f"cad-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(user_id=user.id, goal_type="general",
                       experience_level="intermediate", weekly_days_available=4, max_hr=190))
    activity = Activity(
        user_id=user.id,
        strava_activity_id=int(_BASE.timestamp()) + uuid.uuid4().int % 100_000,
        start_date=_BASE, type="Walk", name="Evening walk",
        distance_m=5050, moving_time_s=2940, elapsed_time_s=2940, elev_gain_m=10.0,
        avg_hr=110, average_speed_mps=1.7, avg_cadence=_SINGLE_LEG_SPM, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort_score=30.0, confidence="high",
        flags=[], confidence_reasons=[],
    ))
    # Streams needed for smoothed_cadence generation: cadence + time (+ velocity/moving).
    db.add(ActivityStream(activity_id=activity.id, stream_type="cadence",
                          data=[_SINGLE_LEG_SPM] * _N))
    db.add(ActivityStream(activity_id=activity.id, stream_type="time",
                          data=list(range(_N))))
    db.add(ActivityStream(activity_id=activity.id, stream_type="velocity_smooth",
                          data=[1.7] * _N))
    db.add(ActivityStream(activity_id=activity.id, stream_type="moving",
                          data=[True] * _N))
    db.commit()
    db.refresh(activity)
    return activity


def _mean(values):
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def test_detail_avg_cadence_agrees_with_charted_stream(client, db):
    """The Metrics `avg_cadence` and the cadence chart's average (the mean of the
    `smoothed_cadence` stream) must be the same number, and it must be the
    plausible two-leg value (~126 spm), not double-doubled (~252)."""
    activity = _seed_activity_with_cadence(db)

    resp = client.get(f"/api/activities/{activity.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    avg_cadence = body["avg_cadence"]
    assert avg_cadence is not None

    streams = {s["stream_type"]: s["data"] for s in body["streams"]}
    assert "smoothed_cadence" in streams, "chart uses the smoothed_cadence stream"
    chart_avg = _mean(streams["smoothed_cadence"])
    assert chart_avg is not None

    # The two page figures must agree (the core of #684).
    assert abs(avg_cadence - chart_avg) < 1.0, (
        f"Metrics avg_cadence {avg_cadence} disagrees with chart avg {chart_avg}"
    )
    # And it must be doubled exactly once (single-leg -> two-leg), not twice.
    assert 110 <= avg_cadence <= 150, f"avg_cadence {avg_cadence} looks double-doubled"


def test_detail_raw_cadence_stream_not_double_doubled(client, db):
    """The raw cadence stream served for the chart's faint secondary line must be
    doubled once (~126), so the chart's Max is plausible rather than ~488."""
    activity = _seed_activity_with_cadence(db)

    resp = client.get(f"/api/activities/{activity.id}")
    assert resp.status_code == 200, resp.text
    streams = {s["stream_type"]: s["data"] for s in resp.json()["streams"]}

    raw = [v for v in streams["cadence"] if isinstance(v, (int, float))]
    assert raw, "raw cadence stream present"
    assert max(raw) <= 150, f"raw cadence max {max(raw)} is double-doubled"
