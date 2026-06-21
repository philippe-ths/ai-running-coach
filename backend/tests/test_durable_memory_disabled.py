"""The prior-report-driven coaching kill switches (COACH_BELIEFS_ENABLED /
COACH_NARRATIVE_ENABLED / COACH_PRIOR_REPORTS_ENABLED / COACH_ADHERENCE_ENABLED).

All four default OFF in prod. The M8 belief loop was found to misclassify "take a
rest day" advice as easy-discipline and reinforce a poisoned "ignores easy
guidance" belief, which the A2c narrative then replayed; the M4 prior-report
digest and the M7 adherence section likewise carried that stale rest-day theme
forward run after run (each report read the previous report's next_steps).

These tests pin the DISABLED-by-default behaviour: even when belief, narrative,
and prior-report rows exist in the database, the context pack carries the empty
(new-runner) form for the believed_facts, preference_profile, narrative,
longitudinal.prior_reports, and adherence sections, and the post-report learning
loop writes nothing. They deliberately do NOT request the `enable_durable_memory`
fixture, so they run against the prod default.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from app.models import Activity, DerivedMetric
from app.models.coach_narrative import CoachNarrative
from app.models.coach_report import CoachReport
from app.models.coaching_context import CoachingContext
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


def _seed_belief_and_narrative(db, uid):
    """A poisoned belief and a populated narrative, exactly the prod state."""
    db.add(CoachingContext(
        id=uuid.uuid4(), user_id=uid, kind="adherence_pattern", key="easy_discipline",
        value={"acted": 5, "total": 8, "statement": "mixed response to easy guidance"},
        confidence="high", observation_count=23,
        first_observed_at=datetime.now(timezone.utc),
        last_reinforced_at=datetime.now(timezone.utc),
    ))
    db.add(CoachNarrative(
        id=uuid.uuid4(), user_id=uid,
        narrative="This runner reliably ignores the rest days I prescribe.",
    ))
    db.flush()


def test_pack_carries_no_beliefs_when_disabled(db):
    """believed_facts and preference_profile stay empty even with a stored belief."""
    uid = _user(db)
    _seed_belief_and_narrative(db, uid)
    act = _activity(db, uid)

    pack = build_context_pack(db, act)
    assert pack.believed_facts.facts == []
    assert pack.preference_profile.themes == []


def test_pack_carries_no_narrative_when_disabled(db):
    """The narrative section is empty even with a stored narrative row."""
    uid = _user(db)
    _seed_belief_and_narrative(db, uid)
    act = _activity(db, uid)

    assert build_context_pack(db, act).narrative.narrative is None


def test_learning_loop_writes_nothing_when_disabled(db):
    """No belief write-back and no consolidation enqueue under the default flags."""
    uid = _user(db)
    act = _activity(db, uid)
    pack = build_context_pack(db, act)

    with patch("app.services.coach.service.write_back_beliefs") as wb, \
         patch("app.services.coach.service.enqueue_consolidation") as ec:
        _fire_learning_loop(db, act, pack)

    wb.assert_not_called()
    ec.assert_not_called()


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
