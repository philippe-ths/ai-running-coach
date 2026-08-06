"""The clause set the live coach prompts are composed from (#803).

These tests are deliberately version-count-agnostic: they walk `COMPOSED_PROMPT_IDS`
and assert properties that must hold for every live version, present and future. Adding
a version adds a row to `PROSE_VARIANTS` and its own test; nothing here changes.

The load-bearing one is the floor. `compose` is the only way a live prompt is built and
it refuses a clause set carrying no floor clause, so a version that omitted its floor
would fail at import rather than ship a prompt without a lane.
"""

import itertools

import pytest

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_clauses import (
    COMPOSED_OPENER_PROMPTS,
    COMPOSED_PROMPT_IDS,
    COMPOSED_PROMPT_VERSIONS,
    DELIVERY,
    GROUP_ORIENTATION_V1,
    GROUP_ORIENTATION_V2,
    GROUP_ORIENTATION_V3,
    IDENTITY,
    MissingSafetyFloorError,
    OPENER_SAFETY_FLOOR,
    PROSE_VARIANTS,
    SAFETY_FLOOR,
    Clause,
    ProseVariant,
    compose,
    fuller_clauses,
    opener_clauses,
)
from app.services.coach.prompt_features import PromptFeature, features_for


# ---------------------------------------------------------------------------
# The floor, by construction
# ---------------------------------------------------------------------------


def test_compose_refuses_a_clause_set_with_no_floor():
    """The mechanism, not a comment: a clause set without a floor does not produce a
    prompt, it raises."""
    with pytest.raises(MissingSafetyFloorError) as excinfo:
        compose([IDENTITY, DELIVERY])
    # The message names what was offered, so the failure points at the version.
    assert "identity" in str(excinfo.value)
    assert "delivery" in str(excinfo.value)


def test_compose_accepts_a_clause_set_carrying_a_floor():
    assert compose([IDENTITY, SAFETY_FLOOR]) == IDENTITY.text + SAFETY_FLOOR.text


def test_every_live_version_carries_a_floor_clause_in_both_modes():
    for prompt_id in COMPOSED_PROMPT_IDS:
        assert SAFETY_FLOOR in fuller_clauses(prompt_id), prompt_id
        assert OPENER_SAFETY_FLOOR in opener_clauses(prompt_id), prompt_id


def test_no_version_declaration_can_drop_the_floor(monkeypatch):
    """A version declaration says two things: which prose variants it takes, and which
    capabilities it carries. Neither vocabulary contains anything that could remove the
    floor. Exhaustive over every prose-variant combination a declaration can express."""
    probe = "probe_version"
    for size in range(len(ProseVariant) + 1):
        for combo in itertools.combinations(list(ProseVariant), size):
            monkeypatch.setitem(PROSE_VARIANTS, probe, frozenset(combo))
            assert SAFETY_FLOOR.text in compose(fuller_clauses(probe)), combo
    # The capability side: the only capability that reaches a clause is BODY, and the
    # version carrying it still has its floor.
    assert SAFETY_FLOOR.text in COMPOSED_PROMPT_VERSIONS["coach_message_lean_grouped_v8"]


def test_the_floor_clause_holds_the_lane():
    """A floor edit should be loud. These are the sentences the deterministic validator
    and the eval safety sensors exist to back up."""
    text = SAFETY_FLOOR.text
    assert "Stay in general-wellness coaching." in text
    assert "Do not diagnose, name a condition, give a drug or supplement dose" in text
    assert "For acute pain (pain_score >= 7), recommend rest and a professional look" in text
    opener = OPENER_SAFETY_FLOOR.text
    assert "no diagnosis, no condition, no dose, no health claim" in opener
    assert "never name HR zones" in opener


