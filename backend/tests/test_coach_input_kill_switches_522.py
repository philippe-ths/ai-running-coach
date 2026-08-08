"""#522: reversible kill switches that turn off eleven coach inputs.

Each flag defaults True (current behaviour: the item is present), so the rest of
the suite stays byte-stable. These tests pin the OFF path: with the flag set False
the named section / sub-field / prompt part disappears from what the coach receives,
and a guard confirms it is still present with the flag at its default.

Convention: a dropped whole pack section vanishes from the serialized dict (the
PACK_SECTIONS registry); a gated sub-field becomes None (same shape the coach
already tolerates for a run with no such data).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest

from app.core.config import settings
from app.models import Activity, DerivedMetric, User, UserProfile
from app.models.checkin import CheckIn
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach.context import build_context_pack
from app.services.coach.corpus import DEFAULT_SCHOOL_ID
from app.services.coach.prompts import build_system_prompt, render_voice_block
from app.services.coach.voice_rewrite import revoice_report
from app.services.coach.service import (
    _resolve_stance_for_activity,
    _resolve_voice_for_activity,
)
from app.services.coach.stance import Emphasis, StanceProfile
from app.services.coach.voice import resolve_voice

V11 = "coach_message_v11"


def _stance(school="polarized", data_sentiment=5, process_outcome=1):
    return StanceProfile(
        school_id=school,
        emphasis=Emphasis(data_sentiment=data_sentiment, process_outcome=process_outcome),
        is_default=False,
    )


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"ks_{uid}@example.com"))
    db.flush()
    return uid


_BASE = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def _activity(db, uid, day, **ov):
    a = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=ov.get("name", "Run"), type="Run",
        start_date=_BASE + timedelta(days=day),
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, avg_cadence=170.0, elev_gain_m=20.0,
        average_speed_mps=2.78, raw_summary={"average_temp": 15.0},
    )
    db.add(a)
    db.flush()
    return a


_STOPS = {
    "stopped_count": 2,
    "total_stopped_time_s": 90,
    "longest_stop_s": 60,
    "stops": [{"at_s": 600, "duration_s": 60}, {"at_s": 1200, "duration_s": 30}],
}


def _metrics(db, act, **ov):
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id, effort="easy",
        duration_class="standard", structure="continuous", is_hilly=False,
        is_race=False, effort_score=5.0, hr_drift=4.0, confidence="high",
        confidence_reasons=["full_coverage"], flags=[],
        stops_analysis=ov.get("stops_analysis", _STOPS),
    ))
    db.flush()


def _checkin(db, act, *, sleep_quality=4):
    db.add(CheckIn(id=uuid.uuid4(), activity_id=act.id, rpe=6, pain_score=0,
                   sleep_quality=sleep_quality))
    db.flush()


def _seed(db, *, with_checkin=True, with_history=True):
    """A v11-rich subject: profile, metrics with stops, a check-in with sleep, and a
    short history so recent_training (and previous_30d) populate."""
    uid = _user(db)
    db.add(UserProfile(
        user_id=uid, goal_type="half", experience_level="intermediate",
        weekly_days_available=4, current_weekly_km=35,
        max_hr=185, max_hr_source="user_entered",
    ))
    db.flush()
    if with_history:
        # priors across the last_30d and previous_30d windows (subject is day ~60).
        for day in (5, 12, 20, 28, 35, 45, 55):
            _metrics(db, _activity(db, uid, day))
    subject = _activity(db, uid, 60, name="Subject")
    _metrics(db, subject)
    if with_checkin:
        _checkin(db, subject)
    db.refresh(subject)
    return subject


def _pack_dict(db, activity):
    return build_context_pack(
        db, activity, prompt_id=V11, stance=_stance()
    ).to_serializable_dict()


# --- whole-section drops ------------------------------------------------------


def test_longitudinal_present_by_default_dropped_when_disabled(db, monkeypatch):
    activity = _seed(db)
    assert "longitudinal" in _pack_dict(db, activity)
    monkeypatch.setattr(settings, "COACH_LONGITUDINAL_ENABLED", False)
    assert "longitudinal" not in _pack_dict(db, activity)


def test_salience_present_by_default_dropped_when_disabled(db, monkeypatch):
    activity = _seed(db)
    assert "salience" in _pack_dict(db, activity)
    monkeypatch.setattr(settings, "COACH_SALIENCE_ENABLED", False)
    assert "salience" not in _pack_dict(db, activity)


def test_continuity_present_by_default_dropped_when_disabled(db, monkeypatch):
    activity = _seed(db)
    assert "continuity" in _pack_dict(db, activity)
    monkeypatch.setattr(settings, "COACH_CONTINUITY_ENABLED", False)
    assert "continuity" not in _pack_dict(db, activity)


# --- recent_training: only the previous_30d window drops ----------------------


def test_previous_30d_drops_but_other_windows_survive(db, monkeypatch):
    activity = _seed(db)
    rt = _pack_dict(db, activity).get("recent_training")
    assert rt is not None and "previous_30d" in rt
    monkeypatch.setattr(settings, "COACH_PREVIOUS_30D_ENABLED", False)
    rt = _pack_dict(db, activity)["recent_training"]
    assert "previous_30d" not in rt
    # the directly-comparable windows and the vs-prev reads are unaffected.
    assert "last_7d" in rt and "last_30d" in rt


# --- corpus: house schools and user materials ---------------------------------


def test_house_school_dropped_but_house_core_retained(db, monkeypatch):
    activity = _seed(db)
    corpus = _pack_dict(db, activity)["corpus"]
    assert corpus["school"] is not None
    assert corpus["house_principles"]  # HOUSE_CORE present
    monkeypatch.setattr(settings, "COACH_HOUSE_SCHOOLS_ENABLED", False)
    corpus = _pack_dict(db, activity)["corpus"]
    assert corpus["school"] is None
    assert corpus["house_principles"]  # HOUSE_CORE still present


def test_user_materials_subfield_dropped_when_disabled(db, monkeypatch):
    activity = _seed(db)
    # Enabled (no uploaded materials) => the key is present as an empty list.
    assert "user_materials" in _pack_dict(db, activity)["corpus"]
    monkeypatch.setattr(settings, "COACH_USER_MATERIALS_ENABLED", False)
    assert "user_materials" not in _pack_dict(db, activity)["corpus"]


# --- focus sub-fields: stops_analysis and sleep_quality -----------------------


def test_stops_analysis_dropped_for_coach_when_disabled(db, monkeypatch):
    activity = _seed(db)
    assert _pack_dict(db, activity)["metrics"]["stops_analysis"] is not None
    monkeypatch.setattr(settings, "COACH_STOPS_ANALYSIS_ENABLED", False)
    assert _pack_dict(db, activity)["metrics"]["stops_analysis"] is None


def test_sleep_quality_dropped_for_coach_when_disabled(db, monkeypatch):
    activity = _seed(db)
    assert _pack_dict(db, activity)["check_in"]["sleep_quality"] == 4
    monkeypatch.setattr(settings, "COACH_SLEEP_QUALITY_ENABLED", False)
    assert _pack_dict(db, activity)["check_in"]["sleep_quality"] is None


# --- prompt parts: playbook and rendered voice block --------------------------


def test_playbook_present_by_default_absent_when_disabled(monkeypatch):
    with_playbook = build_system_prompt(V11, "Intervals")
    assert "INTERVAL SESSION FOCUS" in with_playbook
    monkeypatch.setattr(settings, "COACH_PLAYBOOK_ENABLED", False)
    assert "INTERVAL SESSION FOCUS" not in build_system_prompt(V11, "Intervals")


def test_voice_block_present_by_default_absent_when_disabled(monkeypatch):
    """The switch still removes the runner's voice; #822 moved WHERE it does it.

    Voice reaches the report as a rewrite of the finished text rather than as a
    prompt block, so the switch is enforced there — off means every runner reads
    the baseline, which is exactly what Default already gives them. The block
    itself remains for the conversational turn and is still gated.
    """
    voice = resolve_voice(CoachingRelationship(voice_preset="roast"))
    assert "## YOUR VOICE FOR THIS RUNNER" in render_voice_block(V11, voice)
    monkeypatch.setattr(settings, "COACH_VOICE_BLOCK_ENABLED", False)
    assert render_voice_block(V11, voice) == ""


@pytest.mark.asyncio
async def test_voice_rewrite_skipped_when_switch_disabled(monkeypatch):
    """Off at the rewrite seam too, so no call is made and the baseline stands."""
    voice = resolve_voice(CoachingRelationship(voice_preset="roast"))
    monkeypatch.setattr(settings, "COACH_VOICE_BLOCK_ENABLED", False)

    called = False

    def _boom(*a, **k):  # pragma: no cover - must never run
        nonlocal called
        called = True
        raise AssertionError("the rewrite must not call a model when switched off")

    monkeypatch.setattr("app.services.coach.turn.build_client", _boom)
    result = await revoice_report(
        baseline="You ran 5km.", voice=voice, user_id=None, validate=lambda t: []
    )
    assert result is None and not called


def test_voice_switch_enforced_inside_shared_render(monkeypatch):
    """The switch lives inside render_voice_block, the ONE render both the report and
    chat go through, so no call site can bypass it (chat's voice path previously did)."""
    from app.services.coach.prompts import render_voice_block

    voice = resolve_voice(None)
    assert render_voice_block(V11, voice) != ""  # on by default
    monkeypatch.setattr(settings, "COACH_VOICE_BLOCK_ENABLED", False)
    assert render_voice_block(V11, voice) == ""  # off at the source, every caller


# --- coaching_relationship: voice + stance resolve to defaults when off --------


def _relationship(db, uid):
    db.add(CoachingRelationship(
        id=uuid.uuid4(), user_id=uid,
        voice_preset="drill_sergeant", voice_warmth=1, voice_humor=1,
        voice_force=5, voice_energy=5,
        stance_school="polarized", stance_data_sentiment=5, stance_process_outcome=1,
    ))
    db.flush()


def test_relationship_disabled_resolves_default_stance(db, monkeypatch):
    uid = _user(db)
    _relationship(db, uid)
    activity = _activity(db, uid, 10)
    # Enabled: the runner's declared school is read.
    assert _resolve_stance_for_activity(db, activity).school_id == "polarized"
    monkeypatch.setattr(settings, "COACH_RELATIONSHIP_ENABLED", False)
    resolved = _resolve_stance_for_activity(db, activity)
    assert resolved.school_id == DEFAULT_SCHOOL_ID
    assert resolved.is_default is True


def test_relationship_disabled_resolves_default_voice(db, monkeypatch):
    uid = _user(db)
    _relationship(db, uid)
    activity = _activity(db, uid, 10)
    assert _resolve_voice_for_activity(db, activity).preset is not None  # declared preset read
    monkeypatch.setattr(settings, "COACH_RELATIONSHIP_ENABLED", False)
    # Default voice has no preset (the moderate centre persona).
    assert _resolve_voice_for_activity(db, activity).preset is None


# --- the UI feature-flags endpoint --------------------------------------------


def test_feature_flags_endpoint_reports_disabled_state(client, monkeypatch):
    assert client.get("/api/coach/feature-flags").json() == {
        "voice": True, "stance": True, "user_materials": True,
        "sleep_quality": True, "stops_analysis": True, "memory": True,
        # #784: the thread SURFACE rides the same map, so the frontend learns
        # from one place whether to render the launcher and sheet at all.
        "threads": True,
    }
    # voice is derived: relationship AND voice-block must both be on.
    monkeypatch.setattr(settings, "COACH_VOICE_BLOCK_ENABLED", False)
    monkeypatch.setattr(settings, "COACH_HOUSE_SCHOOLS_ENABLED", False)
    body = client.get("/api/coach/feature-flags").json()
    assert body["voice"] is False       # voice block off
    assert body["stance"] is False      # house schools off => stance panel inert
    assert body["user_materials"] is True
