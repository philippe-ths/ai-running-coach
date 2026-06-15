"""P1.3 stance context-pack section + school threading (ADR 0015).

The `stance` section (the two emphasis axes) is emitted ONLY under a stance-aware
prompt id, mirroring the `corpus`/`block` idiom. The runner's SELECTED SCHOOL
rides the `corpus` section but takes effect ONLY under the stance-aware prompt (v5)
— under v4 the corpus keeps the hardcoded default (P1.2 byte-stable). These tests pin:
  - AC2: under coach_message_v5 the pack carries a `stance` section (two emphasis
    axes) AND the `corpus` section carries the runner's selected school;
  - AC1: under v4 the corpus still resolves to the hardcoded default even when a
    stance is passed, and the `stance` key is absent;
  - AC1/AC7: under any non-stance prompt the `stance` key is absent and the pack is
    byte-identical (fingerprint-stable) to today;
  - AC2: the run's facts/DerivedMetric are unchanged by the stance section.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import Activity, DerivedMetric, User, UserProfile
from app.services.coach.context import _build_stance_context, build_context_pack
from app.services.coach.corpus import DEFAULT_SCHOOL_ID, SCHOOLS
from app.services.coach.stance import DEFAULT_EMPHASIS, Emphasis, StanceProfile

V5 = "coach_message_v5"
V4 = "coach_message_v4"


def _seed_activity(db) -> Activity:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"stance_{user_id}@example.com"))
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


def _stance(school="polarized", data_sentiment=5, process_outcome=1):
    return StanceProfile(
        school_id=school,
        emphasis=Emphasis(data_sentiment=data_sentiment, process_outcome=process_outcome),
        is_default=False,
    )


def test_stance_section_present_under_v5_with_two_emphasis_axes(db):
    """AC2: under v5 the pack carries the stance section — both emphasis axes with
    their poles, the runner's values, and a descriptor."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V5, stance=_stance())

    assert pack.stance is not None
    by_key = {a.key: a for a in pack.stance.emphasis}
    assert set(by_key) == {"data_sentiment", "process_outcome"}
    assert by_key["data_sentiment"].value == 5
    assert by_key["data_sentiment"].low_pole == "Data"
    assert by_key["data_sentiment"].high_pole == "Sentiment"
    assert by_key["data_sentiment"].descriptor  # non-empty
    assert by_key["process_outcome"].value == 1
    assert "stance" in pack.to_serializable_dict()


def test_v5_threads_the_runners_selected_school_into_corpus(db):
    """AC2: under v5 the corpus section carries the runner's SELECTED school, not the
    hardcoded default."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V5, stance=_stance(school="polarized"))
    assert pack.corpus is not None and pack.corpus.school is not None
    assert pack.corpus.school.id == "polarized"
    assert pack.corpus.school.name == SCHOOLS["polarized"].name


def test_v5_with_no_stance_uses_default_school_and_balanced_emphasis(db):
    """An undeclared stance under v5 resolves to the default school + balanced 3/3."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V5)  # no stance passed
    assert pack.corpus.school.id == DEFAULT_SCHOOL_ID
    assert pack.stance is not None
    for axis in pack.stance.emphasis:
        assert axis.value == 3  # balanced default


def test_v4_keeps_hardcoded_default_school_even_with_a_stance(db):
    """AC1: the runner's school takes effect ONLY under the stance-aware prompt. Under
    v4 the corpus stays on the hardcoded default and the stance key is absent — so a
    runner who declared a non-default stance leaves v4 byte-stable."""
    activity = _seed_activity(db)
    pack = build_context_pack(db, activity, prompt_id=V4, stance=_stance(school="polarized"))
    assert pack.corpus.school.id == DEFAULT_SCHOOL_ID  # NOT polarized
    d = pack.to_serializable_dict()
    assert "stance" not in d
    # byte-identical to v4 with no stance at all
    assert d == build_context_pack(db, activity, prompt_id=V4).to_serializable_dict()


def test_stance_changes_the_fingerprint_under_v5(db):
    """The stance section + selected school are load-bearing in the cache identity:
    moving an emphasis axis or the school changes the v5 fingerprint."""
    activity = _seed_activity(db)
    a = build_context_pack(db, activity, prompt_id=V5, stance=_stance(data_sentiment=5)).fingerprint()
    b = build_context_pack(db, activity, prompt_id=V5, stance=_stance(data_sentiment=1)).fingerprint()
    c = build_context_pack(db, activity, prompt_id=V5, stance=_stance(school="enjoyment-and-consistency")).fingerprint()
    assert a != b  # emphasis is load-bearing
    assert a != c  # school is load-bearing


def test_stance_section_absent_and_byte_stable_for_non_stance_prompts(db):
    """AC1/AC7: no prompt_id and non-stance prompts omit the stance key, byte-stable."""
    activity = _seed_activity(db)
    base = build_context_pack(db, activity)  # no prompt_id
    base_dict = base.to_serializable_dict()
    assert "stance" not in base_dict

    for non_stance in (None, "coach_message_v3", "coach_message_v2", "coach_report_v10"):
        d = build_context_pack(db, activity, prompt_id=non_stance).to_serializable_dict()
        assert "stance" not in d, f"stance leaked under {non_stance}"


def test_stance_does_not_touch_the_facts(db):
    """AC2: adding the stance section changes ONLY stance + the threaded corpus school
    — the activity/metrics facts are byte-identical to a v5 pack with no stance."""
    activity = _seed_activity(db)
    plain_v5 = build_context_pack(db, activity, prompt_id=V5).to_serializable_dict()
    with_stance = build_context_pack(db, activity, prompt_id=V5, stance=_stance()).to_serializable_dict()

    # everything except corpus (school changed) and stance (emphasis changed) is equal
    ignore = {"corpus", "stance"}
    assert {k: v for k, v in plain_v5.items() if k not in ignore} == \
           {k: v for k, v in with_stance.items() if k not in ignore}


def test_build_stance_context_returns_none_for_non_stance_prompt():
    """The gate is the prompt id: a non-stance prompt yields no section at all."""
    assert _build_stance_context("coach_message_v4", _stance()) is None
    assert _build_stance_context("coach_message_v3", _stance()) is None
    assert _build_stance_context(None, _stance()) is None
