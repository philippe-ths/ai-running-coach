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
    _chat(db, recent, "assistant", "Great — let's build toward it")  # coach turn: context, non-durable

    sources = gather_memory_sources(db, uid)

    texts = [s.text for s in sources.sources]
    assert "Left knee niggle since February" in texts  # long-ago fact still retrieved
    assert "I'm aiming for the Valencia half in October" in texts
    # #657: the coach's turn is now INCLUDED as dialogue context, but as a
    # non-durable source it can never ground a durable fact.
    assert "Great — let's build toward it" in texts
    coach_turn = next(s for s in sources.sources if s.text == "Great — let's build toward it")
    assert coach_turn.durable is False
    assert coach_turn.role == "coach"
    assert coach_turn.id not in sources.durable_source_ids
    # The runner's own words remain durable and citable for graduation.
    runner_turn = next(s for s in sources.sources if s.text.startswith("I'm aiming for the Valencia"))
    assert runner_turn.durable is True and runner_turn.id in sources.durable_source_ids
    # Both chat turns share the activity thread, so the dialogue can be interleaved.
    assert coach_turn.thread_id == runner_turn.thread_id is not None


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


def test_writer_messages_render_the_dialogue_with_both_sides(db):
    # #657: the coach's turn appears as CONTEXT alongside the runner's turn in the
    # same conversation, so the writer can read an elliptical commitment against it.
    uid = _user(db)
    act = _activity(db, uid, when=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    _chat(db, act, "assistant", "Want to do 4x1km on Tuesday?")
    _chat(db, act, "user", "yeah lets do that")

    sources = gather_memory_sources(db, uid)
    _system, user = build_writer_messages(sources)

    assert "CONVERSATIONS" in user
    assert "Want to do 4x1km on Tuesday?" in user  # coach turn is present as context
    assert "yeah lets do that" in user  # runner's elliptical commitment
    # The coach turn is labelled as the coach speaking, so it is not read as the runner.
    assert "(coach," in user and "(runner," in user


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


def test_single_commitment_graduates_a_plan_end_to_end(db):
    # #657: with the live plan bar at 1, a single clear runner commitment becomes a
    # durable plan (no waiting for a second mention).
    uid = _user(db)
    a1 = _activity(db, uid)
    _note(db, a1, "Going to do a 10k next month")

    stub = _StubClient([_cand("Plans to run a 10k next month", "goals_and_plans", ["note0"])])
    row = asyncio.run(update_memory(db, uid, client=stub))

    profile = RunnerMemoryProfile.model_validate(row.profile)
    assert profile.goals_and_plans == ["Plans to run a 10k next month"]


def test_single_source_still_does_not_graduate_a_non_plan_durable_section(db):
    # The lowered PLAN bar does not lower the corroboration bar for the other durable
    # sections; character still needs >=2 distinct sources.
    uid = _user(db)
    a1 = _activity(db, uid)
    _note(db, a1, "Ran before work today")

    stub = _StubClient([_cand("Runs before work", "who_you_are", ["note0"])])
    row = asyncio.run(update_memory(db, uid, client=stub))

    profile = RunnerMemoryProfile.model_validate(row.profile)
    assert profile.who_you_are == []  # one source -> still held out of a >=2 section


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


# --------------------------------------------------------------------------- #
# #607 — soft over-budget entry gate (PAUSE, not overshoot; non-fatal).
# --------------------------------------------------------------------------- #
def _arm_over_budget(monkeypatch, user_id):
    """Drive the real budget gate over a per-user daily ceiling for `user_id`.

    Uses a fresh in-process gate + a real recorded overspend (not a mock of the
    thing under test), mirroring test_budget_cap_guard. Returns nothing; caller
    resets via budget.set_gate(None)."""
    from app.core.config import settings
    from app.services.coach import budget as B

    B.set_gate(B.new_in_memory_gate())
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 0.01)
    B.record(user_id, "claude-opus-4-8", 1_000_000, 0)  # ~$5.00 >> $0.01 ceiling


def test_over_budget_skips_llm_call_and_writes_nothing(db, monkeypatch):
    """Over budget: the rewrite-from-source pass PAUSES — no Haiku call, no write,
    the profile is unchanged (retryable on the next non-fallback report)."""
    from app.services.coach import budget as B

    uid = _user(db)
    act = _activity(db, uid)
    _note(db, act, "Left knee niggle since February")  # sources present (not cold start)

    _arm_over_budget(monkeypatch, uid)
    try:
        stub = _StubClient([_cand("should never appear", "who_you_are", ["note0"])])
        result = asyncio.run(update_memory(db, uid, client=stub))
        assert result is None            # non-fatal skip
        assert stub.calls == 0           # the aux Haiku call was NOT made
        assert get_memory(db, uid) is None  # nothing written -> next report retries
    finally:
        B.set_gate(None)


def test_under_budget_proceeds_as_before(db, monkeypatch):
    """Under budget: behaviour is unchanged — the pass calls the writer and stores."""
    from app.services.coach import budget as B

    uid = _user(db)
    a1 = _activity(db, uid, when=datetime(2026, 5, 1, 7, 0, tzinfo=timezone.utc))
    a2 = _activity(db, uid, when=datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    _note(db, a1, "Sub-3 marathon")
    _note(db, a2, "Sub-3 marathon again")

    B.set_gate(B.new_in_memory_gate())  # fresh gate, no ceiling armed -> not over
    try:
        stub = _StubClient([_cand("Targeting a sub-3 marathon", "goals_and_plans", ["note0", "note1"])])
        result = asyncio.run(update_memory(db, uid, client=stub))
        assert stub.calls == 1
        assert result is not None
        assert get_memory(db, uid) is not None
    finally:
        B.set_gate(None)
