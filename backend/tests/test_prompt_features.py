"""Structural guards on the prompt-feature manifest (#328).

The manifest (`prompt_features.PROMPT_FEATURES`) is the single source of truth for which
capabilities each coach prompt id carries; the `*_PROMPT_IDS` frozensets and
`is_*_prompt` predicates in `prompts.py` are derived views over it.

These tests assert PROPERTIES of the manifest, never a copy of it. A copy is what this
file used to hold, and it cost what duplication always costs: the mirror had to be edited
in lockstep with every new version, and the guard that was supposed to check the derived
views had silently fallen seven features behind the manifest it checked. Each version's
own capability delta is asserted in that version's own test file, so adding a version
touches no test but its own (#803).
"""

from app.services.coach import prompts
from app.services.coach.prompt_features import (
    PROMPT_FEATURES,
    PromptFeature,
    fullest_message_prompt_id,
    has_feature,
    ids_with,
)

F = PromptFeature

# Ids that must carry NO capabilities (the inert-under-rollback set).
NON_CAPABILITY_IDS = [
    "coach_message_v1",
    "coach_report_v1",
    "coach_report_v10",
    None,
    "unknown_prompt_xyz",
]


def test_legacy_and_unknown_ids_carry_no_capabilities():
    for pid in NON_CAPABILITY_IDS:
        for feature in PromptFeature:
            assert not has_feature(pid, feature), (pid, feature)


def test_every_feature_has_a_derived_view_equal_to_its_manifest_slice():
    """Walked over `PromptFeature` rather than listed, which is the whole point: the
    listed version of this guard was checking 15 of the 22 features and nobody could see
    which 7 it had stopped covering. A new feature without a view now fails here."""
    for feature in PromptFeature:
        view_name = f"{feature.name}_PROMPT_IDS"
        assert hasattr(prompts, view_name), view_name
        assert getattr(prompts, view_name) == ids_with(feature), view_name


# The two features with no `is_*_prompt` predicate, and why. TWO_STAGE's predicate is
# `service.is_two_stage_prompt` (it gates the cadence, not a pack section); VOICE is
# consulted through `render_voice_block`, which returns "" for a non-voice prompt. Named
# so that a THIRD feature quietly shipping without a predicate is caught.
FEATURES_WITHOUT_A_PREDICATE = {F.TWO_STAGE, F.VOICE}


def test_every_other_feature_has_a_predicate_equal_to_has_feature():
    """The `is_*_prompt` predicates are exactly their `has_feature` derivation, for every
    registered prompt id plus the non-capability ids."""
    checked = set()
    for feature in PromptFeature:
        predicate = getattr(prompts, f"is_{feature.name.lower()}_prompt", None)
        if feature in FEATURES_WITHOUT_A_PREDICATE:
            assert predicate is None, feature
            continue
        assert predicate is not None, feature
        for pid in list(prompts.PROMPT_VERSIONS.keys()) + NON_CAPABILITY_IDS:
            assert predicate(pid) == has_feature(pid, feature), (feature, pid)
        checked.add(feature)
    assert checked == set(PromptFeature) - FEATURES_WITHOUT_A_PREDICATE


def test_every_feature_is_carried_by_at_least_one_prompt():
    """A feature no prompt carries is a gate that can never open: either a version was
    dropped or the capability was declared and never wired."""
    for feature in PromptFeature:
        assert ids_with(feature), feature


def test_memory_feature_is_active_on_v13():
    """ADR 0025: M3 registers coach_message_v13 carrying the MEMORY capability, so the
    `memory` pack section + addendum + the background writer activate together on the
    v13 flip. v12 and below stay memory-free, so the rollback is a pure config flip."""
    assert prompts.is_memory_prompt("coach_message_v13") is True
    assert prompts.is_memory_prompt("coach_message_v12") is False


def test_opener_prompts_cover_exactly_two_stage_ids():
    """Every two-stage prompt has a distinct opener form, and nothing else does."""
    assert set(prompts._OPENER_PROMPTS.keys()) == set(prompts.TWO_STAGE_PROMPT_IDS)


