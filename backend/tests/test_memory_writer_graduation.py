"""M2 — graduate-or-drop, the deterministic structural half of the incident fix.

These are CI-gated (no LLM): given candidate lines tagged with supporting source
ids, `apply_graduation` promotes a line to a permanent section (1-4) only on >=2
distinct sources, holds a safety-relevant limit on one mention, treats `lately`
as a probationary pen, drops a fabricated (0-source) line everywhere, and caps
every section. Support is re-derived from the current source set, so there is no
stored counter that can drift or poison (the structural fix for the rest-day
fixation incident).
"""

from app.schemas.coach_memory import MAX_LINES_PER_SECTION
from app.services.coach.memory_update import (
    GRADUATION_MIN_SOURCES,
    MemoryCandidate,
    apply_graduation,
)

KNOWN = {"s1", "s2", "s3", "s4", "s5", "s6", "s7"}


def _c(text, section, sources, safety=False):
    return MemoryCandidate(
        text=text, section=section, supporting_source_ids=sources, safety_relevant=safety
    )


def test_single_source_line_does_not_graduate_to_durable_section():
    profile = apply_graduation([_c("Targeting a sub-3 marathon", "goals_and_plans", ["s1"])], KNOWN)
    assert profile.goals_and_plans == []


def test_two_distinct_sources_graduate_to_durable_section():
    profile = apply_graduation(
        [_c("Targeting a sub-3 marathon", "goals_and_plans", ["s1", "s2"])], KNOWN
    )
    assert profile.goals_and_plans == ["Targeting a sub-3 marathon"]


def test_repeated_same_source_does_not_count_twice():
    # Citing one source twice is one distinct source — below the threshold.
    profile = apply_graduation(
        [_c("Runs before work", "who_you_are", ["s1", "s1", "s1"])], KNOWN
    )
    assert profile.who_you_are == []


def test_safety_relevant_limit_held_on_single_mention():
    profile = apply_graduation(
        [_c("Possible left-knee niggle, mentioned once", "limits_and_constraints", ["s1"], safety=True)],
        KNOWN,
    )
    assert profile.limits_and_constraints == ["Possible left-knee niggle, mentioned once"]


def test_non_safety_single_source_limit_drops():
    profile = apply_graduation(
        [_c("Prefers afternoon runs", "limits_and_constraints", ["s1"], safety=False)], KNOWN
    )
    assert profile.limits_and_constraints == []


def test_lately_is_probationary_pen_accepts_single_source():
    profile = apply_graduation(
        [_c("Open thread: agreed to try a metronome", "lately", ["s1"])], KNOWN
    )
    assert profile.lately == ["Open thread: agreed to try a metronome"]


def test_fabricated_zero_source_line_drops_everywhere():
    # No cited source is valid -> 0 distinct support -> stored nowhere.
    profile = apply_graduation(
        [
            _c("Ran a secret ultra", "lately", []),
            _c("Hates hills", "what_works_for_you", ["ghost"]),
        ],
        KNOWN,
    )
    assert profile.lately == []
    assert profile.what_works_for_you == []


def test_hallucinated_source_ids_contribute_nothing():
    # Two cited ids but neither is a real source -> does not graduate.
    profile = apply_graduation(
        [_c("Fabricated goal", "goals_and_plans", ["ghost1", "ghost2"])], KNOWN
    )
    assert profile.goals_and_plans == []


def test_sections_are_capped():
    over = [
        _c(f"goal {i}", "goals_and_plans", ["s1", "s2"]) for i in range(MAX_LINES_PER_SECTION + 4)
    ]
    profile = apply_graduation(over, KNOWN)
    assert len(profile.goals_and_plans) == MAX_LINES_PER_SECTION


def test_duplicate_lines_are_deduped():
    profile = apply_graduation(
        [
            _c("Runs before work", "who_you_are", ["s1", "s2"]),
            _c("Runs before work", "who_you_are", ["s3", "s4"]),
        ],
        KNOWN,
    )
    assert profile.who_you_are == ["Runs before work"]


