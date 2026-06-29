"""M3 — the `memory` pack section + v13 byte-stability + the G5 validator stance.

The runner memory profile reaches the coach as the `memory` pack section under v13
only. Pinned here: the section surfaces the profile whole with provenance; under v13
the pack MINUS memory is byte-identical to v12 (the section moved no other fact); the
section drops byte-stably on cold start, kill-switch off, an all-empty profile, and
under any non-memory prompt; and (G5) the validator does NOT block memory citation
while the medical-scope floor still polices memory-grounded prose.
"""

import uuid
from datetime import datetime, timezone

from app.core.config import settings
from app.models import Activity, DerivedMetric, User
from app.schemas.coach import CoachMessageReport, CoachNextStep
from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.coach.context import build_context_pack
from app.services.coach.memory_store import upsert_memory
from app.services.coach.validator import validate_message_policy

V12 = "coach_message_v12"
V13 = "coach_message_v13"


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id):
    a = Activity(
        id=uuid.uuid4(), user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run",
        start_date=datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc),
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
    ))
    db.flush()
    return a


def _seed_profile(db, user_id, **sections):
    upsert_memory(
        db, user_id,
        profile=RunnerMemoryProfile(**sections),
        model_id="claude-haiku-4-5",
        source_report_count=4,
        grounded_through=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Byte-stability + surfacing
# --------------------------------------------------------------------------- #
def test_v13_pack_minus_memory_is_byte_identical_to_v12(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid, goals_and_plans=["Valencia half, sub-1:45, October"])

    p13 = build_context_pack(db, act, prompt_id=V13)
    p12 = build_context_pack(db, act, prompt_id=V12)

    assert p13.memory is not None
    d13 = p13.to_serializable_dict()
    d13.pop("memory", None)
    assert d13 == p12.to_serializable_dict(), "the memory section moved a fact"


def test_memory_section_surfaces_the_whole_profile_with_provenance(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(
        db, uid,
        who_you_are=["Runs before work"],
        limits_and_constraints=["Left knee niggle, mentioned once"],
        goals_and_plans=["Valencia half, sub-1:45, October"],
        lately=["Open thread: agreed to try a metronome"],
    )

    mem = build_context_pack(db, act, prompt_id=V13).memory
    assert mem is not None
    assert mem.who_you_are == ["Runs before work"]
    assert mem.goals_and_plans == ["Valencia half, sub-1:45, October"]
    assert mem.lately == ["Open thread: agreed to try a metronome"]
    assert mem.source_report_count == 4
    assert mem.last_updated_days_ago is not None and mem.last_updated_days_ago >= 0


# --------------------------------------------------------------------------- #
# Byte-stable drop: cold start, kill-switch, empty profile, non-memory prompt
# --------------------------------------------------------------------------- #
def test_cold_start_no_profile_drops_the_section(db):
    uid = _user(db)
    act = _activity(db, uid)

    p13 = build_context_pack(db, act, prompt_id=V13)
    assert p13.memory is None
    assert "memory" not in p13.to_serializable_dict()


def test_kill_switch_off_drops_the_section(db, monkeypatch):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid, goals_and_plans=["Valencia half"])
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", False)

    p13 = build_context_pack(db, act, prompt_id=V13)
    assert p13.memory is None
    assert "memory" not in p13.to_serializable_dict()


def test_all_empty_profile_drops_the_section(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid)  # a row exists but graduated nothing

    p13 = build_context_pack(db, act, prompt_id=V13)
    assert p13.memory is None


def test_non_memory_prompt_never_carries_memory(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid, goals_and_plans=["Valencia half"])

    p12 = build_context_pack(db, act, prompt_id=V12)
    assert p12.memory is None
    assert "memory" not in p12.to_serializable_dict()


# --------------------------------------------------------------------------- #
# G5 — memory is citable; the medical-scope floor still polices memory prose
# --------------------------------------------------------------------------- #
def _msg(message):
    return CoachMessageReport(
        message=message,
        headline="Noted",
        next_steps=[CoachNextStep(action="Keep easy runs easy", details="", why="")],
        risks=[],
        questions=[],
    )


def test_validator_does_not_block_memory_citation(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid, limits_and_constraints=["Left knee niggle, mentioned once"])
    pack = build_context_pack(db, act, prompt_id=V13)

    report = _msg("You mentioned your knee, so let's keep today relaxed and see how it settles.")
    violations = validate_message_policy(report, pack)

    assert not any(v.rule == "medical_overreach" for v in violations)


def test_medical_scope_floor_still_fires_on_memory_grounded_overreach(db):
    uid = _user(db)
    act = _activity(db, uid)
    _seed_profile(db, uid, limits_and_constraints=["Left knee niggle, mentioned once"])
    pack = build_context_pack(db, act, prompt_id=V13)

    report = _msg("Since you mentioned your knee, I diagnose you with patellar tendinopathy.")
    violations = validate_message_policy(report, pack)

    assert any(v.rule == "medical_overreach" for v in violations)
