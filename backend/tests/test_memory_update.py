"""M2 — the rewrite-from-source writer, CI-gated structural invariants (stub LLM).

These pin the anti-incident guarantees deterministically (no real LLM): anti-echo
input construction, cold start, durable-fact persistence via the section-shaped
gather, end-to-end graduate-or-drop through a stubbed writer, idempotency, and
that a coach digest never graduates a durable fact. The judgment-quality replay
corpus (does the real LLM supersede / surface-a-question / not fixate) is the
eval-validated tier, run separately as a real-LLM scorecard.
"""

import asyncio
import copy
import uuid
from datetime import datetime, timezone

from app.models import Activity, CheckIn, CoachChatMessage, RunnerMemory, User
from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.coach.llm import Usage
from app.services.coach.memory_store import get_memory, upsert_memory
from app.services.coach.memory_update import (
    MemorySources,
    build_writer_messages,
    gather_memory_sources,
    update_memory,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
class _StubClient:
    """A stub Anthropic client returning canned candidates, so the deterministic
    apply-logic is exercised without a real LLM."""

    def __init__(self, candidates, model="claude-haiku-4-5"):
        self.model = model
        self.calls = 0
        self.last_user = None
        self.last_system = None
        self._candidates = candidates

    async def generate_structured_with_usage(self, *, system, user, tool, max_tokens):
        self.calls += 1
        self.last_system = system
        self.last_user = user
        return {"candidates": self._candidates}, Usage(input_tokens=10, output_tokens=20)


def _user(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"test_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, *, when=None):
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run",
        type="Run",
        start_date=when or datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3600,
        elapsed_time_s=3700,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=10.0,
        average_speed_mps=2.78,
        raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


def _note(db, activity, text):
    db.add(CheckIn(id=uuid.uuid4(), activity_id=activity.id, notes=text))
    db.flush()


def _chat(db, activity, role, content):
    db.add(CoachChatMessage(id=uuid.uuid4(), activity_id=activity.id, role=role, content=content))
    db.flush()


def _cand(text, section, sources, safety=False):
    return {
        "text": text,
        "section": section,
        "supporting_source_ids": sources,
        "safety_relevant": safety,
    }


# --------------------------------------------------------------------------- #
# Cold start
# --------------------------------------------------------------------------- #
def test_cold_start_no_sources_writes_nothing_and_calls_no_llm(db):
    uid = _user(db)
    stub = _StubClient([_cand("should never appear", "who_you_are", ["note0", "note1"])])

    result = asyncio.run(update_memory(db, uid, client=stub))

    assert result is None
    assert stub.calls == 0  # no LLM call, no enqueue loop
    assert get_memory(db, uid) is None


# --------------------------------------------------------------------------- #
# Gather: durable-fact persistence + source kinds (G8)
# --------------------------------------------------------------------------- #
def test_gather_returns_stated_facts_as_durable_sources(db):
    uid = _user(db)
    old = _activity(db, uid, when=datetime(2025, 2, 1, 7, 0, tzinfo=timezone.utc))
    _note(db, old, "Left knee niggle since February")
    recent = _activity(db, uid, when=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    _chat(db, recent, "user", "I'm aiming for the Valencia half in October")
    _chat(db, recent, "assistant", "Great — let's build toward it")  # coach message, excluded

    sources = gather_memory_sources(db, uid)

    texts = [s.text for s in sources.sources]
    assert "Left knee niggle since February" in texts  # long-ago fact still retrieved
    assert "I'm aiming for the Valencia half in October" in texts
    assert "Great — let's build toward it" not in texts  # role=assistant excluded
    # All runner-authored sources are durable (citable for graduation).
    assert sources.durable_source_ids == sources.source_ids


def test_gather_has_no_profile_field():
    # Anti-echo by type: the bundle the writer is handed cannot carry a prior profile.
    assert not hasattr(MemorySources(), "profile")
    assert "profile" not in {f for f in MemorySources.__dataclass_fields__}


# --------------------------------------------------------------------------- #
# Anti-echo: the writer's own prior profile never reaches the prompt
# --------------------------------------------------------------------------- #
def test_writer_messages_never_contain_the_prior_profile(db):
    uid = _user(db)
    act = _activity(db, uid)
    _note(db, act, "Training for a spring marathon")
    # Seed a POISONED prior profile — the exact incident shape.
    poison = "IGNORES EASY GUIDANCE — fixated verdict that must never echo"
    upsert_memory(db, uid, profile=RunnerMemoryProfile(who_you_are=[poison]))

    sources = gather_memory_sources(db, uid)
    system, user = build_writer_messages(sources)

    assert poison not in system
    assert poison not in user
    assert poison not in repr(sources)


# --------------------------------------------------------------------------- #
# End-to-end through a stubbed writer: graduate-or-drop + provenance + idempotency
# --------------------------------------------------------------------------- #
def test_two_distinct_check_ins_graduate_a_durable_fact(db):
    uid = _user(db)
    a1 = _activity(db, uid, when=datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc))
    a2 = _activity(db, uid, when=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    _note(db, a1, "Going for a sub-3 marathon")
    _note(db, a2, "Still chasing sub-3 in the spring")

    # Two real note sources exist (note0, note1) — the stub cites both.
    stub = _StubClient([_cand("Targeting a sub-3 marathon", "goals_and_plans", ["note0", "note1"])])
    row = asyncio.run(update_memory(db, uid, client=stub))

    assert stub.calls == 1
    assert row is not None
    assert row.model_id == "claude-haiku-4-5"
    assert row.source_report_count == 2
    profile = RunnerMemoryProfile.model_validate(row.profile)
    assert profile.goals_and_plans == ["Targeting a sub-3 marathon"]


def test_single_check_in_does_not_graduate_end_to_end(db):
    uid = _user(db)
    a1 = _activity(db, uid)
    _note(db, a1, "Mentioned wanting to try a 10k")

    stub = _StubClient([_cand("Wants to try a 10k", "goals_and_plans", ["note0"])])
    row = asyncio.run(update_memory(db, uid, client=stub))

    profile = RunnerMemoryProfile.model_validate(row.profile)
    assert profile.goals_and_plans == []  # one source -> held out of the durable section


def test_writer_pass_is_idempotent_on_identical_sources(db):
    uid = _user(db)
    a1 = _activity(db, uid, when=datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc))
    a2 = _activity(db, uid, when=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    _note(db, a1, "Sub-3 marathon")
    _note(db, a2, "Sub-3 marathon again")
    candidates = [_cand("Targeting a sub-3 marathon", "goals_and_plans", ["note0", "note1"])]

    first = asyncio.run(update_memory(db, uid, client=_StubClient(candidates)))
    first_profile = copy.deepcopy(first.profile)  # snapshot before the second pass mutates the shared row
    second = asyncio.run(update_memory(db, uid, client=_StubClient(candidates)))

    # No section-membership drift, no growth, one row.
    assert first_profile == second.profile
    assert RunnerMemoryProfile.model_validate(second.profile).goals_and_plans == ["Targeting a sub-3 marathon"]
    assert len(db.query(RunnerMemory).filter_by(user_id=uid).all()) == 1
