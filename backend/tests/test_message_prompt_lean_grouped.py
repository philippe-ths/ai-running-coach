"""coach_message_lean_grouped_v1, and the later versions that isolate one prose change.

grouped_v1 is the disposition-first lean prose served the GROUPED pack: the same content
re-nested into the five coaching-question groups, so the prompt is that prose with a
group orientation and the two `continuity.*` paths re-anchored under `our_thread`.

The pin below is what makes the safety floor's survival checkable rather than asserted.
Since #803 the live lineage is composed from its own clause set, and the archived
`coach_message_lean_v1` string is an independent artifact, so comparing the two is a real
byte comparison: everything except the group orientation must still be lean_v1's prose,
character for character. Before #803 the same line restated the definition and could not
fail.
"""

from app.core.config import settings
from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import features_for
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER,
    _OPENER_PROMPTS,
    build_system_prompt,
    is_grouped_pack_prompt,
)
from app.services.coach.service import active_schema_version

GROUPED = "coach_message_lean_grouped_v1"
LEAN = "coach_message_lean_v1"
V14 = "coach_message_v14"


def regrouped_from_lean_v1(orientation: str) -> str:
    """lean_v1's frozen prose turned into a grouped prompt, derived here independently.

    The group orientation goes in ahead of the truth rule and the two `continuity.*`
    dotted paths move under `our_thread`. Nothing else may differ, which is the point:
    the identity, the truth rule, the misread numbers, THE LANE, the delivery protocol
    and the worked examples all have to come through byte for byte.
    """
    out = SYSTEM_PROMPT_MESSAGE_LEAN_V1.replace(
        "continuity.opener_message", "our_thread.continuity.opener_message"
    ).replace("continuity.reply", "our_thread.continuity.reply")
    anchor = "# The one rule about what is true"
    return out.replace(anchor, orientation + anchor, 1)


def regrouped_opener_from_lean_v1() -> str:
    anchor = "# What stays true, even here"
    return SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER.replace(
        anchor, clauses.OPENER_GROUP_ORIENTATION.text + anchor, 1
    )


def test_grouped_registered_in_message_family_and_flagged_grouped():
    assert GROUPED in PROMPT_VERSIONS
    assert GROUPED in _OPENER_PROMPTS
    assert GROUPED.startswith(MESSAGE_PROMPT_PREFIX)
    assert active_schema_version(GROUPED) == active_schema_version(LEAN)
    # The grouped-serving flag is on for GROUPED and off for the flat prompts.
    assert is_grouped_pack_prompt(GROUPED)
    assert not is_grouped_pack_prompt(LEAN)
    assert not is_grouped_pack_prompt(V14)
    assert not is_grouped_pack_prompt(None)


def test_grouped_has_full_capability_parity_with_lean_v1():
    """Same gated sections -> identical pack CONTENT. The A/B isolates the pack SHAPE
    (+ the orientation), exactly as lean_v1 isolates the prose vs v14.

    #800 moved the grouped-serialization flag out of a hand-maintained frozenset in
    prompts.py and into the manifest as `GROUPED_PACK`. It is the SHAPE this A/B
    isolates, so excluding it is what makes the parity claim say what it means: every
    CONTENT-bearing capability is identical."""
    from app.services.coach.prompt_features import PromptFeature as _F

    assert features_for(GROUPED) - {_F.GROUPED_PACK} == features_for(LEAN)
    assert _F.GROUPED_PACK in features_for(GROUPED)
    assert _F.GROUPED_PACK not in features_for(LEAN)


