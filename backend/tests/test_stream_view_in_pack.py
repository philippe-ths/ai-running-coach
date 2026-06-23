"""#443: the consolidated stream view is carried in the DEFAULT coach pack under a
stream-view-aware prompt (coach_message_v10), and absent (byte-stable) under v9 and
every earlier prompt.

Before #443 the stream view was built and stored on every analysis but reached the
coach pack nowhere (CoachContextPack had no such field). Now it rides the default
pack under v10 via the Optional-and-drop idiom, so v9 and below stay byte-identical.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import build_context_pack

_STREAM_VIEW = {
    "n_points": 3,
    "source_n": 180,
    "time_s": [0, 60, 120],
    "hr": [140, 150, 160],
    "pace_s_per_km": [300, 305, 310],
    "grade_pct": None,
    "cadence_spm": None,
}


def _seed(db) -> Activity:
    user = User(id=uuid.uuid4(), email=f"sv-{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    activity = Activity(
        id=uuid.uuid4(),
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run",
        type="Run",
        start_date=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3600,
        avg_hr=150.0,
        raw_summary={},
    )
    db.add(activity)
    db.flush()
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort="easy",
        structure="continuous",
        duration_class="standard",
        effort_score=80.0,
        flags=[],
        confidence="high",
        confidence_reasons=[],
        stream_view=_STREAM_VIEW,
    )
    db.add(dm)
    db.flush()
    db.refresh(activity)
    return activity


def test_stream_view_present_in_default_pack_under_v10(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id="coach_message_v10")
    assert pack.stream_view is not None
    assert pack.stream_view["n_points"] == 3
    # ...and it survives serialization into the user message.
    assert "stream_view" in pack.to_serializable_dict()


def test_stream_view_absent_under_v9_and_default(db):
    activity = _seed(db)

    pack_v9 = build_context_pack(db, activity, prompt_id="coach_message_v9")
    assert pack_v9.stream_view is None
    assert "stream_view" not in pack_v9.to_serializable_dict()

    # the default no-prompt path (callers that pass no prompt_id) is byte-stable too
    pack_default = build_context_pack(db, activity)
    assert pack_default.stream_view is None
    assert "stream_view" not in pack_default.to_serializable_dict()


def test_v10_pack_without_a_stored_view_degrades_to_absent(db):
    """A stream-view prompt on an activity whose metrics carry no stored view (e.g. a
    summary-only import) simply omits the section — the addendum tells the coach to
    reason from the scalar metrics."""
    activity = _seed(db)
    # Wipe the stored view, mimicking an activity analysed without streams.
    activity.metrics.stream_view = None
    db.flush()

    pack = build_context_pack(db, activity, prompt_id="coach_message_v10")
    assert pack.stream_view is None
    assert "stream_view" not in pack.to_serializable_dict()
