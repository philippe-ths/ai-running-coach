"""Characterisation tests for the typed CoachContextPack schema.

These tests pin the migration's load-bearing invariant: a typed pack must produce
the byte-identical cache hash the legacy dict-based pack produced, and round-trip
through model_validate / model_dump without losing or reordering fields.
"""

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, UserProfile
from app.models.user import User
from app.schemas.coach_context import CoachContextPack
from app.services.coach.context import build_context_pack, hash_context_pack


def _legacy_full_pack() -> dict:
    """A pack dict in the same shape build_context_pack produces, with every field populated."""
    return {
        "activity": {
            "date": "2026-02-15T10:00:00+00:00",
            "name": "Tempo Thursday",
            "type": "Run",
            "distance_m": 10000,
            "moving_time_s": 3600,
            "avg_hr": 150.0,
            "max_hr": 175.0,
            "avg_cadence": 170.0,
            "elev_gain_m": 50.0,
        },
        "metrics": {
            "headline": "Tempo run",
            "effort": "tempo",
            "duration_class": "standard",
            "structure": "continuous",
            "is_hilly": False,
            "is_race": False,
            "effort_score": 3.0,
            "hr_drift": 4.2,
            "pace_variability": 6.5,
            "flags": ["high_drift"],
            "confidence": "high",
            "confidence_reasons": ["full_stream_coverage"],
            "time_in_zones": {"Z1": 600, "Z2": 1200, "Z3": 600, "Z4": 300, "Z5": 60},
            "zones_calibrated": True,
            "zones_basis": "user_user_entered",
            "efficiency_analysis": {"power_per_hr": 1.42},
            "stops_analysis": {"count": 0},
            "interval_structure": None,
            "workout_match": {"detection_confidence": "low", "match_score": 0.4},
            "interval_kpis": None,
            "risk_level": "low",
            "risk_score": 2,
            "risk_reasons": [],
            "training_context": {
                "intensity_distribution_7d": {"easy": 3, "moderate": 1, "hard": 0},
                "days_since_last_hard": 4,
                "hard_sessions_this_week": 0,
            },
            "discount_signals": None,
        },
        "check_in": {
            "rpe": 6,
            "pain_score": 0,
            "pain_location": None,
            "sleep_quality": 7,
            "notes": "Felt strong.",
        },
        "profile": {
            "goal_type": "half",
            "experience_level": "intermediate",
            "weekly_days_available": 4,
            "injury_notes": None,
            "max_hr": 185,
            "max_hr_source": "user_entered",
            "current_weekly_km": 35,
        },
        "recent_training_summary": {
            "last_7d": {
                "activity_count": 3,
                "total_distance_m": 28000,
                "total_moving_time_s": 9800,
                "total_effort": 12.4,
            },
            "last_28d": {
                "activity_count": 12,
                "total_distance_m": 110000,
                "total_moving_time_s": 38000,
                "total_effort": 48.1,
            },
            "previous_28d": {
                "activity_count": 10,
                "total_distance_m": 95000,
                "total_moving_time_s": 33000,
                "total_effort": 41.0,
            },
        },
        "longitudinal": {
            "prior_reports": [
                {
                    "activity_date": "2026-02-12T10:00:00+00:00",
                    "headline": "Solid aerobic long run",
                    "lead_argument": "Aerobic durability is holding across the long run.",
                    "next_steps": ["Add a tempo segment (15 min at threshold)"],
                }
            ],
            "baseline_trend": {
                "bucket": "easy|flat|cool",
                "sample_count": 5,
                "efficiency_factor": {"direction": "improving", "magnitude_pct": 4.1, "slope": 0.01, "n": 5},
                "hr_drift": {"direction": "declining", "magnitude_pct": -3.0, "slope": -0.2, "n": 5},
            },
        },
        "perceived_effort": {
            # rpe / effort_score folded out (one-fact-one-place): they live in
            # check_in.rpe / metrics.effort_score. This is the canonical post-fold shape.
            "effort_axis": "tempo",
            "divergence": -1,
            "divergence_direction": "felt_easier",
            "hr_confounded": False,
            "recommended_weighting": "balanced",
            "pain_trend": {"abstained": False, "sample_count": 5, "direction": "stable", "magnitude_pct": 0.0, "slope": 0.0},
        },
        "adherence": {
            "prior_report_date": "2026-02-12T10:00:00+00:00",
            "outcomes": [
                {
                    "prior_action": "Add a tempo segment",
                    "theme": "add_quality",
                    "label": "acted_on",
                    "comparable_activity_date": "2026-02-14T10:00:00+00:00",
                    "basis": "a quality session followed on 2026-02-14 (tempo effort)",
                    "overridden": False,
                }
            ],
        },
        "believed_facts": {
            "facts": [
                {
                    "kind": "hr_confound",
                    "statement": "This runner's heart rate tends to read inflated in the heat.",
                    "confidence": "high",
                    "observed_count": 4,
                    "last_seen_days_ago": 6,
                }
            ],
        },
        "calibration": {
            "hr_drift": {
                # observed_drift_pct folded out (one-fact-one-place): == metrics.hr_drift.
                "calibrated": True,
                "expected_drift_pct": 5.5,
                "delta_pct": 3.5,
                "comparison": "above",
                "sample_count": 6,
                "basis": "typical drift ~5.5% across 6 comparable runs",
            },
            "referral": None,
        },
        "preference_profile": {
            "themes": [
                {"theme": "easy_discipline", "tendency": "acts_on", "acted": 5, "total": 6},
            ],
        },
        "narrative": {
            "narrative": "Steady, consistency-driven runner who responds to easy-day discipline.",
            "source_report_count": 3,
            "last_updated_days_ago": 2,
        },
        "salience": {
            "novelty": {"first_of_kind": ["first_interval_session"], "has_history": True},
            "safety_override": {"force_fuller": False, "reasons": []},
        },
        "continuity": {
            "opener_message": "Nice session — give me a sec for the full breakdown.",
            "reply": "felt easy",
        },
        "safety_rules": {
            "never_diagnose": True,
            "pain_severe_threshold": 7,
            "no_invented_facts": True,
        },
    }


