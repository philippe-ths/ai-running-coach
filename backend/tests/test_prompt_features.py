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


def test_derived_sets_match_captured_membership():
    """Belt-and-suspenders: the derived sets equal the explicit captured membership."""
    assert prompts.TWO_STAGE_PROMPT_IDS == {
        "coach_message_v2", "coach_message_v3", "coach_message_v4",
        "coach_message_v5", "coach_message_v6", "coach_message_v7",
    }
    assert prompts.VOICE_PROMPT_IDS == {
        "coach_message_v3", "coach_message_v4", "coach_message_v5",
        "coach_message_v6", "coach_message_v7",
    }
    assert prompts.CORPUS_PROMPT_IDS == {
        "coach_message_v4", "coach_message_v5", "coach_message_v6", "coach_message_v7",
    }
    assert prompts.STANCE_PROMPT_IDS == {
        "coach_message_v5", "coach_message_v6", "coach_message_v7",
    }
    assert prompts.TRAINING_LOAD_PROMPT_IDS == {"coach_message_v6", "coach_message_v7"}
    assert prompts.USER_MATERIALS_PROMPT_IDS == {"coach_message_v7"}


def test_predicates_agree_with_has_feature():
    """The is_*_prompt predicates are exactly their has_feature derivation, for every
    registered prompt id plus the non-capability ids."""
    for pid in list(prompts.PROMPT_VERSIONS.keys()) + NON_CAPABILITY_IDS:
        assert prompts.is_corpus_prompt(pid) == has_feature(pid, F.CORPUS)
        assert prompts.is_stance_prompt(pid) == has_feature(pid, F.STANCE)
        assert prompts.is_training_load_prompt(pid) == has_feature(pid, F.TRAINING_LOAD)
        assert prompts.is_user_materials_prompt(pid) == has_feature(pid, F.USER_MATERIALS)


def test_opener_prompts_cover_exactly_two_stage_ids():
    """Every two-stage prompt has a distinct opener form, and nothing else does."""
    assert set(prompts._OPENER_PROMPTS.keys()) == set(prompts.TWO_STAGE_PROMPT_IDS)


def test_manifest_ids_are_registered_prompts():
    """No capability is declared for a prompt id that does not exist."""
    assert set(PROMPT_FEATURES.keys()) <= set(prompts.PROMPT_VERSIONS.keys())


def test_message_version_capabilities_are_monotonic():
    """Each coach_message_vN is a superset of vN-1: the family only ever adds."""
    ordered = [
        "coach_message_v2", "coach_message_v3", "coach_message_v4",
        "coach_message_v5", "coach_message_v6", "coach_message_v7",
    ]
    for earlier, later in zip(ordered, ordered[1:]):
        assert PROMPT_FEATURES[earlier] < PROMPT_FEATURES[later], (earlier, later)
