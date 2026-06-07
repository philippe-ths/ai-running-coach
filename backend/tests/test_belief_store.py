"""M8 belief store — DB-integration tests.

The thin DB layer over beliefs.py: write-back creates/reinforces rows
(non-redundant upsert), retrieval filters by quality + decay and tags with
confidence/recency, everything is user-scoped, and the loop closes through
build_context_pack (a report's beliefs reach the next pack's believed_facts).
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.config import settings
from app.models import Activity, DerivedMetric
from app.models.coaching_context import CoachingContext
from app.models.user import User
from app.services.coach.belief_store import (
    build_believed_facts,
    retrieve_beliefs,
    write_back_beliefs,
)
from app.services.coach.context import build_context_pack
from app.services.coach.prompts import PROMPT_VERSIONS, SYSTEM_PROMPT_V4

_HEAT_DISCOUNT = {
    "hr_drift_pct": 9.0,
    "likely_inflated_by": ["heat"],
    "temperature_c": 29.0,
    "confidence": "high",
    "interpretation": "HR drift likely inflated by heat.",
}


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, discount_signals=None):
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=10.0,
        average_speed_mps=2.78, raw_summary={},
    )
    db.add(a)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=a.id, effort="easy", duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=3.0,
        confidence="high", confidence_reasons=[], flags=[],
        discount_signals=discount_signals,
    ))
    db.flush()
    return a


def _pack(discount_signals=None, outcomes=None):
    return SimpleNamespace(
        metrics=SimpleNamespace(discount_signals=discount_signals),
        adherence=SimpleNamespace(outcomes=outcomes or []),
    )


def _outcome(theme, label, overridden=False):
    return {"theme": theme, "label": label, "overridden": overridden}


def test_write_back_creates_heat_belief(db):
    uid = _user(db)
    act = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, act, _pack(discount_signals=_HEAT_DISCOUNT))
    rows = db.query(CoachingContext).filter(CoachingContext.user_id == uid).all()
    assert len(rows) == 1
    assert rows[0].kind == "hr_confound"
    assert rows[0].key == "heat"
    assert rows[0].observation_count == 1
    assert rows[0].confidence == "low"
    assert act.beliefs_written_at is not None  # sentinel set


def test_same_activity_not_double_counted(db):
    """The at-most-once sentinel makes a re-read / force-regen safe: the same
    physical run never contributes its observation twice."""
    uid = _user(db)
    act = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, act, _pack(discount_signals=_HEAT_DISCOUNT))
    write_back_beliefs(db, act, _pack(discount_signals=_HEAT_DISCOUNT))  # re-run
    row = db.query(CoachingContext).filter(CoachingContext.user_id == uid).one()
    assert row.observation_count == 1  # not double-counted


def test_reinforces_across_distinct_activities_not_duplicates(db):
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    rows = db.query(CoachingContext).filter(CoachingContext.user_id == uid).all()
    assert len(rows) == 1  # non-redundant: reinforced in place, not duplicated
    assert rows[0].observation_count == 2
    assert rows[0].confidence == "medium"


def test_write_back_accumulates_adherence_counts(db):
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid),
                       _pack(outcomes=[_outcome("easy_discipline", "acted_on")]))
    write_back_beliefs(db, _activity(db, uid),
                       _pack(outcomes=[_outcome("easy_discipline", "ignored")]))
    row = db.query(CoachingContext).filter(CoachingContext.kind == "adherence_pattern").one()
    assert row.value["acted"] == 1
    assert row.value["total"] == 2


def test_write_back_no_signals_writes_nothing(db):
    uid = _user(db)
    act = _activity(db, uid)
    write_back_beliefs(db, act, _pack())
    assert db.query(CoachingContext).filter(CoachingContext.user_id == uid).count() == 0


def test_single_observation_not_retrievable(db):
    uid = _user(db)
    act = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, act, _pack(discount_signals=_HEAT_DISCOUNT))
    assert retrieve_beliefs(db, uid) == []  # obs < 2


def test_reinforced_belief_is_retrieved_with_tags_on_matching_run(db):
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    hot = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, hot, _pack(discount_signals=_HEAT_DISCOUNT))
    facts = build_believed_facts(db, hot).facts  # hot run -> heat belief applies
    assert len(facts) == 1
    assert facts[0].kind == "hr_confound"
    assert facts[0].confidence == "medium"
    assert facts[0].observed_count == 2
    assert facts[0].last_seen_days_ago >= 0
    assert "heat" in facts[0].statement.lower()


def test_confound_not_surfaced_on_cool_run(db):
    """SAFETY: a heat belief must not be applied to discount a cool-day run."""
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    cool = _activity(db, uid, discount_signals=None)  # no heat on this run
    assert build_believed_facts(db, cool).facts == []


def test_decayed_belief_not_retrieved(db):
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    hot = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, hot, _pack(discount_signals=_HEAT_DISCOUNT))
    row = db.query(CoachingContext).filter(CoachingContext.user_id == uid).one()
    row.last_reinforced_at = datetime.now(timezone.utc) - timedelta(days=200)  # past TTL
    db.commit()
    assert build_believed_facts(db, hot).facts == []


def test_beliefs_are_user_scoped(db):
    uid_a = _user(db)
    write_back_beliefs(db, _activity(db, uid_a, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    write_back_beliefs(db, _activity(db, uid_a, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    uid_b = _user(db)
    act_b = _activity(db, uid_b, discount_signals=_HEAT_DISCOUNT)
    assert build_believed_facts(db, act_b).facts == []  # B sees none of A's beliefs


def test_loop_closes_through_context_pack(db):
    """A report's beliefs reach the next pack's believed_facts section."""
    uid = _user(db)
    write_back_beliefs(db, _activity(db, uid, discount_signals=_HEAT_DISCOUNT),
                       _pack(discount_signals=_HEAT_DISCOUNT))
    hot = _activity(db, uid, discount_signals=_HEAT_DISCOUNT)
    write_back_beliefs(db, hot, _pack(discount_signals=_HEAT_DISCOUNT))
    pack = build_context_pack(db, hot)
    assert len(pack.believed_facts.facts) == 1
    assert pack.believed_facts.facts[0].kind == "hr_confound"


class TestPromptVersioning:
    def test_v5_is_active_default(self):
        assert settings.COACH_PROMPT_ID == "coach_report_v6"

    def test_v5_adds_believed_facts_rule(self):
        v5 = PROMPT_VERSIONS["coach_report_v5"]
        assert "BELIEVED FACTS" in v5
        assert "19." in v5
        assert "today's data wins" in v5.lower()

    def test_v4_stays_byte_stable(self):
        assert PROMPT_VERSIONS["coach_report_v4"] == SYSTEM_PROMPT_V4
        assert "BELIEVED FACTS" not in PROMPT_VERSIONS["coach_report_v4"]

    def test_v5_extends_v4(self):
        v4 = PROMPT_VERSIONS["coach_report_v4"]
        v5 = PROMPT_VERSIONS["coach_report_v5"]
        assert v5 != v4
        assert v5.startswith(v4)
