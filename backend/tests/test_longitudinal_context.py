"""Tests for M4 longitudinal contrast in the coach context pack.

Two sub-paths (brief § M4):
- reports-only: the pack carries a digest of the last 1-2 prior reports'
  lead_argument + next_steps, and NOT the full prior report body (token bound).
- baseline-delta: when a RunnerBaseline trend exists for this activity's context
  bucket, that trend reaches the pack.

Plus the prompt-discipline check: the active prompt carries an explicit
"advance the narrative, do not restate" instruction.
"""

import uuid
from datetime import datetime, timezone, timedelta

from app.models import Activity, DerivedMetric, RunnerBaseline
from app.models.coach_report import CoachReport
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import build_system_prompt, PROMPT_VERSIONS


def _user(db):
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"test_{user_id}@example.com"))
    db.flush()
    return user_id


def _activity(db, user_id, start_date, **overrides):
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=overrides.get("name", "Run"),
        type="Run",
        start_date=start_date,
        distance_m=overrides.get("distance_m", 10000),
        moving_time_s=overrides.get("moving_time_s", 3600),
        elapsed_time_s=overrides.get("elapsed_time_s", 3700),
        avg_hr=overrides.get("avg_hr", 150.0),
        max_hr=overrides.get("max_hr", 175.0),
        avg_cadence=overrides.get("avg_cadence", 170.0),
        elev_gain_m=overrides.get("elev_gain_m", 50.0),
        average_speed_mps=overrides.get("average_speed_mps", 2.78),
        raw_summary=overrides.get("raw_summary", {}),
    )
    db.add(a)
    db.flush()
    return a


def _metrics(db, activity, **overrides):
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort=overrides.get("effort", "easy"),
        duration_class=overrides.get("duration_class", "standard"),
        structure=overrides.get("structure", "continuous"),
        is_hilly=overrides.get("is_hilly", False),
        is_race=overrides.get("is_race", False),
        effort_score=overrides.get("effort_score", 3.0),
        hr_drift=overrides.get("hr_drift"),
        confidence=overrides.get("confidence", "high"),
        confidence_reasons=overrides.get("confidence_reasons", []),
        flags=overrides.get("flags", []),
    )
    db.add(dm)
    db.flush()
    return dm


def _report(db, activity, *, lead_text, next_action, takeaway_marker,
            headline="Solid run", is_fallback=False,
            prompt_id="coach_report_v2", created_at=None):
    """Store a CoachReport with a distinctive lead_argument, next_step and a
    takeaway_marker that lives ONLY in the (non-digest) body."""
    content = {
        "headline": headline,
        "thesis": f"Thesis body {takeaway_marker}",
        "lead_argument": {"text": lead_text, "evidence": [{"field": "metrics.hr_drift", "value": 4.0}]},
        "key_takeaways": [{"text": f"Takeaway {takeaway_marker}"}],
        "next_steps": [{"action": next_action, "details": "20 min easy", "why": f"because {takeaway_marker}"}],
        "risks": [],
        "questions": [],
    }
    kwargs = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
    r = CoachReport(
        id=uuid.uuid4(),
        activity_id=activity.id,
        prompt_id=prompt_id,
        schema_version="1.2",
        report=content,
        meta={},
        context_pack={},
        is_fallback=is_fallback,
        **kwargs,
    )
    db.add(r)
    db.flush()
    return r


# --- reports-only sub-path --------------------------------------------------

def test_pack_carries_prior_report_digest(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, lead_text="Aerobic base is holding", next_action="Add a tempo segment",
            takeaway_marker="MARKER_ONE")

    current = _activity(db, user_id, base + timedelta(days=3))
    _metrics(db, current)

    pack = build_context_pack(db, current)

    assert len(pack.longitudinal.prior_reports) == 1
    digest = pack.longitudinal.prior_reports[0]
    assert digest.lead_argument == "Aerobic base is holding"
    assert any("Add a tempo segment" in s for s in digest.next_steps)
    assert digest.headline == "Solid run"


