"""P4 user-materials in the corpus pack section (#286, ADR 0017).

The runner's distilled materials ride INSIDE the existing `corpus` section as a
`user_materials` sub-field, populated ONLY under a user-materials-aware prompt
(coach_message_v7). These tests pin:
  - under v7 the corpus section carries the runner's active materials (load-bearing);
  - under v4/v5/v6 the materials NEVER leak — the corpus section is byte-identical to
    its P1.2/P1.3 shape even when the runner HAS active materials (the activation
    boundary: materials take effect only at v7);
  - the materials change ONLY the corpus section — every fact stays put;
  - the materials never reach the DerivedMetric or the safety floor.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.user_material import UserMaterial
from app.services.coach.context import build_context_pack

V4 = "coach_message_v4"
V5 = "coach_message_v5"
V6 = "coach_message_v6"
V7 = "coach_message_v7"

_DISTILLED = {
    "stance": "Run on feel; chase the joy of the trail.",
    "principles": ["Time on feet over pace.", "Hills are strength work."],
    "method_framing": "Frame every run by how sustainable and enjoyable it is.",
    "emphasis_hints": ["enjoyment", "time on feet"],
}


def _seed_activity(db) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"umat_{user_id}@example.com"))
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


def _add_material(db, user_id, *, day: int = 10, distilled=None, hash_suffix: str = "1"):
    db.add(UserMaterial(
        id=uuid.uuid4(),
        user_id=user_id,
        kind="philosophy",
        title="My approach",
        filename="approach.md",
        raw_text="(untrusted source — never read into the pack)",
        distilled=dict(_DISTILLED) if distilled is None else distilled,
        status="active",
        content_hash=f"hash-{user_id}-{hash_suffix}",
        created_at=datetime(2026, 5, day, 8, 0, tzinfo=timezone.utc),
    ))
    db.flush()


def test_v7_corpus_section_carries_active_user_materials(db):
    """Under v7 the corpus section carries the runner's active distilled materials."""
    activity = _seed_activity(db)
    _add_material(db, activity.user_id)
    pack = build_context_pack(db, activity, prompt_id=V7)

    assert pack.corpus is not None
    assert pack.corpus.user_materials is not None
    assert len(pack.corpus.user_materials) == 1
    m = pack.corpus.user_materials[0]
    assert m.stance == _DISTILLED["stance"]
    assert list(m.principles) == _DISTILLED["principles"]
    # it serializes inside the corpus section
    corpus_dict = pack.to_serializable_dict()["corpus"]
    assert "user_materials" in corpus_dict
    assert corpus_dict["user_materials"][0]["stance"] == _DISTILLED["stance"]
    # the house school is still present (augment, not replace)
    assert corpus_dict["school"] is not None
    assert corpus_dict["house_principles"]


def test_v7_with_no_materials_emits_empty_list(db):
    """Under v7 a runner with no active materials gets an empty list (present, not a
    dropped key) — the section is materials-aware, the runner simply has none yet."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V7)
    assert pack.corpus is not None
    assert pack.corpus.user_materials == []
    assert "user_materials" in pack.to_serializable_dict()["corpus"]


def test_materials_never_leak_into_v4_v5_v6_even_when_present(db):
    """Activation boundary: under v4/v5/v6 the materials NEVER appear — the corpus
    section keeps its exact P1.2/P1.3 shape (keys house_principles + school only)
    even when the runner HAS active materials. This is the byte-stability guarantee
    prod (running v6) depends on."""
    activity = _seed_activity(db)
    _add_material(db, activity.user_id)

    for prompt in (V4, V5, V6):
        pack = build_context_pack(db, activity, prompt_id=prompt)
        assert pack.corpus is not None
        assert pack.corpus.user_materials is None
        corpus_dict = pack.to_serializable_dict()["corpus"]
        assert "user_materials" not in corpus_dict, f"materials leaked under {prompt}"
        # the corpus section shape is exactly the pre-#286 shape
        assert set(corpus_dict.keys()) == {"house_principles", "school"}


def test_v6_fingerprint_is_unchanged_by_the_presence_of_materials(db):
    """A runner uploading a material must NOT shift their v6 cache identity — the
    materials are inert under v6, so the v6 fingerprint is identical with and without
    an active material present."""
    activity = _seed_activity(db)
    fp_before = build_context_pack(db, activity, prompt_id=V6).fingerprint()
    _add_material(db, activity.user_id)
    fp_after = build_context_pack(db, activity, prompt_id=V6).fingerprint()
    assert fp_after == fp_before


def test_v7_materials_change_only_the_corpus_section(db):
    """The materials add ONLY to the corpus section — every fact (activity, metrics,
    calibration, ...) is byte-identical between a v7 pack with materials and the v6
    pack (whose corpus section is materials-free)."""
    activity = _seed_activity(db)
    _add_material(db, activity.user_id)

    v6_dict = build_context_pack(db, activity, prompt_id=V6).to_serializable_dict()
    v7_dict = build_context_pack(db, activity, prompt_id=V7).to_serializable_dict()

    # the run's facts are identical; only the corpus section differs
    v6_no_corpus = {k: v for k, v in v6_dict.items() if k != "corpus"}
    v7_no_corpus = {k: v for k, v in v7_dict.items() if k != "corpus"}
    assert v7_no_corpus == v6_no_corpus
    # and the difference is exactly the user_materials sub-field
    assert "user_materials" in v7_dict["corpus"]
    assert "user_materials" not in v6_dict["corpus"]