def _legacy_sparse_pack() -> dict:
    """A pack with every Optional field set to None and every defaultable collection empty."""
    return {
        "activity": {
            "date": "2026-02-15T10:00:00+00:00",
            "name": None,
            "type": None,
            "distance_m": 0,
            "moving_time_s": 0,
            "avg_hr": None,
            "max_hr": None,
            "avg_cadence": None,
            "elev_gain_m": 0.0,
        },
        "metrics": {
            "headline": None,
            "effort": None,
            "duration_class": None,
            "structure": None,
            "is_hilly": None,
            "is_race": None,
            "effort_score": None,
            "hr_drift": None,
            "pace_variability": None,
            "flags": [],
            "confidence": "low",
            "confidence_reasons": [],
            "time_in_zones": None,
            "zones_calibrated": False,
            "zones_basis": "uncalibrated",
            "efficiency_analysis": None,
            "stops_analysis": None,
            # No session detected: the interval/workout group collapses to one signal in
            # the canonical serialized shape (the three structured fields are omitted).
            "interval_workout": "none detected",
            "risk_level": None,
            "risk_score": None,
            "risk_reasons": [],
            "training_context": None,
            "discount_signals": None,
        },
        "check_in": {
            "rpe": None,
            "pain_score": None,
            "pain_location": None,
            "sleep_quality": None,
            "notes": None,
        },
        "profile": {
            "goal_type": None,
            "experience_level": None,
            "weekly_days_available": None,
            "injury_notes": None,
            "max_hr": None,
            "max_hr_source": None,
            "current_weekly_km": None,
        },
        "recent_training_summary": {
            "last_7d": {
                "activity_count": 0,
                "total_distance_m": 0,
                "total_moving_time_s": 0,
                "total_effort": 0.0,
            },
            "last_28d": {
                "activity_count": 0,
                "total_distance_m": 0,
                "total_moving_time_s": 0,
                "total_effort": 0.0,
            },
            "previous_28d": {
                "activity_count": 0,
                "total_distance_m": 0,
                "total_moving_time_s": 0,
                "total_effort": 0.0,
            },
        },
        "longitudinal": {
            "prior_reports": [],
            "baseline_trend": None,
        },
        "perceived_effort": {
            # rpe / effort_score folded out (one-fact-one-place); canonical post-fold shape.
            "effort_axis": None,
            "divergence": None,
            "divergence_direction": None,
            "hr_confounded": False,
            "recommended_weighting": "hr_only",
            "pain_trend": None,
        },
        "adherence": {
            "prior_report_date": None,
            "outcomes": [],
        },
        "believed_facts": {
            "facts": [],
        },
        "calibration": {
            "hr_drift": {"calibrated": False, "basis": "n/a"},
            "referral": None,
        },
        "preference_profile": {"themes": []},
        "narrative": {
            "narrative": None,
            "source_report_count": None,
            "last_updated_days_ago": None,
        },
        "salience": {
            "novelty": {"first_of_kind": [], "has_history": False},
            "safety_override": {"force_fuller": False, "reasons": []},
        },
        "continuity": {"opener_message": None, "reply": None},
        "safety_rules": {
            "never_diagnose": True,
            "pain_severe_threshold": 7,
            "no_invented_facts": True,
        },
    }


