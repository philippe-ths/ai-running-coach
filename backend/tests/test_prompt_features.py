"""Characterization tests for the prompt-feature manifest (#328).

The manifest (`prompt_features.PROMPT_FEATURES`) is the single source of truth for
which capabilities each coach prompt id carries; the `*_PROMPT_IDS` frozensets and
`is_*_prompt` predicates in `prompts.py` are derived views over it. These tests pin
the per-id capability oracle captured from `main` before the refactor, so the
refactor is provably behaviour-preserving and a future manifest edit that changes a
prompt's capabilities is caught.
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

# The capability oracle, captured from `main` before the refactor: each coach_message
# version adds exactly one capability to the prior version's set. Every id NOT listed
# here (legacy coach_report_*, coach_message_v1, None, unknown) carries no capability.
EXPECTED_CAPABILITIES = {
    "coach_message_v2": {F.TWO_STAGE},
    "coach_message_v3": {F.TWO_STAGE, F.VOICE},
    "coach_message_v4": {F.TWO_STAGE, F.VOICE, F.CORPUS},
    "coach_message_v5": {F.TWO_STAGE, F.VOICE, F.CORPUS, F.STANCE},
    "coach_message_v6": {F.TWO_STAGE, F.VOICE, F.CORPUS, F.STANCE, F.TRAINING_LOAD},
    "coach_message_v7": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
    },
    # #266 — v8 carries exactly v7's capabilities (a philosophy retune, not a new feature).
    "coach_message_v8": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
    },
    # #400 — v9 = v8 + the VOLUME capability.
    "coach_message_v9": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
    },
    # #443 — v10 = v9 + the STREAM_VIEW capability.
    "coach_message_v10": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
    },
    # #444 — v11 = v10 + the RECENT_TRAINING capability.
    "coach_message_v11": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
    },
    # #561 — v12 = v11 + the TRAINING_HISTORY capability.
    "coach_message_v12": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
        F.TRAINING_HISTORY,
    },
    # ADR 0025 — v13 = v12 + the MEMORY capability.
    "coach_message_v13": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
        F.TRAINING_HISTORY,
        F.MEMORY,
    },
    # #578 — v14 = v13 + the INTENSITY capability.
    "coach_message_v14": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
        F.TRAINING_HISTORY,
        F.MEMORY,
        F.INTENSITY,
    },
    # EXPERIMENT — coach_message_lean_v1 carries the SAME full capability set as v14 by
    # design (prompt-feature parity => identical context pack), so the disposition-first
    # prose rewrite is a clean A/B on system-prompt text alone.
    "coach_message_lean_v1": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
        F.TRAINING_HISTORY,
        F.MEMORY,
        F.INTENSITY,
    },
    # ADR 0026 Slice 1 — the grouped-pack variant of lean_v1: full capability parity
    # (same pack CONTENT), differing only in the pack SHAPE it is served.
    "coach_message_lean_grouped_v1": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.TRAINING_LOAD,
        F.USER_MATERIALS,
        F.VOLUME,
        F.STREAM_VIEW,
        F.RECENT_TRAINING,
        F.TRAINING_HISTORY,
        F.MEMORY,
        F.INTENSITY,
    },
    # ADR 0026 Slice 2 (#670) — grouped_v2 redefines the `right_now` content: it REPLACES
    # TRAINING_LOAD -> READINESS and VOLUME + RECENT_TRAINING -> RECENT_WEEKS. PR 2 also
    # REBASES the_runner.training_history: TRAINING_HISTORY -> TRAINING_HISTORY_2WK (the same
    # section, ladder rebased after the 2-week recent_weeks window + enriched). It keeps
    # every other grouped_v1 capability, so it is deliberately NOT a superset of grouped_v1
    # (the overlap swamps + the training-history boundary move); it carries 11 features.
    "coach_message_lean_grouped_v2": {
        F.TWO_STAGE,
        F.VOICE,
        F.CORPUS,
        F.STANCE,
        F.READINESS,
        F.USER_MATERIALS,
        F.RECENT_WEEKS,
        F.STREAM_VIEW,
        F.TRAINING_HISTORY_2WK,
        F.MEMORY,
        F.INTENSITY,
    },
}

# Ids that must carry NO capabilities (the inert-under-rollback set).
NON_CAPABILITY_IDS = [
    "coach_message_v1",
    "coach_report_v1",
    "coach_report_v10",
    None,
    "unknown_prompt_xyz",
]


def test_manifest_matches_captured_oracle():
    """Each capability-bearing prompt id carries exactly its captured feature set."""
    assert {pid: set(feats) for pid, feats in PROMPT_FEATURES.items()} == EXPECTED_CAPABILITIES


def test_legacy_and_unknown_ids_carry_no_capabilities():
    for pid in NON_CAPABILITY_IDS:
        for feature in PromptFeature:
            assert not has_feature(pid, feature), (pid, feature)


def test_derived_sets_equal_manifest_views():
    """The `*_PROMPT_IDS` frozensets are exactly the manifest's derived views."""
    assert prompts.TWO_STAGE_PROMPT_IDS == ids_with(F.TWO_STAGE)
    assert prompts.VOICE_PROMPT_IDS == ids_with(F.VOICE)
    assert prompts.CORPUS_PROMPT_IDS == ids_with(F.CORPUS)
    assert prompts.STANCE_PROMPT_IDS == ids_with(F.STANCE)
    assert prompts.TRAINING_LOAD_PROMPT_IDS == ids_with(F.TRAINING_LOAD)
    assert prompts.USER_MATERIALS_PROMPT_IDS == ids_with(F.USER_MATERIALS)
    assert prompts.VOLUME_PROMPT_IDS == ids_with(F.VOLUME)
    assert prompts.STREAM_VIEW_PROMPT_IDS == ids_with(F.STREAM_VIEW)
    assert prompts.RECENT_TRAINING_PROMPT_IDS == ids_with(F.RECENT_TRAINING)
    assert prompts.TRAINING_HISTORY_PROMPT_IDS == ids_with(F.TRAINING_HISTORY)
    assert prompts.TRAINING_HISTORY_2WK_PROMPT_IDS == ids_with(F.TRAINING_HISTORY_2WK)
    assert prompts.MEMORY_PROMPT_IDS == ids_with(F.MEMORY)