def test_pack_digest_excludes_full_report_body(db):
    """Token bound: the prior report's key_takeaways / thesis / why must NOT
    leak into the pack; only the lead_argument + next_step digest does."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    a1 = _activity(db, user_id, base)
    _metrics(db, a1)
    _report(db, a1, lead_text="Lead survives", next_action="Run easy",
            takeaway_marker="BODY_ONLY_MARKER")

    current = _activity(db, user_id, base + timedelta(days=2))
    _metrics(db, current)

    pack = build_context_pack(db, current)
    # Assert on the EXACT surface service.py sends to the LLM (to_serializable_dict
    # -> json.dumps), not model_dump_json, so the token bound is checked where it
    # actually matters.
    import json
    serialized = json.dumps(pack.to_serializable_dict(), default=str)

    assert "Lead survives" in serialized          # digest is present
    assert "BODY_ONLY_MARKER" not in serialized    # full body is not


def test_pack_caps_prior_reports_at_two_most_recent(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    for i in range(3):
        a = _activity(db, user_id, base + timedelta(days=i))
        _metrics(db, a)
        _report(db, a, lead_text=f"lead {i}", next_action=f"action {i}",
                takeaway_marker=f"M{i}")

    current = _activity(db, user_id, base + timedelta(days=10))
    _metrics(db, current)

    pack = build_context_pack(db, current)

    leads = [d.lead_argument for d in pack.longitudinal.prior_reports]
    assert len(leads) == 2
    assert leads == ["lead 2", "lead 1"]  # most recent first, oldest dropped


def test_pack_picks_latest_version_when_activity_has_several_reports(db):
    """An activity can hold several versioned reports (different prompt_id). The
    digest must carry the newest one (by created_at), not an older version."""
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    prior = _activity(db, user_id, base)
    _metrics(db, prior)
    _report(db, prior, lead_text="old v1 lead", next_action="x", takeaway_marker="OLD",
            prompt_id="coach_report_v1",
            created_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
    _report(db, prior, lead_text="new v2 lead", next_action="y", takeaway_marker="NEW",
            prompt_id="coach_report_v2",
            created_at=datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc))

    current = _activity(db, user_id, base + timedelta(days=5))
    _metrics(db, current)

    pack = build_context_pack(db, current)

    assert len(pack.longitudinal.prior_reports) == 1
    assert pack.longitudinal.prior_reports[0].lead_argument == "new v2 lead"


def test_pack_excludes_fallback_and_future_reports(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    fallback = _activity(db, user_id, base)
    _metrics(db, fallback)
    _report(db, fallback, lead_text="fallback lead", next_action="x",
            takeaway_marker="FB", is_fallback=True)

    current = _activity(db, user_id, base + timedelta(days=2))
    _metrics(db, current)

    future = _activity(db, user_id, base + timedelta(days=5))
    _metrics(db, future)
    _report(db, future, lead_text="future lead", next_action="y", takeaway_marker="FUT")

    pack = build_context_pack(db, current)

    assert pack.longitudinal.prior_reports == []


def test_pack_has_empty_longitudinal_for_first_ever_activity(db):
    user_id = _user(db)
    current = _activity(db, user_id, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    _metrics(db, current)

    pack = build_context_pack(db, current)

    assert pack.longitudinal.prior_reports == []
    assert pack.longitudinal.baseline_trend is None


# --- baseline-delta sub-path ------------------------------------------------

def test_pack_carries_matching_baseline_trend(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    # This activity: easy effort, flat, cool (avg temp 15) -> bucket "easy|flat|cool"
    current = _activity(db, user_id, base, raw_summary={"average_temp": 15})
    _metrics(db, current, effort="easy", is_hilly=False)

    db.add(RunnerBaseline(
        id=uuid.uuid4(),
        user_id=user_id,
        sample_count=6,
        bucketed_trends={
            "easy|flat|cool": {
                "sample_count": 5,
                "abstained": False,
                "efficiency_factor": {"direction": "improving", "magnitude_pct": 4.1, "slope": 0.01, "n": 5},
                "hr_drift": {"direction": "declining", "magnitude_pct": -3.0, "slope": -0.2, "n": 5},
            },
            "hard|flat|cool": {"sample_count": 2, "abstained": True},
        },
    ))
    db.flush()

    pack = build_context_pack(db, current)

    trend = pack.longitudinal.baseline_trend
    assert trend is not None
    assert trend.bucket == "easy|flat|cool"
    assert trend.efficiency_factor["direction"] == "improving"
    assert trend.hr_drift["direction"] == "declining"


def test_pack_omits_abstained_baseline_bucket(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    current = _activity(db, user_id, base, raw_summary={"average_temp": 15})
    _metrics(db, current, effort="easy", is_hilly=False)

    db.add(RunnerBaseline(
        id=uuid.uuid4(),
        user_id=user_id,
        sample_count=3,
        bucketed_trends={"easy|flat|cool": {"sample_count": 2, "abstained": True}},
    ))
    db.flush()

    pack = build_context_pack(db, current)
    assert pack.longitudinal.baseline_trend is None


def test_pack_omits_baseline_trend_when_no_matching_bucket(db):
    user_id = _user(db)
    base = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)

    current = _activity(db, user_id, base, raw_summary={"average_temp": 30})  # hot
    _metrics(db, current, effort="easy", is_hilly=False)

    db.add(RunnerBaseline(
        id=uuid.uuid4(),
        user_id=user_id,
        sample_count=6,
        bucketed_trends={
            "easy|flat|cool": {"sample_count": 5, "abstained": False,
                               "efficiency_factor": None, "hr_drift": None},
        },
    ))
    db.flush()

    pack = build_context_pack(db, current)
    assert pack.longitudinal.baseline_trend is None


# --- prompt discipline ------------------------------------------------------

def test_active_prompt_has_anti_restate_instruction():
    from app.core.config import settings
    prompt = build_system_prompt(settings.COACH_PROMPT_ID)
    lowered = prompt.lower()
    assert "advance the narrative" in lowered
    assert "longitudinal" in lowered


def test_v1_prompt_unchanged_has_no_longitudinal_rule():
    """The retained v1 prompt must stay byte-stable so cached v1 reports remain
    reproducible (the M0 versioning seam)."""
    v1 = PROMPT_VERSIONS["coach_report_v1"]
    assert "advance the narrative" not in v1.lower()
    assert "16. LONGITUDINAL" not in v1