def test_fullest_message_prompt_is_the_max_capability_id():
    """The structural pack guards derive their prompt from this, so it must be the
    coach_message id with the most capabilities (every gated section on) and must
    advance automatically as new versions are added — never a stale hardcode."""
    fullest = fullest_message_prompt_id()
    assert fullest.startswith("coach_message")
    # It carries every ADDITIVE feature any prompt carries, so no gated section is missed
    # by a guard built under it. ADR 0026 Slice 2 (#670) introduces MUTUALLY-EXCLUSIVE
    # alternatives — READINESS/RECENT_WEEKS REPLACE TRAINING_LOAD/VOLUME/RECENT_TRAINING
    # under the grouped_v2 prompt — so no single prompt carries the full union; the
    # fullest carries the pre-Slice-2 (larger) side of each swap. The redefined sections
    # are guarded by their own Slice-2 tests. PR 2 adds TRAINING_HISTORY_2WK as a third
    # alternative (grouped_v2 carries it INSTEAD of TRAINING_HISTORY).
    # Slice 3 (#673) adds INTENSITY_READ + INTENSITY_MIX as two more alternatives (they
    # REPLACE perceived_effort/calibration/intensity under the grouped_v3 prompt).
    # Slice 4 (#680) adds METRICS_COACH_FRAMED, a presentation-only view flag (no section),
    # so grouped_v4 carries the max RAW count yet is NOT the additive-fullest — the ranking
    # is by ADDITIVE features, so `fullest` stays grouped_v1. Slice 5 (#682) adds
    # SALIENCE_DROPPED, a view-only section REMOVAL (also non-additive), likewise excluded.
    ALTERNATIVE_FEATURES = {
        F.READINESS, F.RECENT_WEEKS, F.TRAINING_HISTORY_2WK,
        F.INTENSITY_READ, F.INTENSITY_MIX, F.METRICS_COACH_FRAMED, F.SALIENCE_DROPPED,
        F.PACK_COACH_VIEW,
        # #800 relocated the grouped-serialization flag into the manifest; like the three
        # view flags above it is presentation-only (the same sections, re-nested), so it
        # is non-additive.
        F.GROUPED_PACK,
    }
    # A third category (#742): ADDITIVE (it adds a pack section, so it is not an
    # alternative) but carried only on the GROUPED lineage, which swapped five original
    # additive features for alternatives and so can never win the additive ranking. The
    # consequence is real and must not be waved through: a section here is NOT covered by
    # the structural guards that build their pack under `fullest`, so each one owes a
    # named guard of its own. `profile.body` -> tests/test_body_pack_section.py.
    GROUPED_ONLY_ADDITIVE = {F.BODY}
    max_additive = max(len(set(f) - ALTERNATIVE_FEATURES) for f in PROMPT_FEATURES.values())
    assert len(set(PROMPT_FEATURES[fullest]) - ALTERNATIVE_FEATURES) == max_additive
    every_feature = set().union(*PROMPT_FEATURES.values())
    # Compared ADDITIVE-side only, which is what the claim actually is: `fullest` carries
    # every additive feature any prompt carries. It may itself carry non-additive flags —
    # since #800 relocated GROUPED_PACK into the manifest, `fullest` (a grouped id) does.
    assert set(PROMPT_FEATURES[fullest]) - ALTERNATIVE_FEATURES == (
        every_feature - ALTERNATIVE_FEATURES - GROUPED_ONLY_ADDITIVE
    )
    # ...and pin exactly WHICH non-additive flags it carries, so that relaxation cannot
    # quietly widen. Before #800 `fullest` carried none and strict equality above said
    # this implicitly; GROUPED_PACK is the one and only flag it may now carry, and a
    # `fullest` that picked up (say) SALIENCE_DROPPED would be a real change of what the
    # structural pack guards are built on top of.
    assert set(PROMPT_FEATURES[fullest]) & ALTERNATIVE_FEATURES == {F.GROUPED_PACK}
    # Every grouped-only additive feature really is absent from `fullest` — otherwise the
    # exemption above is stale and silently widening what the guards skip.
    assert GROUPED_ONLY_ADDITIVE.isdisjoint(PROMPT_FEATURES[fullest])


def test_manifest_ids_are_registered_prompts():
    """No capability is declared for a prompt id that does not exist."""
    assert set(PROMPT_FEATURES.keys()) <= set(prompts.PROMPT_VERSIONS.keys())


def test_message_version_capabilities_are_monotonic():
    """Each coach_message_vN up to v7 is a strict superset of vN-1: the family added one
    capability per version. v8 (#266) is the first to add NONE — it carries exactly
    v7's feature set (a philosophy/tone retune, not a capability), so the strict chain
    stops at v7 and v8 equals it."""
    ordered = [
        "coach_message_v2", "coach_message_v3", "coach_message_v4",
        "coach_message_v5", "coach_message_v6", "coach_message_v7",
    ]
    for earlier, later in zip(ordered, ordered[1:]):
        assert PROMPT_FEATURES[earlier] < PROMPT_FEATURES[later], (earlier, later)
    # v8 adds no capability: exactly v7's feature set.
    assert PROMPT_FEATURES["coach_message_v8"] == PROMPT_FEATURES["coach_message_v7"]
    # #400 — v9 resumes the chain: a strict superset of v8 (it adds VOLUME).
    assert PROMPT_FEATURES["coach_message_v9"] > PROMPT_FEATURES["coach_message_v8"]
    # #443 — v10 = v9 + STREAM_VIEW: a strict superset of v9.
    assert PROMPT_FEATURES["coach_message_v10"] > PROMPT_FEATURES["coach_message_v9"]
    # #444 — v11 = v10 + RECENT_TRAINING: a strict superset of v10.
    assert PROMPT_FEATURES["coach_message_v11"] > PROMPT_FEATURES["coach_message_v10"]
    # #561 — v12 = v11 + TRAINING_HISTORY: a strict superset of v11.
    assert PROMPT_FEATURES["coach_message_v12"] > PROMPT_FEATURES["coach_message_v11"]
    # ADR 0025 — v13 = v12 + MEMORY: a strict superset of v12.
    assert PROMPT_FEATURES["coach_message_v13"] > PROMPT_FEATURES["coach_message_v12"]
    # #578 — v14 = v13 + INTENSITY: a strict superset of v13.
    assert PROMPT_FEATURES["coach_message_v14"] > PROMPT_FEATURES["coach_message_v13"]