def test_every_live_prompt_renders_the_floor_verbatim():
    for prompt_id in COMPOSED_PROMPT_IDS:
        assert SAFETY_FLOOR.text in COMPOSED_PROMPT_VERSIONS[prompt_id], prompt_id
        assert OPENER_SAFETY_FLOOR.text in COMPOSED_OPENER_PROMPTS[prompt_id], prompt_id


# ---------------------------------------------------------------------------
# The clause vocabulary
# ---------------------------------------------------------------------------


def _all_clauses() -> list[Clause]:
    return [v for v in vars(clauses).values() if isinstance(v, Clause)]


def test_clause_names_are_unique():
    names = [clause.name for clause in _all_clauses()]
    assert len(names) == len(set(names))


def test_exactly_two_clauses_are_marked_as_the_floor():
    """One per mode. A third would mean a floor had been split, which is how a floor
    quietly loses half of itself."""
    floors = {clause.name for clause in _all_clauses() if clause.is_floor}
    assert floors == {"safety_floor", "opener_safety_floor"}


def test_every_clause_is_non_empty():
    for clause in _all_clauses():
        assert clause.text.strip(), clause.name


# ---------------------------------------------------------------------------
# What a version carries
# ---------------------------------------------------------------------------


def test_the_group_orientation_is_read_off_the_manifest():
    """The orientation tells the coach what each context group holds, so it must follow
    the capabilities that decide those contents rather than being chosen by hand."""
    for prompt_id in COMPOSED_PROMPT_IDS:
        features = features_for(prompt_id)
        carried = [c for c in fuller_clauses(prompt_id) if c.name.startswith("group_orientation")]
        assert len(carried) == 1, prompt_id
        if PromptFeature.INTENSITY_READ in features:
            expected = GROUP_ORIENTATION_V3
        elif PromptFeature.READINESS in features:
            expected = GROUP_ORIENTATION_V2
        else:
            expected = GROUP_ORIENTATION_V1
        assert carried[0] is expected, prompt_id


def test_the_body_clause_follows_the_body_capability():
    """The body clause describes the `profile.body` pack signal, so a version that is
    not served that signal is never told about it."""
    for prompt_id in COMPOSED_PROMPT_IDS:
        carries_clause = clauses.BODY in fuller_clauses(prompt_id)
        carries_signal = PromptFeature.BODY in features_for(prompt_id)
        assert carries_clause is carries_signal, prompt_id


def test_exactly_one_intervals_variant_per_version():
    for prompt_id in COMPOSED_PROMPT_IDS:
        carried = [c for c in fuller_clauses(prompt_id) if c.name.startswith("intervals_")]
        assert len(carried) == 1, prompt_id


def test_every_live_version_is_registered_and_served_the_grouped_pack():
    for prompt_id in COMPOSED_PROMPT_IDS:
        assert prompts.PROMPT_VERSIONS[prompt_id] == COMPOSED_PROMPT_VERSIONS[prompt_id]
        assert prompts._OPENER_PROMPTS[prompt_id] == COMPOSED_OPENER_PROMPTS[prompt_id]
        assert prompts.is_grouped_pack_prompt(prompt_id), prompt_id


def test_the_composed_prompts_use_grouped_dotted_paths():
    """`continuity.*` moves under `our_thread` in the grouped pack; a live prompt must
    never point the coach at the flat path."""
    for prompt_id in COMPOSED_PROMPT_IDS:
        text = COMPOSED_PROMPT_VERSIONS[prompt_id]
        assert "our_thread.continuity.opener_message" in text, prompt_id
        assert "`continuity.opener_message`" not in text, prompt_id


def test_clause_names_reports_the_set_in_order():
    names = clauses.clause_names("coach_message_lean_grouped_v8")
    assert names.index("identity") < names.index("safety_floor") < names.index("worked_examples")
    assert "body" in names
    assert clauses.clause_names("coach_message_lean_grouped_v8", mode="opener") == (
        "opener_identity",
        "opener_group_orientation",
        "opener_safety_floor",
        "opener_delivery",
    )
