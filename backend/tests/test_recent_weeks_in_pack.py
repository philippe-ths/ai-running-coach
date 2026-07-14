"""ADR 0026 Slice 2 (#670): the redefined `right_now` content in the coach pack.

Under the new grouped prompt (coach_message_lean_grouped_v2) the pack carries
`readiness` (renamed training_load) + the merged day-resolved `recent_weeks`, and DROPS
the three old sections. Under every prior prompt (prod lean_v1, grouped_v1, v8) the old
three still emit and the two new ones are absent — so the prior packs stay byte-stable.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User
from app.schemas.coach_context import CoachContextPack
from app.services.coach.context import build_context_pack

_NEW = ("readiness", "recent_weeks")
_OLD = ("training_load", "training_volume", "recent_training")


def _seed(db) -> Activity:
    user = User(id=uuid.uuid4(), email=f"rw-{uuid.uuid4()}@example.com")
    db.add(user)
    db.flush()
    # A short history so readiness and recent_weeks both have content to populate.
    base = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    subject = None
    for i in range(10):
        start = base - timedelta(days=i * 2)
        act = Activity(
            id=uuid.uuid4(),
            user_id=user.id,
            strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
            name="Run",
            type="Run",
            start_date=start,
            start_date_local=start,
            distance_m=10000,
            moving_time_s=3000,
            elapsed_time_s=3000,
            avg_hr=150.0,
            raw_summary={},
        )
        db.add(act)
        db.flush()
        db.add(
            DerivedMetric(
                id=uuid.uuid4(),
                activity_id=act.id,
                effort="easy",
                structure="continuous",
                duration_class="standard",
                effort_score=60.0,
                hr_drift=3.0,
                flags=[],
                confidence="high",
                confidence_reasons=[],
            )
        )
        db.flush()
        if i == 0:
            subject = act
    db.refresh(subject)
    return subject


def test_grouped_v2_pack_carries_redefined_right_now_content(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id="coach_message_lean_grouped_v2")

    # The two redefined sections are present; the three old ones are dropped.
    assert pack.recent_weeks is not None
    assert pack.readiness is not None
    assert pack.training_load is None
    assert pack.training_volume is None
    assert pack.recent_training is None

    flat = pack.to_serializable_dict()
    for k in _NEW:
        assert k in flat
    for k in _OLD:
        assert k not in flat

    # Grouped serving nests both under right_now.
    grouped = pack.to_grouped_dict()
    assert "recent_weeks" in grouped["right_now"]
    assert "readiness" in grouped["right_now"]
    for k in _OLD:
        assert k not in grouped["right_now"]


def test_prior_prompt_pack_is_unchanged_and_byte_stable(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id="coach_message_lean_v1")

    # The prior shape: the old three sections, and NEITHER new field.
    assert pack.readiness is None
    assert pack.recent_weeks is None
    assert pack.training_load is not None
    assert pack.recent_training is not None

    flat = pack.to_serializable_dict()
    # The new RightNow fields must not leak a key into the prior serialized pack.
    for k in _NEW:
        assert k not in flat
    for k in _OLD:
        assert k in flat


def test_recent_weeks_absent_under_default_no_prompt(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity)
    assert pack.recent_weeks is None
    assert pack.readiness is None
    assert "recent_weeks" not in pack.to_serializable_dict()
    assert "readiness" not in pack.to_serializable_dict()


def test_recent_weeks_sparse_nulls_are_stripped(db):
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id="coach_message_lean_grouped_v2")
    rw = pack.to_serializable_dict()["recent_weeks"]
    # A run with no check-in / no elevation must not carry null rpe/pain/notes/elev keys.
    a_day = next(d for d in rw["this_week"]["days"] if d.get("activities"))
    act = a_day["activities"][0]
    for absent in ("rpe", "pain", "notes", "shape", "long_run"):
        assert absent not in act
    # A rest day carries no null day_totals key.
    rest_day = next((d for d in rw["this_week"]["days"] if d.get("rest")), None)
    if rest_day is not None:
        assert "day_totals" not in rest_day


def test_grouped_v2_pack_strict_reparses(db):
    """The chat read path and the eval harness strict-re-parse a STORED pack. Both the
    grouped and flat serializations of a grouped_v2 pack (nulls stripped) must round-trip
    through CoachContextPack.load() without an extra=forbid rejection."""
    activity = _seed(db)
    pack = build_context_pack(db, activity, prompt_id="coach_message_lean_grouped_v2")
    for shape in (pack.to_grouped_dict(), pack.to_serializable_dict()):
        reloaded = CoachContextPack.load(shape)
        assert reloaded.recent_weeks is not None
        assert reloaded.readiness is not None
