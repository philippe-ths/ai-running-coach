"""The surviving prior-report-driven coaching kill switches
(COACH_PRIOR_REPORTS_ENABLED / COACH_ADHERENCE_ENABLED).

Both default OFF in prod: the M4 prior-report digest and the M7 adherence section
carried a stale rest-day theme forward run after run (each report read the
previous report's next_steps), so they are disabled by default.

These tests pin the DISABLED-by-default behaviour: even when prior-report rows
exist, the pack carries the empty (new-runner) form for longitudinal.prior_reports
and adherence, and the post-report learning loop enqueues nothing under the live
(non-memory) prompt. They deliberately do NOT request `enable_durable_memory`, so
they run against the prod default.

(The M8 belief loop + A2c narrative halves of durable memory were retired in M4,
ADR 0025, replaced by the runner memory profile; their switches and stores are
gone. The memory enqueue gating is pinned in test_memory_enqueue_gating.py.)
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.models import Activity, DerivedMetric
from app.models.coach_report import CoachReport
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.service import _fire_learning_loop


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, uid):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
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


def test_learning_loop_enqueues_nothing_under_live_prompt(db):
    """No memory update enqueue under the default flags + the live (non-memory)
    prompt: the only post-report enqueue gates on a memory-aware prompt (v13)."""
    uid = _user(db)
    act = _activity(db, uid)
    pack = build_context_pack(db, act)

    with patch("app.services.coach.service.enqueue_memory_update") as em:
        _fire_learning_loop(db, act, pack, "coach_message_v12")

    em.assert_not_called()


def _activity_at(db, uid, dt, *, effort="easy", user_intent=None):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=dt,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={}, user_intent=user_intent,
    )
    db.add(a); db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort=effort, duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
    ))
    db.flush()
    return a


def _prior_report(db, activity, next_action):
    db.add(CoachReport(
        id=uuid.uuid4(), activity_id=activity.id,
        prompt_id="coach_report_v3", schema_version="1.2",
        report={
            "headline": "Prior run", "thesis": "Body",
            "lead_argument": {"text": "Lead", "evidence": []},
            "key_takeaways": [{"text": "Takeaway"}],
            "next_steps": [{"action": next_action, "details": "", "why": ""}],
            "risks": [], "questions": [],
        },
        meta={}, context_pack={}, is_fallback=False,
    ))
    db.flush()


def test_pack_carries_no_prior_reports_when_disabled(db):
    """longitudinal.prior_reports stays empty even when a prior report exists."""
    uid = _user(db)
    a1 = _activity_at(db, uid, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    _prior_report(db, a1, "Keep your easy runs easy")
    current = _activity_at(db, uid, datetime(2026, 3, 4, 8, 0, tzinfo=timezone.utc))

    assert build_context_pack(db, current).longitudinal.prior_reports == []


def test_pack_carries_no_adherence_when_disabled(db):
    """The adherence section stays empty even with a prior report + a comparable
    subsequent run that would otherwise be labelled."""
    uid = _user(db)
    a1 = _activity_at(db, uid, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
                      user_intent="easy")
    _prior_report(db, a1, "Keep your easy runs easy")
    current = _activity_at(db, uid, datetime(2026, 3, 4, 8, 0, tzinfo=timezone.utc),
                           user_intent="easy")

    pack = build_context_pack(db, current)
    assert pack.adherence.outcomes == []
    assert pack.adherence.prior_report_date is None
