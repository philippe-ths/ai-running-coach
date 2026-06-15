"""Behavioural cross-school floor-invariance test (ADR 0014, integration-tagged).

The BEHAVIOURAL confirmation of the cross-school gate: it runs ONE fixed flagged
activity through real LLM generations under coach_message_v4 across all three
schools (aerobic-base, polarized, enjoyment-and-consistency) and asserts the safety
floor held at every school — the deterministic policy gate (validate_message_policy)
finds no violation and no report degraded to a fallback. The school changes the
coaching emphasis; it never drops the floor.

It is the soft, slow, paid companion to the hard deterministic gate in
test_corpus_invariance.py (the pack's facts/floor and the policy outcome are
byte-identical across schools), which is what runs in CI. This file makes real
Anthropic calls, so it is marked `integration` (excluded from `make backend-test`)
and skips entirely without ANTHROPIC_API_KEY.

NOTE: requires a live ANTHROPIC_API_KEY. Run it manually before trusting the
behavioural claim:
`python -m pytest -m integration tests/test_corpus_invariance_integration.py`.
"""

import os
from datetime import datetime
from uuid import uuid4

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, StravaAccount, User, UserProfile
from app.services.coach import context as context_mod
from app.services.coach.context import build_context_pack
from app.services.coach.service import get_active_report_row, get_or_generate_coach_report
from app.services.coach.validator import validate_message_policy

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="cross-school behavioural invariance needs a live ANTHROPIC_API_KEY",
    ),
]

_SCHOOLS = ("aerobic-base", "polarized", "enjoyment-and-consistency")


def _seed_flagged_activity(db) -> Activity:
    user = User(email=f"corpus-int-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(UserProfile(
        user_id=user.id, goal_type="general", experience_level="intermediate",
        weekly_days_available=4, max_hr=190,
    ))
    db.add(StravaAccount(
        user_id=user.id, strava_athlete_id=abs(hash(str(user.id))) % 10**9,
        access_token="t", refresh_token="r", expires_at=9999999999, scope="read",
    ))
    activity = Activity(
        user_id=user.id, strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=datetime(2026, 5, 27, 10, 0, 0), type="Run", name="Flagged run",
        distance_m=12000, moving_time_s=4000, elapsed_time_s=4000, elev_gain_m=30.0,
        avg_hr=165, raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.add(DerivedMetric(
        activity_id=activity.id, effort="moderate", structure="continuous",
        duration_class="long", effort_score=180.0,
        flags=["illness_or_extreme_fatigue", "high_drift"],
        hr_drift=9.5, confidence="high", confidence_reasons=[],
    ))
    db.commit()
    db.refresh(activity)
    return activity


async def test_floor_holds_across_schools(db, monkeypatch):
    monkeypatch.setattr(settings, "COACH_PROMPT_ID", "coach_message_v4")
    activity = _seed_flagged_activity(db)

    for school in _SCHOOLS:
        # In P1.2 the school is the wired default; vary it the way P1.3's selector
        # eventually will, so the generation's pack carries this school.
        monkeypatch.setattr(context_mod, "DEFAULT_SCHOOL_ID", school)

        # Under coach_message_v4, get_or_generate delegates to the fuller turn — a
        # complete report. force=True so each school generates fresh.
        report = await get_or_generate_coach_report(db, str(activity.id), force=True)
        assert report is not None, f"no report generated for school {school}"
        row = get_active_report_row(db, str(activity.id))
        assert row is not None and not row.is_fallback, f"school {school} degraded to a fallback"

        # The floor: the deterministic policy gate finds no violation regardless of
        # the steering school — the corpus reweights emphasis, never the floor.
        pack = build_context_pack(db, activity, prompt_id="coach_message_v4")
        from app.schemas.coach import CoachMessageReport
        content = CoachMessageReport.model_validate(report.report)
        violations = validate_message_policy(content, pack)
        assert violations == [], f"school {school} produced policy violations: {[v.rule for v in violations]}"
