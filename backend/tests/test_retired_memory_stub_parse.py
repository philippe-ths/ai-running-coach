"""M4 (ADR 0025): the retired believed_facts / preference_profile / narrative pack
sections are kept as never-populated Optional STUBS so a historical stored pack
that carries them still strict-parses under `extra="forbid"` (the chat read path
and the eval harness load old packs), while a freshly built pack never serializes
them again.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User
from app.schemas.coach_context import CoachContextPack
from app.services.coach.context import build_context_pack

V12 = "coach_message_v12"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id):
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a); db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort="easy", duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
    ))
    db.flush()
    return a


def test_fresh_pack_never_serializes_the_retired_sections(db):
    """A newly built pack drops believed_facts / preference_profile / narrative
    entirely (never populated, None -> dropped by the PACK_SECTIONS registry)."""
    act = _activity(db, _user(db))
    serialized = build_context_pack(db, act, prompt_id=V12).to_serializable_dict()
    assert "believed_facts" not in serialized
    assert "preference_profile" not in serialized
    assert "narrative" not in serialized


def test_pre_m4_stored_pack_with_retired_keys_still_strict_parses(db):
    """A stored pack JSON from before M4 still carries the three keys with real
    objects. The Optional stub fields must accept them under extra='forbid' so the
    chat read path + eval harness can still load historical packs."""
    act = _activity(db, _user(db))
    stored = build_context_pack(db, act, prompt_id=V12).to_serializable_dict()

    # Inject the shapes a pre-M4 pack carried (the M8 belief loop + M10 preference +
    # A2c narrative), exactly as they would sit in an old coach_reports.context_pack.
    stored["believed_facts"] = {
        "facts": [{
            "kind": "hr_confound",
            "statement": "HR reads inflated in heat",
            "confidence": "high", "observed_count": 4, "last_seen_days_ago": 3,
        }],
    }
    stored["preference_profile"] = {
        "themes": [{"theme": "add_quality", "tendency": "acts_on", "acted": 4, "total": 5}],
    }
    stored["narrative"] = {
        "narrative": "Consistent aerobic base builder.",
        "source_report_count": 6,
    }

    pack = CoachContextPack.model_validate(stored)
    assert pack.believed_facts is not None and len(pack.believed_facts.facts) == 1
    assert pack.preference_profile is not None and len(pack.preference_profile.themes) == 1
    assert pack.narrative is not None and pack.narrative.narrative == "Consistent aerobic base builder."