def test_idempotent_membership_on_identical_candidates():
    candidates = [
        _c("Targeting a sub-3 marathon", "goals_and_plans", ["s1", "s2"]),
        _c("Open thread: metronome on easy runs", "lately", ["s3"]),
    ]
    first = apply_graduation(candidates, KNOWN)
    second = apply_graduation(candidates, KNOWN)
    assert first.model_dump() == second.model_dump()


def test_threshold_constant_is_two():
    assert GRADUATION_MIN_SOURCES == 2


def test_live_plan_bar_default_is_one():
    # #657: the wired default lowers ONLY the plan bar to 1; the general
    # corroboration constant is untouched.
    from app.core.config import settings

    assert settings.COACH_MEMORY_PLAN_GRADUATION_MIN == 1
    assert GRADUATION_MIN_SOURCES == 2


# --- runner-stated graduates, coach-said (digests) does not (anti-coach-echo) --- #


def test_digest_only_support_does_not_graduate_a_durable_section():
    # Two cited sources, but both are coach digests (not in durable set) -> a
    # coach conclusion repeated across reports cannot harden into durable memory.
    profile = apply_graduation(
        [_c("Targeting a sub-3 marathon", "goals_and_plans", ["d1", "d2"])],
        {"s1", "s2", "d1", "d2"},
        durable_source_ids={"s1", "s2"},
    )
    assert profile.goals_and_plans == []


def test_digest_support_still_allows_a_lately_thread():
    # A single coach digest legitimately grounds a `lately` open thread.
    profile = apply_graduation(
        [_c("Open thread: agreed last time to add a long run", "lately", ["d1"])],
        {"s1", "d1"},
        durable_source_ids={"s1"},
    )
    assert profile.lately == ["Open thread: agreed last time to add a long run"]


def test_two_runner_statements_graduate_even_with_digests_present():
    profile = apply_graduation(
        [_c("Targeting a sub-3 marathon", "goals_and_plans", ["s1", "s2", "d1"])],
        {"s1", "s2", "d1"},
        durable_source_ids={"s1", "s2"},
    )
    assert profile.goals_and_plans == ["Targeting a sub-3 marathon"]


# --- #657: goals_and_plans graduates on a single commitment at a lowered bar --- #


def test_plan_graduates_on_single_source_at_bar_one():
    # A single clear commitment ("I'll do 7x400m") becomes a durable plan when the
    # plan bar is lowered to 1 (the writer now sees the dialogue and can tell a
    # commitment from a question, so the crude second gate is not needed here).
    profile = apply_graduation(
        [_c("Plans to run 7x400m intervals", "goals_and_plans", ["s1"])],
        KNOWN,
        plan_min_sources=1,
    )
    assert profile.goals_and_plans == ["Plans to run 7x400m intervals"]


def test_lowered_plan_bar_does_not_lower_other_durable_sections():
    # Lowering the PLAN bar must not lower the corroboration bar for character,
    # preferences, or firm limits — those keep >=2.
    profile = apply_graduation(
        [_c("Runs before work", "who_you_are", ["s1"])],
        KNOWN,
        plan_min_sources=1,
    )
    assert profile.who_you_are == []


def test_coach_only_support_never_graduates_a_plan_even_at_bar_one():
    # The anti-echo backstop HOLDS at the lowered bar: a plan citing only a
    # non-durable (coach) source drops, so a coach's idea can never become the
    # runner's durable plan even when a single runner source would now suffice.
    profile = apply_graduation(
        [_c("Plans 1k reps", "goals_and_plans", ["c1"])],
        {"s1", "s2", "c1"},
        durable_source_ids={"s1", "s2"},  # c1 is a KNOWN coach turn, but NOT durable
        plan_min_sources=1,
    )
    assert profile.goals_and_plans == []


def test_plan_bar_defaults_to_the_two_source_constant():
    # Without the override the function keeps the historical >=2 default for plans,
    # so the LIVE bar-of-1 is a wiring choice (update_memory), not baked into the gate.
    profile = apply_graduation([_c("A goal", "goals_and_plans", ["s1"])], KNOWN)
    assert profile.goals_and_plans == []