def test_roundtrip_preserves_full_dict_shape():
    pack_dict = _legacy_full_pack()
    pack = CoachContextPack.model_validate(pack_dict)
    assert pack.to_serializable_dict() == pack_dict


def test_roundtrip_preserves_sparse_dict_shape():
    pack_dict = _legacy_sparse_pack()
    pack = CoachContextPack.model_validate(pack_dict)
    assert pack.to_serializable_dict() == pack_dict


def test_no_session_collapses_interval_group_to_one_signal():
    """A run with no detected interval/workout serialises ONE `interval_workout` signal,
    not three null fields — the model reads a single 'no workout' fact."""
    pack_dict = _legacy_full_pack()
    pack_dict["metrics"]["interval_structure"] = None
    pack_dict["metrics"]["interval_kpis"] = None
    pack_dict["metrics"]["workout_match"] = {
        "match_score": None, "detection_confidence": "low",
        "confidence_reasons": ["no_intervals_detected"], "detected_workout": None,
    }
    out = CoachContextPack.model_validate(pack_dict).to_serializable_dict()["metrics"]
    assert out["interval_workout"] == "none detected"
    assert "interval_structure" not in out
    assert "workout_match" not in out
    assert "interval_kpis" not in out


def test_detected_session_keeps_structured_interval_group():
    """When a session IS detected the structured trio rides through unchanged and the
    collapsed `interval_workout` field is absent."""
    pack_dict = _legacy_full_pack()
    pack_dict["metrics"]["interval_structure"] = {
        "source": "recorded_laps",
        "work_segments": [{"duration_s": 90}],
        "summary": {"rep_count": 6},
    }
    pack_dict["metrics"]["workout_match"] = {"detection_confidence": "high", "match_score": 0.9}
    out = CoachContextPack.model_validate(pack_dict).to_serializable_dict()["metrics"]
    assert out["interval_structure"]["summary"]["rep_count"] == 6
    assert out["workout_match"]["detection_confidence"] == "high"
    assert "interval_workout" not in out


def test_old_verbose_no_session_pack_still_validates_and_collapses():
    """Backward compat: a pre-collapse stored pack (the three interval fields present and
    null, no `interval_workout`) still validates under extra='forbid', and re-serialises to
    the new collapsed shape — so the eval harness / chat re-parse of history never breaks."""
    old = _legacy_full_pack()
    old["metrics"]["interval_structure"] = None
    old["metrics"]["workout_match"] = None
    old["metrics"]["interval_kpis"] = None
    old["metrics"].pop("interval_workout", None)
    pack = CoachContextPack.model_validate(old)  # must not raise
    out = pack.to_serializable_dict()["metrics"]
    assert out["interval_workout"] == "none detected"


def test_folds_redundant_scalar_copies_out_of_serialization():
    """One-fact-one-place fold: a pack carrying the redundant scalar COPIES
    (perceived_effort.rpe/effort_score, calibration.hr_drift.observed_drift_pct,
    metrics.discount_signals.hr_drift_pct) re-serialises with those copies DROPPED,
    while their sole homes (check_in.rpe, metrics.effort_score, metrics.hr_drift)
    remain. Doubles as the backward-compat check: a stored pre-fold pack carrying the
    copies still validates under extra='forbid' and round-trips to the folded shape, so
    the eval harness / chat re-parse of history never breaks."""
    old = _legacy_full_pack()
    old["perceived_effort"]["rpe"] = 6
    old["perceived_effort"]["effort_score"] = 3.0
    old["calibration"]["hr_drift"]["observed_drift_pct"] = 9.0
    old["metrics"]["discount_signals"] = {"likely_inflated_by": ["heat"], "hr_drift_pct": 4.2}

    out = CoachContextPack.model_validate(old).to_serializable_dict()  # must not raise

    assert "rpe" not in out["perceived_effort"]
    assert "effort_score" not in out["perceived_effort"]
    assert "observed_drift_pct" not in out["calibration"]["hr_drift"]
    assert "hr_drift_pct" not in out["metrics"]["discount_signals"]
    # The sole homes are untouched.
    assert out["check_in"]["rpe"] == 6
    assert out["metrics"]["effort_score"] == 3.0
    assert out["metrics"]["hr_drift"] == 4.2
    # The sections keep their value-add.
    assert out["perceived_effort"]["divergence"] == -1
    assert out["calibration"]["hr_drift"]["delta_pct"] == 3.5
    assert out["metrics"]["discount_signals"]["likely_inflated_by"] == ["heat"]


