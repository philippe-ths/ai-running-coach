"""M1 — RunnerMemoryProfile schema contract.

The profile is a structured JSON document of five named sections, each a capped
list of short plain-language lines, strict-coerced (`extra="forbid"`, per-section
count cap + per-line length cap). Human-readable, diffable, machine-validatable.
"""

import pytest
from pydantic import ValidationError

from app.schemas.coach_memory import (
    MAX_LINES_PER_SECTION,
    MAX_LINE_LENGTH,
    MEMORY_SECTION_FIELDS,
    RunnerMemoryProfile,
)


def test_valid_five_section_profile_builds():
    profile = RunnerMemoryProfile(
        who_you_are=["Marathoner, 3 years running", "Runs before work"],
        limits_and_constraints=["Left knee niggle, mentioned once"],
        goals_and_plans=["Valencia half, sub-1:45, October"],
        what_works_for_you=["Responds well to metronome cues on easy runs"],
        lately=["Open thread: agreed to try a metronome on easy runs"],
    )
    assert profile.goals_and_plans == ["Valencia half, sub-1:45, October"]


def test_all_empty_cold_start_profile_is_valid():
    profile = RunnerMemoryProfile()
    for field in MEMORY_SECTION_FIELDS:
        assert getattr(profile, field) == []


def test_unknown_section_key_is_rejected():
    with pytest.raises(ValidationError):
        RunnerMemoryProfile(unknown_section=["nope"])


def test_over_cap_section_is_rejected():
    too_many = [f"line {i}" for i in range(MAX_LINES_PER_SECTION + 1)]
    with pytest.raises(ValidationError):
        RunnerMemoryProfile(who_you_are=too_many)


def test_section_at_cap_is_accepted():
    at_cap = [f"line {i}" for i in range(MAX_LINES_PER_SECTION)]
    profile = RunnerMemoryProfile(lately=at_cap)
    assert len(profile.lately) == MAX_LINES_PER_SECTION


def test_over_long_line_is_rejected():
    too_long = "x" * (MAX_LINE_LENGTH + 1)
    with pytest.raises(ValidationError):
        RunnerMemoryProfile(limits_and_constraints=[too_long])


def test_line_at_length_cap_is_accepted():
    at_cap = "x" * MAX_LINE_LENGTH
    profile = RunnerMemoryProfile(goals_and_plans=[at_cap])
    assert profile.goals_and_plans == [at_cap]


def test_all_five_section_fields_present():
    assert MEMORY_SECTION_FIELDS == (
        "who_you_are",
        "limits_and_constraints",
        "goals_and_plans",
        "what_works_for_you",
        "lately",
    )
