"""#658: the offline runner-memory eval rubric + self-test.

Validates that each deterministic assertion both passes a clean write and catches
its own violation (the inverted-oracle gate), and that the harness self-test
agrees. No DB, no API key.
"""

from app.schemas.coach_memory import RunnerMemoryProfile
from app.services.coach.eval.memory.fixtures import (
    deliberately_bad_memory,
    known_good_memory,
)
from app.services.coach.eval.memory.harness import run_self_test
from app.services.coach.eval.memory.rubric import (
    AssertionStatus,
    MemoryScore,
    score_memory,
    score_stored_profile,
)
from app.services.coach.memory_update import MemoryCandidate, MemorySources, SourceItem


def _status(score: MemoryScore, name: str) -> AssertionStatus:
    for a in score.assertions:
        if a.name == name:
            return a.status
    raise AssertionError(f"assertion {name} not present")


def test_self_test_passes():
    assert run_self_test() is True


def test_good_fixture_fails_nothing_and_applies_all():
    score = score_memory(*known_good_memory())
    assert score.failed_count == 0
    # Every assertion is applicable in the good fixture (it has lines in every
    # durable section, a plan, and a grounded safety limit).
    assert score.applicable_count == 4
    assert score.pass_rate == 1.0


def test_bad_fixture_fails_every_assertion():
    score = score_memory(*deliberately_bad_memory())
    failed = {a.name for a in score.assertions if a.status is AssertionStatus.FAIL}
    assert failed == {
        "no_inferred_verdict",
        "durable_lines_grounded",
        "plan_from_commitment",
        "safety_limit_held",
    }


def test_verdict_floor_catches_inferred_verdict():
    profile = RunnerMemoryProfile(who_you_are=["Ignores easy days and overcooks the easy runs"])
    score = score_stored_profile(profile)
    assert _status(score, "no_inferred_verdict") is AssertionStatus.FAIL


def test_verdict_floor_allows_stated_constraint():
    # A legitimately STATED preference/limit must not trip the narrow verdict floor.
    profile = RunnerMemoryProfile(
        limits_and_constraints=["No morning runs — works early shifts"],
        what_works_for_you=["Prefers evening runs", "Gels make me nauseous"],
    )
    score = score_stored_profile(profile)
    assert _status(score, "no_inferred_verdict") is AssertionStatus.PASS


def test_coach_proposed_plan_is_not_a_commitment():
    sources = MemorySources(
        sources=(
            SourceItem(id="cc1", kind="coach_chat", text="want to try a 50k?", durable=False, role="coach"),
        )
    )
    candidates = [
        MemoryCandidate(text="Doing a 50k", section="goals_and_plans", supporting_source_ids=["cc1"]),
    ]
    profile = RunnerMemoryProfile(goals_and_plans=["Doing a 50k"])
    score = score_memory(sources, candidates, profile)
    assert _status(score, "plan_from_commitment") is AssertionStatus.FAIL


def test_committed_plan_passes():
    sources = MemorySources(
        sources=(
            SourceItem(id="rr1", kind="chat", text="yeah I'm doing the 50k", durable=True, role="runner"),
        )
    )
    candidates = [
        MemoryCandidate(text="Doing a 50k", section="goals_and_plans", supporting_source_ids=["rr1"]),
    ]
    profile = RunnerMemoryProfile(goals_and_plans=["Doing a 50k"])
    score = score_memory(sources, candidates, profile)
    assert _status(score, "plan_from_commitment") is AssertionStatus.PASS


def test_dropped_safety_limit_fails():
    sources = MemorySources(
        sources=(
            SourceItem(id="rc1", kind="check_in_note", text="knee is sore", durable=True),
        )
    )
    candidates = [
        MemoryCandidate(text="Possible knee niggle", section="limits_and_constraints", supporting_source_ids=["rc1"], safety_relevant=True),
    ]
    profile = RunnerMemoryProfile(limits_and_constraints=[])  # dropped
    score = score_memory(sources, candidates, profile)
    assert _status(score, "safety_limit_held") is AssertionStatus.FAIL


def test_coach_echoed_durable_fact_fails_anti_echo():
    sources = MemorySources(
        sources=(
            SourceItem(id="cc1", kind="coach_chat", text="you love long tempos", durable=False, role="coach"),
        )
    )
    candidates = [
        MemoryCandidate(text="Loves long tempo blocks", section="what_works_for_you", supporting_source_ids=["cc1"]),
    ]
    profile = RunnerMemoryProfile(what_works_for_you=["Loves long tempo blocks"])
    score = score_memory(sources, candidates, profile)
    assert _status(score, "durable_lines_grounded") is AssertionStatus.FAIL