def test_grouped_v1_is_lean_v1_plus_orientation_and_continuity_repath_only():
    """The load-bearing byte pin: the composed clause set reproduces lean_v1's prose with
    only the orientation added and the continuity paths re-anchored, so no safety-critical
    line can have drifted while the lineage moved onto clauses."""
    assert PROMPT_VERSIONS[GROUPED] == regrouped_from_lean_v1(
        clauses.GROUP_ORIENTATION_V1.text
    )
    # Reverting the two navigational changes yields lean_v1 exactly.
    reverted = (
        PROMPT_VERSIONS[GROUPED]
        .replace(clauses.GROUP_ORIENTATION_V1.text, "")
        .replace("our_thread.continuity", "continuity")
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1


def test_grouped_v1_opener_is_lean_v1_opener_plus_orientation_only():
    assert _OPENER_PROMPTS[GROUPED] == regrouped_opener_from_lean_v1()
    reverted = _OPENER_PROMPTS[GROUPED].replace(
        clauses.OPENER_GROUP_ORIENTATION.text, ""
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER


def test_grouped_v1_carries_the_v1_orientation_clause():
    assert clauses.clause_names(GROUPED) == (
        "identity",
        "disposition",
        "group_orientation_v1",
        "truth_rule",
        "misread_numbers",
        "intervals_this_run",
        "perceived_effort",
        "safety_floor",
        "delivery",
        "continuity",
        "worked_examples",
    )


def test_grouped_orientation_names_the_five_coaching_question_groups():
    text = PROMPT_VERSIONS[GROUPED]
    assert "How your context is organized" in text
    for group in ("this_run", "right_now", "the_runner", "our_thread", "how_to_coach"):
        assert f"`{group}`" in text
    # continuity is re-anchored under our_thread; the bare flat path is gone.
    assert "our_thread.continuity.opener_message" in text
    assert "our_thread.continuity.reply" in text


def test_grouped_keeps_the_load_bearing_safety_surface_verbatim():
    """Defense in depth over the derivation pin: the safety lane, the misread-number
    facts, and the truth rule appear verbatim in the grouped prompt."""
    lean = SYSTEM_PROMPT_MESSAGE_LEAN_V1
    grouped = PROMPT_VERSIONS[GROUPED]
    for anchor in (
        "Stay in general-wellness coaching.",
        "`effort_score` is cumulative training LOAD",
        "`discount_signals` is authoritative.",
        "When `zones_calibrated` is false",
        "For acute pain (pain_score >= 7)",
    ):
        assert anchor in lean and anchor in grouped, anchor


def test_grouped_builds_for_both_modes():
    fuller = build_system_prompt(GROUPED, mode="fuller", voice=None)
    opener = build_system_prompt(GROUPED, mode="opener", voice=None)
    assert PROMPT_VERSIONS[GROUPED] in fuller
    assert _OPENER_PROMPTS[GROUPED] in opener
    assert "YOUR VOICE FOR THIS RUNNER" in fuller  # voice-aware, like lean_v1


def test_grouped_ships_inert():
    assert settings.COACH_PROMPT_ID != GROUPED


def test_message_prompt_lean_grouped_v6_extends_laps_rule_to_past_sessions():
    """#712: grouped_v6 = grouped_v5's prose with the recorded-laps discipline extended
    to a past session pulled from recent_weeks. It differs from grouped_v5 ONLY by taking
    the other intervals clause; the opener is byte-identical; it serves the grouped pack."""
    v5 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v5"]
    v6 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v6"]
    assert v6 != v5
    assert (
        v6.replace(clauses.INTERVALS_ANY_SESSION.text, clauses.INTERVALS_THIS_RUN.text)
        == v5
    )
    assert "an earlier one you're revisiting" in v6
    assert "an earlier one you're revisiting" not in v5
    # the clause set says the same thing, and says which clause carries the change.
    assert "intervals_any_session" in clauses.clause_names("coach_message_lean_grouped_v6")
    assert "intervals_this_run" in clauses.clause_names("coach_message_lean_grouped_v5")
    # opener byte-identical (the opener carries no lap advice).
    assert (
        prompts._OPENER_PROMPTS["coach_message_lean_grouped_v6"]
        == prompts._OPENER_PROMPTS["coach_message_lean_grouped_v5"]
    )
    assert "coach_message_lean_grouped_v6" in prompts.GROUPED_PACK_PROMPT_IDS


def test_message_prompt_lean_grouped_v7_adds_personalisation_bullet():
    """grouped_v7 = grouped_v5's prose + the "coach this runner, not the median"
    personalisation bullet. It differs from grouped_v5 ONLY by that one clause, so
    reverting it reproduces grouped_v5 BYTE-FOR-BYTE and the safety floor is invariant by
    construction. A sibling of grouped_v6 (each isolates one change off grouped_v5), so a
    grouped_v5 -> grouped_v7 flip is a pure A/B on personalisation."""
    v5 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v5"]
    v7 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v7"]
    assert v7 != v5
    assert v7.replace(clauses.PERSONALISATION.text, "") == v5
    assert "not the average one" in v7
    assert "not the average one" not in v5
    assert "personalisation" in clauses.clause_names("coach_message_lean_grouped_v7")
    assert "personalisation" not in clauses.clause_names("coach_message_lean_grouped_v5")
    # the disposition is fuller-only; the opener carries no advice, so it is byte-identical.
    assert (
        prompts._OPENER_PROMPTS["coach_message_lean_grouped_v7"]
        == prompts._OPENER_PROMPTS["coach_message_lean_grouped_v5"]
    )
    assert "coach_message_lean_grouped_v7" in prompts.GROUPED_PACK_PROMPT_IDS
    assert features_for("coach_message_lean_grouped_v7") == features_for(
        "coach_message_lean_grouped_v5"
    )
    assert settings.COACH_PROMPT_ID != "coach_message_lean_grouped_v7"  # ships inert


def test_message_prompt_lean_grouped_v8_adds_the_body_clause():
    """#742: grouped_v8 = grouped_v7's prose + the body clause, which it gets by carrying
    PromptFeature.BODY. Reverting the clause reproduces grouped_v7 byte-for-byte, and the
    clause is present exactly because the version is served `profile.body`."""
    from app.services.coach.prompt_features import PromptFeature as _F

    v7 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v7"]
    v8 = prompts.PROMPT_VERSIONS["coach_message_lean_grouped_v8"]
    assert v8 != v7
    assert v8.replace(clauses.BODY.text, "") == v7
    assert "body" in clauses.clause_names("coach_message_lean_grouped_v8")
    assert "body" not in clauses.clause_names("coach_message_lean_grouped_v7")
    assert _F.BODY in features_for("coach_message_lean_grouped_v8")
    assert _F.BODY not in features_for("coach_message_lean_grouped_v7")
    assert (
        prompts._OPENER_PROMPTS["coach_message_lean_grouped_v8"]
        == prompts._OPENER_PROMPTS["coach_message_lean_grouped_v5"]
    )
    assert settings.COACH_PROMPT_ID != "coach_message_lean_grouped_v8"  # ships inert
