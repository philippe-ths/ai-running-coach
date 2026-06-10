"""M10 preference profile in the coach context pack (DB-integration) + prompt
versioning. The profile is derived from the user's accumulated M8
adherence_pattern beliefs, reusing the belief retrieval (active, non-decayed,
quality-cleared)."""

import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.models import Activity, DerivedMetric
from app.models.coaching_context import CoachingContext
from app.models.user import User
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V6


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


def _belief(db, uid, theme, acted, total):
    db.add(CoachingContext(
        id=uuid.uuid4(), user_id=uid, kind="adherence_pattern", key=theme,
        value={"acted": acted, "total": total, "statement": "x"},
        confidence="high", observation_count=total,
        first_observed_at=datetime.now(timezone.utc),
        last_reinforced_at=datetime.now(timezone.utc),
    ))
    db.flush()


def test_pack_carries_preference_profile_from_beliefs(db):
    uid = _user(db)
    _belief(db, uid, "easy_discipline", 5, 6)   # acts_on
    _belief(db, uid, "add_quality", 0, 4)        # ignores
    act = _activity(db, uid)

    prof = build_context_pack(db, act).preference_profile
    by_theme = {t.theme: t.tendency for t in prof.themes}
    assert by_theme["easy_discipline"] == "acts_on"
    assert by_theme["add_quality"] == "ignores"


def test_pack_preference_empty_without_history(db):
    uid = _user(db)
    act = _activity(db, uid)
    assert build_context_pack(db, act).preference_profile.themes == []


def test_pack_preference_ignores_non_adherence_beliefs(db):
    """A confound belief is not an advice-preference signal."""
    uid = _user(db)
    db.add(CoachingContext(
        id=uuid.uuid4(), user_id=uid, kind="hr_confound", key="heat",
        value={"statement": "x"}, confidence="high", observation_count=4,
        first_observed_at=datetime.now(timezone.utc),
        last_reinforced_at=datetime.now(timezone.utc),
    ))
    db.flush()
    act = _activity(db, uid)
    assert build_context_pack(db, act).preference_profile.themes == []


class TestPromptVersioning:
    def test_v7_is_active_default(self):
        # Active default has since advanced (#168 -> v8); v7 stays byte-stable.
        assert settings.COACH_PROMPT_ID == "coach_report_v10"

    def test_v7_adds_preference_rule(self):
        v7 = PROMPT_VERSIONS["coach_report_v7"]
        assert "PREFERENCE" in v7
        assert "21." in v7
        assert "acts on" in v7.lower()

    def test_v6_stays_byte_stable(self):
        assert PROMPT_VERSIONS["coach_report_v6"] == SYSTEM_PROMPT_V6
        assert "21. PREFERENCE" not in PROMPT_VERSIONS["coach_report_v6"]

    def test_v7_extends_v6(self):
        v6 = PROMPT_VERSIONS["coach_report_v6"]
        v7 = PROMPT_VERSIONS["coach_report_v7"]
        assert v7 != v6
        assert v7.startswith(v6)