def test_fingerprint_matches_legacy_hash_full():
    pack_dict = _legacy_full_pack()
    expected = hash_context_pack(pack_dict)
    pack = CoachContextPack.model_validate(pack_dict)
    assert pack.fingerprint() == expected


def test_fingerprint_matches_legacy_hash_sparse():
    pack_dict = _legacy_sparse_pack()
    expected = hash_context_pack(pack_dict)
    pack = CoachContextPack.model_validate(pack_dict)
    assert pack.fingerprint() == expected


def test_real_fixture_pack_roundtrips_through_typed_schema(db):
    """Build a pack from a real fixture activity and confirm the typed schema preserves it."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()

    activity = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=12345,
        name="Tempo Thursday",
        type="Run",
        user_intent="tempo",
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
        effort="tempo",
        duration_class="standard",
        structure="continuous",
        is_hilly=False,
        is_race=False,
        effort_score=5.5,
        pace_variability=4.2,
        hr_drift=3.1,
        time_in_zones={"Z2": 600, "Z3": 1800, "Z4": 1200},
        flags=["high_intensity"],
        confidence="high",
        confidence_reasons=["full_coverage"],
        training_context={"intensity_distribution_7d": {"easy": 3, "moderate": 1, "hard": 0}},
    ))
    db.add(UserProfile(
        user_id=user_id,
        goal_type="half",
        experience_level="intermediate",
        weekly_days_available=4,
        current_weekly_km=35,
        max_hr=185,
        max_hr_source="user_entered",
    ))
    db.flush()
    # Refresh so .metrics / .check_in relationships populate.
    db.refresh(activity)

    pack = build_context_pack(db, activity)
    pack_dict = pack.to_serializable_dict()

    # Round-trip through model_validate preserves the dict shape.
    assert CoachContextPack.model_validate(pack_dict).to_serializable_dict() == pack_dict
    # The typed fingerprint matches the legacy dict-based hash byte-for-byte.
    assert pack.fingerprint() == hash_context_pack(pack_dict)


# --- The #493 gated-pack-section registry --------------------------------------


def test_pack_section_registry_covers_every_droppable_field():
    """Every Optional-and-dropped section is a PACK_SECTIONS descriptor, and every
    descriptor names a real declared field. This is the structural invariant the
    registry replaces seven hand-copied drop branches with."""
    from app.schemas.coach_context import CoachContextPack, PACK_SECTIONS

    model_fields = CoachContextPack.model_fields
    for section in PACK_SECTIONS:
        assert section.field in model_fields, section.field
    # The seven prompt-gated sections named by #493 are all present.
    fields = {s.field for s in PACK_SECTIONS}
    assert {
        "block",
        "corpus",
        "stance",
        "training_load",
        "training_volume",
        "stream_view",
        "recent_training",
    }.issubset(fields)


def test_pack_section_descriptor_naming_unknown_field_fails_at_import_check():
    """The import-time guard turns a descriptor that names a non-existent schema
    field into a loud RuntimeError instead of a silent byte-stability break."""
    import pytest

    import app.schemas.coach_context as m
    from app.schemas.coach_context import PackSection

    original = m.PACK_SECTIONS
    try:
        m.PACK_SECTIONS = original + (PackSection("this_field_does_not_exist"),)
        with pytest.raises(RuntimeError, match="not declared on CoachContextPack"):
            m._assert_descriptors_match_fields()
    finally:
        m.PACK_SECTIONS = original
    # Sanity: the real registry still passes once restored.
    m._assert_descriptors_match_fields()


def test_pack_section_duplicate_descriptor_fails_at_import_check():
    """A duplicate descriptor for the same field is also caught at the import guard."""
    import pytest

    import app.schemas.coach_context as m
    from app.schemas.coach_context import PackSection

    original = m.PACK_SECTIONS
    try:
        m.PACK_SECTIONS = original + (PackSection("block"),)
        with pytest.raises(RuntimeError, match="duplicate descriptor"):
            m._assert_descriptors_match_fields()
    finally:
        m.PACK_SECTIONS = original
    m._assert_descriptors_match_fields()