def test_memory_feature_is_active_on_v13():
    """ADR 0025: M3 registers coach_message_v13 carrying the MEMORY capability, so the
    `memory` pack section + addendum + the background writer activate together on the
    v13 flip. (M2 shipped the feature inert; this assertion was flipped here.) v12 and
    below stay memory-free, so the rollback is a pure config flip. (v14+ are additive
    supersets and carry MEMORY too, #578.)"""
    assert prompts.MEMORY_PROMPT_IDS == {
        "coach_message_v13", "coach_message_v14", "coach_message_lean_v1",
        "coach_message_lean_grouped_v1", "coach_message_lean_grouped_v2",
    }
    assert prompts.is_memory_prompt("coach_message_v13") is True
    assert prompts.is_memory_prompt("coach_message_v12") is False


def test_derived_sets_match_captured_membership():
    """Belt-and-suspenders: the derived sets equal the explicit captured membership."""
    # coach_message_lean_v1 (the experiment) and coach_message_lean_grouped_v1 (ADR 0026
    # Slice 1) both carry the FULL capability set, so both join every derived set below.
    # coach_message_lean_grouped_v2 (Slice 2, #670) joins only the ADDITIVE sets — it
    # REPLACES training_load/volume/recent_training with readiness/recent_weeks, so it is
    # absent from those three sets and is the sole member of the two new ones.
    LEAN = "coach_message_lean_v1"
    GROUPED = "coach_message_lean_grouped_v1"
    GROUPED2 = "coach_message_lean_grouped_v2"
    assert prompts.TWO_STAGE_PROMPT_IDS == {
        "coach_message_v2", "coach_message_v3", "coach_message_v4",
        "coach_message_v5", "coach_message_v6", "coach_message_v7", "coach_message_v8",
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2,
    }
    assert prompts.VOICE_PROMPT_IDS == {
        "coach_message_v3", "coach_message_v4", "coach_message_v5",
        "coach_message_v6", "coach_message_v7", "coach_message_v8", "coach_message_v9",
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED, GROUPED2,
    }
    assert prompts.CORPUS_PROMPT_IDS == {
        "coach_message_v4", "coach_message_v5", "coach_message_v6",
        "coach_message_v7", "coach_message_v8", "coach_message_v9", "coach_message_v10",
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED, GROUPED2,
    }
    assert prompts.STANCE_PROMPT_IDS == {
        "coach_message_v5", "coach_message_v6", "coach_message_v7", "coach_message_v8",
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2,
    }
    # Slice 2: grouped_v2 REPLACES training_load with readiness, so it is NOT here.
    assert prompts.TRAINING_LOAD_PROMPT_IDS == {
        "coach_message_v6", "coach_message_v7", "coach_message_v8", "coach_message_v9",
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.USER_MATERIALS_PROMPT_IDS == {
        "coach_message_v7", "coach_message_v8", "coach_message_v9", "coach_message_v10",
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED, GROUPED2,
    }
    # Slice 2: grouped_v2 REPLACES volume + recent_training with recent_weeks, so it is
    # absent from both of these sets.
    assert prompts.VOLUME_PROMPT_IDS == {
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.STREAM_VIEW_PROMPT_IDS == {
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED, GROUPED2,
    }
    assert prompts.RECENT_TRAINING_PROMPT_IDS == {
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED,
    }
    # Slice 2 PR 2: grouped_v2 REBASES training_history (carries TRAINING_HISTORY_2WK
    # instead), so it drops OUT of the original-ladder set and is the sole member of the new.
    assert prompts.TRAINING_HISTORY_PROMPT_IDS == {
        "coach_message_v12", "coach_message_v13", "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.TRAINING_HISTORY_2WK_PROMPT_IDS == {GROUPED2}
    assert prompts.MEMORY_PROMPT_IDS == {
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2,
    }
    assert prompts.INTENSITY_PROMPT_IDS == {"coach_message_v14", LEAN, GROUPED, GROUPED2}
    # Slice 2's two new sets: grouped_v2 is their sole member.
    assert prompts.READINESS_PROMPT_IDS == {GROUPED2}
    assert prompts.RECENT_WEEKS_PROMPT_IDS == {GROUPED2}


def test_predicates_agree_with_has_feature():
    """The is_*_prompt predicates are exactly their has_feature derivation, for every
    registered prompt id plus the non-capability ids."""
    for pid in list(prompts.PROMPT_VERSIONS.keys()) + NON_CAPABILITY_IDS:
        assert prompts.is_corpus_prompt(pid) == has_feature(pid, F.CORPUS)
        assert prompts.is_stance_prompt(pid) == has_feature(pid, F.STANCE)
        assert prompts.is_training_load_prompt(pid) == has_feature(pid, F.TRAINING_LOAD)
        assert prompts.is_user_materials_prompt(pid) == has_feature(pid, F.USER_MATERIALS)
        assert prompts.is_volume_prompt(pid) == has_feature(pid, F.VOLUME)
        assert prompts.is_stream_view_prompt(pid) == has_feature(pid, F.STREAM_VIEW)
        assert prompts.is_recent_training_prompt(pid) == has_feature(pid, F.RECENT_TRAINING)
        assert prompts.is_training_history_prompt(pid) == has_feature(pid, F.TRAINING_HISTORY)
        assert prompts.is_training_history_2wk_prompt(pid) == has_feature(pid, F.TRAINING_HISTORY_2WK)
        assert prompts.is_memory_prompt(pid) == has_feature(pid, F.MEMORY)


def test_opener_prompts_cover_exactly_two_stage_ids():
    """Every two-stage prompt has a distinct opener form, and nothing else does."""
    assert set(prompts._OPENER_PROMPTS.keys()) == set(prompts.TWO_STAGE_PROMPT_IDS)


def test_fullest_message_prompt_is_the_max_capability_id():
    """The structural pack guards derive their prompt from this, so it must be the
    coach_message id with the most capabilities (every gated section on) and must
    advance automatically as new versions are added — never a stale hardcode."""
    fullest = fullest_message_prompt_id()
    max_n = max(len(f) for f in PROMPT_FEATURES.values())
    assert len(PROMPT_FEATURES[fullest]) == max_n
    assert fullest.startswith("coach_message")
    # It carries every ADDITIVE feature any prompt carries, so no gated section is missed
    # by a guard built under it. ADR 0026 Slice 2 (#670) introduces MUTUALLY-EXCLUSIVE
    # alternatives — READINESS/RECENT_WEEKS REPLACE TRAINING_LOAD/VOLUME/RECENT_TRAINING
    # under the grouped_v2 prompt — so no single prompt carries the full union; the
    # fullest carries the pre-Slice-2 (larger) side of each swap. The redefined sections
    # are guarded by their own Slice-2 tests. PR 2 adds TRAINING_HISTORY_2WK as a third
    # alternative (grouped_v2 carries it INSTEAD of TRAINING_HISTORY).
    ALTERNATIVE_FEATURES = {F.READINESS, F.RECENT_WEEKS, F.TRAINING_HISTORY_2WK}
    every_feature = set().union(*PROMPT_FEATURES.values())
    assert set(PROMPT_FEATURES[fullest]) == every_feature - ALTERNATIVE_FEATURES


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
