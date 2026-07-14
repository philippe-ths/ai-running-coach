"""ADR 0026 Slice 5 (#682): the salience drop from the fuller LLM view in the REAL pack.

Pinned here: grouped_v5's canonical (stored) pack is byte-identical to grouped_v4's (the
drop is a view transform, not a section change, so re-parse/validator/eval are unaffected);
grouped_v5's FULLER LLM view has no `salience` key while its OPENER view keeps it (the drop
is fuller-only, so the opener prose that reads salience.novelty stays valid); and every prior
prompt's LLM view still carries salience (byte-stable under a rollback). The canonical pack
keeps salience throughout, so the deterministic safety force reading it is untouched.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import build_context_pack
from app.services.coach.service import _llm_pack_message

GROUPED4 = "coach_message_lean_grouped_v4"
GROUPED5 = "coach_message_lean_grouped_v5"
LEAN = "coach_message_lean_v1"
V14 = "coach_message_v14"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.add(UserProfile(user_id=uid, goal_type="general_fitness", experience_level="intermediate",
                       weekly_days_available=5, max_hr=190, max_hr_source="user", current_weekly_km=40))
    db.flush()
    return uid


def _activity(db, user_id, *, days_ago=0, effort="easy"):
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
        flags=[], time_in_zones={"Z1": 12, "Z2": 436, "Z3": 521}, discount_signals=None,
    ))
    db.flush()
    return a


def _seed_window(db, uid, *, n=6):
    for i in range(n):
        _activity(db, uid, days_ago=2 + i, effort="easy")


def test_grouped_v5_canonical_pack_equals_grouped_v4(db):
    """The salience drop is a serialization VIEW, not a section: the stored/canonical grouped
    pack under v5 is byte-identical to v4, and it still carries the `salience` section (so the
    deterministic safety force reading it, the validator, and the eval are all unaffected)."""
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    d4 = build_context_pack(db, subject, prompt_id=GROUPED4).to_grouped_dict()
    d5 = build_context_pack(db, subject, prompt_id=GROUPED5).to_grouped_dict()
    assert d5 == d4
    assert "salience" in d5  # canonical keeps it


def test_grouped_v5_fuller_view_drops_salience_opener_keeps_it(db):
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    canonical = build_context_pack(db, subject, prompt_id=GROUPED5).to_grouped_dict()
    assert "salience" in canonical

    # Fuller view (default mode): salience removed.
    fuller = json.loads(_llm_pack_message(canonical, GROUPED5))
    assert "salience" not in fuller

    # Opener view: salience retained (the opener prose reads salience.novelty).
    opener = json.loads(_llm_pack_message(canonical, GROUPED5, mode="opener"))
    assert "salience" in opener

    # The canonical dict was not mutated by either view.
    assert "salience" in canonical


def test_prior_prompts_keep_salience_in_the_llm_view(db):
    """Byte-stability: every non-salience-dropped prompt's fuller LLM view still carries
    salience, so a rollback (or any prior grouped prompt) is unchanged."""
    uid = _user(db)
    subject = _activity(db, uid, days_ago=0, effort="hard")
    _seed_window(db, uid)

    grouped4_canonical = build_context_pack(db, subject, prompt_id=GROUPED4).to_grouped_dict()
    assert "salience" in json.loads(_llm_pack_message(grouped4_canonical, GROUPED4))

    flat_canonical = build_context_pack(db, subject, prompt_id=V14).to_serializable_dict()
    for pid in (V14, LEAN, None):
        assert "salience" in json.loads(_llm_pack_message(flat_canonical, pid))
