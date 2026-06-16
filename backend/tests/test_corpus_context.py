"""P1.2 corpus context-pack section (ADR 0014).

The `corpus` section is emitted ONLY under a corpus-aware prompt id, mirroring the
`block`-section idiom (Optional[...] = None, dropped from serialization when None).
These tests pin:
  - AC2: under coach_message_v4 the pack carries a `corpus` section with the house
    core + the keyed default school (aerobic-base);
  - AC1/AC7: under any non-corpus prompt (or no prompt_id) the section is absent and
    the pack is byte-identical (fingerprint-stable) to today;
  - AC2: the run's facts/DerivedMetric in the pack are unchanged by the corpus;
  - AC5: with no school resolvable the section degrades to house-core-only and the
    pack still builds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import _build_corpus_context, build_context_pack
from app.services.coach.corpus import DEFAULT_SCHOOL_ID, SCHOOLS, HOUSE_CORE

V4 = "coach_message_v4"


def _seed_activity(db) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"corpus_{user_id}@example.com"))
    db.flush()
    activity = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Easy Tuesday",
        type="Run",
        start_date=datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=50.0,
        average_speed_mps=2.78,
    )
    db.add(activity)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort="easy",
        duration_class="standard",
        structure="continuous",
        is_hilly=False,
        is_race=False,
        effort_score=5.5,
        hr_drift=3.1,
        flags=[],
        confidence="high",
        confidence_reasons=["full_coverage"],
    ))
    db.add(UserProfile(
        user_id=user_id, goal_type="half", experience_level="intermediate",
        weekly_days_available=4, current_weekly_km=35,
        max_hr=185, max_hr_source="user_entered",
    ))
    db.flush()
    db.refresh(activity)
    return activity


def test_corpus_section_present_under_v4_with_house_core_and_default_school(db):
    """AC2: under coach_message_v4 the pack carries the corpus section: the house
    principles plus the keyed default school (aerobic-base)."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V4)

    assert pack.corpus is not None
    assert list(pack.corpus.house_principles) == list(HOUSE_CORE.principles)
    assert pack.corpus.school is not None
    expected = SCHOOLS[DEFAULT_SCHOOL_ID]
    assert pack.corpus.school.id == DEFAULT_SCHOOL_ID
    assert pack.corpus.school.name == expected.name
    assert pack.corpus.school.stance == expected.stance
    assert pack.corpus.school.method_framing == expected.method_framing
    # every structured field is carried through faithfully (tuple -> list in the pack)
    assert list(pack.corpus.school.principles) == list(expected.principles)
    assert list(pack.corpus.school.emphasis_hints) == list(expected.emphasis_hints)
    # the section serializes (present key under v4)
    assert "corpus" in pack.to_serializable_dict()


def test_corpus_changes_the_fingerprint_under_v4(db):
    """The corpus section is load-bearing in the cache identity: adding it under v4
    changes the pack fingerprint (so v4 reports regenerate), even though the pack is
    byte-stable for every non-corpus prompt."""
    activity = _seed_activity(db)
    v3_fp = build_context_pack(db, activity, prompt_id="coach_message_v3").fingerprint()
    v4_fp = build_context_pack(db, activity, prompt_id=V4).fingerprint()
    assert v4_fp != v3_fp


def test_corpus_section_absent_and_byte_stable_for_non_corpus_prompts(db):
    """AC1/AC7: no prompt_id and a non-corpus prompt_id both omit the corpus key, and
    the pack is byte-identical (fingerprint-stable) to the no-prompt pack."""
    activity = _seed_activity(db)

    base = build_context_pack(db, activity)                              # no prompt_id
    base_dict = base.to_serializable_dict()
    assert "corpus" not in base_dict

    for non_corpus in (None, "coach_message_v3", "coach_message_v2", "coach_report_v10"):
        pack = build_context_pack(db, activity, prompt_id=non_corpus)
        d = pack.to_serializable_dict()
        assert "corpus" not in d, f"corpus leaked under {non_corpus}"
        assert d == base_dict, f"pack moved under {non_corpus}"
        assert pack.fingerprint() == base.fingerprint(), f"fingerprint moved under {non_corpus}"


def test_corpus_does_not_touch_the_facts(db):
    """AC2: adding the corpus section changes ONLY the corpus key — every fact
    (activity, metrics/DerivedMetric, calibration, ...) is byte-identical."""
    activity = _seed_activity(db)
    base_dict = build_context_pack(db, activity).to_serializable_dict()  # no corpus
    v4_dict = build_context_pack(db, activity, prompt_id=V4).to_serializable_dict()

    assert "corpus" in v4_dict
    v4_without_corpus = {k: v for k, v in v4_dict.items() if k != "corpus"}
    assert v4_without_corpus == base_dict


def test_corpus_degrades_to_house_core_only_when_no_school_resolves():
    """AC5: with no school resolvable the section is still built — house principles
    present, school None — so the exchange still generates."""
    # V4 is not a user-materials prompt, so the materials read is never performed —
    # db/activity are unused and passing None exercises the pure school-lookup path.
    section = _build_corpus_context(None, None, V4, school_id="no-such-school")
    assert section is not None
    assert list(section.house_principles) == list(HOUSE_CORE.principles)
    assert section.school is None
    # P4 byte-stability: materials are inert under v4, so the sub-field stays None.
    assert section.user_materials is None


def test_build_corpus_context_returns_none_for_non_corpus_prompt():
    """The gate is the prompt id: a non-corpus prompt yields no section at all."""
    assert _build_corpus_context(None, None, "coach_message_v3") is None
    assert _build_corpus_context(None, None, None) is None
