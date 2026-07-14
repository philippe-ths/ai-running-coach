"""ADR 0026 Slice 4 (#680): the coach-native leaf reframing in the REAL pack.

Pinned here: the grouped-v4 prompt's canonical (stored) pack is byte-identical to
grouped-v3's (framing adds no section and changes no stored fact); the grouped-v4
OUTGOING LLM view is reframed to coach-native units (km / % max / MM:SS); and the
service serialization helper frames ONLY under the metrics-coach-framed prompt, so every
prior prompt's LLM message is byte-identical to the pre-Slice-4 serialization.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import build_context_pack
from app.services.coach.coach_framing import frame_pack
from app.services.coach.service import _llm_pack_message

GROUPED3 = "coach_message_lean_grouped_v3"
GROUPED4 = "coach_message_lean_grouped_v4"
LEAN = "coach_message_lean_v1"
V14 = "coach_message_v14"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.add(UserProfile(user_id=uid, goal_type="general_fitness", experience_level="intermediate",
                       weekly_days_available=5, max_hr=190, max_hr_source="user", current_weekly_km=40))
    db.flush()
    return uid


def _activity(db, user_id, *, days_ago=0, effort="easy", time_in_zones=None):
    start = datetime(2026, 6, 28, 8, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=start, start_date_local=start,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.4, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort=effort, duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=42.0,
        hr_drift=8.0, pace_variability=12.8, confidence="high", confidence_reasons=[],
        flags=[], time_in_zones=time_in_zones or {"Z1": 12, "Z2": 436, "Z3": 521},
        discount_signals=None,
    ))
    db.flush()
    return a


def _seed_window(db, uid, *, n=6):
    for i in range(n):
        _activity(db, uid, days_ago=2 + i, effort="easy")


def test_grouped_v4_canonical_pack_equals_grouped_v3(db):
    """Framing is a serialization VIEW, not a section: the stored/canonical grouped pack
    under v4 is byte-identical to v3, so re-parse/validator/eval are unaffected."""
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    d3 = build_context_pack(db, subject, prompt_id=GROUPED3).to_grouped_dict()
    d4 = build_context_pack(db, subject, prompt_id=GROUPED4).to_grouped_dict()
    assert d4 == d3


def test_grouped_v4_llm_view_is_coach_framed(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    canonical = build_context_pack(db, subject, prompt_id=GROUPED4).to_grouped_dict()
    view = frame_pack(canonical)

    act = view["this_run"]["activity"]
    assert act["distance_km"] == 10.0 and "distance_m" not in act
    assert act["duration"] == "1h00m" and "moving_time_s" not in act
    assert act["avg_hr"] == "150 bpm (79% max)"     # 150/190
    assert act["max_hr"] == "175 bpm (92% max)"
    assert act["avg_cadence"] == 170

    m = view["this_run"]["metrics"]
    assert m["effort_score"] == 42 and isinstance(m["effort_score"], int)
    assert m["time_in_zones"] == {"Z1": "0:12", "Z2": "7:16", "Z3": "8:41"}

    # the canonical pack still carries the raw leaves (framing did not mutate it)
    assert canonical["this_run"]["activity"]["distance_m"] == 10000


def test_service_helper_frames_only_under_the_feature(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    canonical = build_context_pack(db, subject, prompt_id=GROUPED4).to_grouped_dict()

    # Non-framed prompts: byte-identical to the prior plain json.dumps (no leak).
    plain = json.dumps(canonical, default=str)
    assert _llm_pack_message(canonical, GROUPED3) == plain
    assert _llm_pack_message(canonical, LEAN) == plain
    assert _llm_pack_message(canonical, V14) == plain
    assert _llm_pack_message(canonical, None) == plain

    # Framed prompt: the reframed view.
    assert _llm_pack_message(canonical, GROUPED4) == json.dumps(frame_pack(canonical), default=str)
    assert _llm_pack_message(canonical, GROUPED4) != plain
