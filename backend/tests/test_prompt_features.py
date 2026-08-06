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
        F.GROUPED_PACK,
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
        F.GROUPED_PACK,
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
    # ADR 0026 Slice 3 (#673) — grouped_v3 collapses the four this-run intensity lenses:
    # it REPLACES INTENSITY -> INTENSITY_READ (this-run merge, retiring perceived_effort/
    # calibration) + INTENSITY_MIX (recent half). It keeps every other grouped_v2
    # capability, so it is an alternative shape (not a superset); it carries 12 features.
    "coach_message_lean_grouped_v3": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
    },
    # ADR 0026 Slice 4 (#680) — grouped_v4 = grouped_v3 + METRICS_COACH_FRAMED, a
    # presentation-only leaf reframing of the outgoing LLM pack view (adds no section). It
    # keeps every grouped_v3 capability; it carries 13 features.
    "coach_message_lean_grouped_v4": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
        F.METRICS_COACH_FRAMED,
    },
    # ADR 0026 Slice 5 (#682) — grouped_v5 = grouped_v4 + SALIENCE_DROPPED (a view-only
    # section removal), the flip target. It carries 14 features.
    "coach_message_lean_grouped_v5": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
        F.METRICS_COACH_FRAMED,
        F.SALIENCE_DROPPED,
        F.PACK_COACH_VIEW,
    },
    # #712 — grouped_v6 = grouped_v5 + a past-session recorded-laps prose clause. NO new
    # capability (identical feature set to grouped_v5, so its pack is byte-identical);
    # only the fuller system-prompt TEXT differs. Ships INERT (flip target: v5 -> v6).
    "coach_message_lean_grouped_v6": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
        F.METRICS_COACH_FRAMED,
        F.SALIENCE_DROPPED,
        F.PACK_COACH_VIEW,
    },
    # grouped_v7 = grouped_v5 + the "coach this runner, not the median" personalisation prose
    # bullet. NO new capability (identical feature set to grouped_v5/v6, so its pack is
    # byte-identical); only the fuller system-prompt TEXT differs. Ships INERT (v5 -> v7).
    "coach_message_lean_grouped_v7": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
        F.METRICS_COACH_FRAMED,
        F.SALIENCE_DROPPED,
        F.PACK_COACH_VIEW,
    },
    # #742: grouped_v8 = grouped_v7 + BODY. The first grouped version since v5 to ADD a
    # capability rather than isolate a prose change, so its pack is NOT byte-identical to
    # grouped_v5's -- it gains the nested `profile.body` signal. Ships INERT (v7 -> v8).
    "coach_message_lean_grouped_v8": {
        F.GROUPED_PACK,
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
        F.INTENSITY_READ,
        F.INTENSITY_MIX,
        F.METRICS_COACH_FRAMED,
        F.SALIENCE_DROPPED,
        F.PACK_COACH_VIEW,
        F.BODY,
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
    assert prompts.METRICS_COACH_FRAMED_PROMPT_IDS == ids_with(F.METRICS_COACH_FRAMED)
    assert prompts.SALIENCE_DROPPED_PROMPT_IDS == ids_with(F.SALIENCE_DROPPED)
    assert prompts.PACK_COACH_VIEW_PROMPT_IDS == ids_with(F.PACK_COACH_VIEW)


def test_memory_feature_is_active_on_v13():
    """ADR 0025: M3 registers coach_message_v13 carrying the MEMORY capability, so the
    `memory` pack section + addendum + the background writer activate together on the
    v13 flip. (M2 shipped the feature inert; this assertion was flipped here.) v12 and
    below stay memory-free, so the rollback is a pure config flip. (v14+ are additive
    supersets and carry MEMORY too, #578.)"""
    assert prompts.MEMORY_PROMPT_IDS == {
        "coach_message_v13", "coach_message_v14", "coach_message_lean_v1",
        "coach_message_lean_grouped_v1", "coach_message_lean_grouped_v2",
        "coach_message_lean_grouped_v3", "coach_message_lean_grouped_v4",
        "coach_message_lean_grouped_v5", "coach_message_lean_grouped_v6",
        "coach_message_lean_grouped_v7", "coach_message_lean_grouped_v8",
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
    # ADR 0026 Slice 3 (#673): grouped_v3 keeps every grouped_v2 capability except it
    # REPLACES INTENSITY with INTENSITY_READ + INTENSITY_MIX, so it joins the shared
    # additive sets (two-stage/voice/corpus/stance/user-materials/stream-view/memory) and
    # the Slice-2 alternative sets (readiness/recent_weeks/training_history_2wk), and is
    # the sole member of the two new Slice-3 sets — while dropping OUT of INTENSITY.
    GROUPED3 = "coach_message_lean_grouped_v3"
    # ADR 0026 Slice 4 (#680): grouped_v4 keeps every grouped_v3 capability and adds
    # METRICS_COACH_FRAMED (a presentation-only view flag), so it joins exactly the same
    # derived sets as grouped_v3 (additive shared sets + the Slice-2/3 alternative sets).
    GROUPED4 = "coach_message_lean_grouped_v4"
    # ADR 0026 Slice 5 (#682): grouped_v5 keeps every grouped_v4 capability and adds
    # SALIENCE_DROPPED (a view-only section removal), so it joins exactly the same derived
    # sets as grouped_v4 and is the sole member of the new SALIENCE_DROPPED set.
    GROUPED5 = "coach_message_lean_grouped_v5"
    # #712: grouped_v6 = grouped_v5 + a fuller-prose laps clause; SAME feature set as
    # grouped_v5, so it joins exactly the same derived sets grouped_v5 belongs to.
    GROUPED6 = "coach_message_lean_grouped_v6"
    # grouped_v7 = grouped_v5 + the personalisation prose bullet; SAME feature set as
    # grouped_v5/v6, so it joins exactly the same derived sets they belong to.
    GROUPED7 = "coach_message_lean_grouped_v7"
    # #742: grouped_v8 = grouped_v7 + BODY. It joins every derived set grouped_v7 belongs
    # to AND is the sole member of the new BODY set (asserted at the end).
    GROUPED8 = "coach_message_lean_grouped_v8"
    assert prompts.TWO_STAGE_PROMPT_IDS == {
        "coach_message_v2", "coach_message_v3", "coach_message_v4",
        "coach_message_v5", "coach_message_v6", "coach_message_v7", "coach_message_v8",
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4,
        GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    assert prompts.VOICE_PROMPT_IDS == {
        "coach_message_v3", "coach_message_v4", "coach_message_v5",
        "coach_message_v6", "coach_message_v7", "coach_message_v8", "coach_message_v9",
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    assert prompts.CORPUS_PROMPT_IDS == {
        "coach_message_v4", "coach_message_v5", "coach_message_v6",
        "coach_message_v7", "coach_message_v8", "coach_message_v9", "coach_message_v10",
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    assert prompts.STANCE_PROMPT_IDS == {
        "coach_message_v5", "coach_message_v6", "coach_message_v7", "coach_message_v8",
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4,
        GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    # Slice 2: grouped_v2..v6 REPLACE training_load with readiness, so they are NOT here.
    assert prompts.TRAINING_LOAD_PROMPT_IDS == {
        "coach_message_v6", "coach_message_v7", "coach_message_v8", "coach_message_v9",
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.USER_MATERIALS_PROMPT_IDS == {
        "coach_message_v7", "coach_message_v8", "coach_message_v9", "coach_message_v10",
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    # Slice 2: grouped_v2..v6 REPLACE volume + recent_training with recent_weeks, so they
    # are absent from both of these sets.
    assert prompts.VOLUME_PROMPT_IDS == {
        "coach_message_v9", "coach_message_v10", "coach_message_v11", "coach_message_v12",
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.STREAM_VIEW_PROMPT_IDS == {
        "coach_message_v10", "coach_message_v11", "coach_message_v12", "coach_message_v13",
        "coach_message_v14", LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    assert prompts.RECENT_TRAINING_PROMPT_IDS == {
        "coach_message_v11", "coach_message_v12", "coach_message_v13", "coach_message_v14",
        LEAN, GROUPED,
    }
    # Slice 2 PR 2: grouped_v2..v6 REBASE training_history (carry TRAINING_HISTORY_2WK
    # instead), so they drop OUT of the original-ladder set and are the members of the new.
    assert prompts.TRAINING_HISTORY_PROMPT_IDS == {
        "coach_message_v12", "coach_message_v13", "coach_message_v14", LEAN, GROUPED,
    }
    assert prompts.TRAINING_HISTORY_2WK_PROMPT_IDS == {GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    assert prompts.MEMORY_PROMPT_IDS == {
        "coach_message_v13", "coach_message_v14", LEAN, GROUPED, GROUPED2, GROUPED3, GROUPED4,
        GROUPED5, GROUPED6, GROUPED7, GROUPED8,
    }
    # Slice 3: grouped_v3..v6 REPLACE INTENSITY with INTENSITY_READ + INTENSITY_MIX, so they
    # drop OUT of the intensity set and are the members of the two new ones.
    assert prompts.INTENSITY_PROMPT_IDS == {"coach_message_v14", LEAN, GROUPED, GROUPED2}
    assert prompts.INTENSITY_READ_PROMPT_IDS == {GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    assert prompts.INTENSITY_MIX_PROMPT_IDS == {GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    # Slice 2's two sets: grouped_v2..v6 are the members.
    assert prompts.READINESS_PROMPT_IDS == {GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    assert prompts.RECENT_WEEKS_PROMPT_IDS == {GROUPED2, GROUPED3, GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    # Slice 4: grouped_v4..v6 carry the metrics-coach-framed view flag.
    assert prompts.METRICS_COACH_FRAMED_PROMPT_IDS == {GROUPED4, GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    # Slice 5: grouped_v5 (flip target) + grouped_v6 carry the salience-dropped + coach-view flags.
    assert prompts.SALIENCE_DROPPED_PROMPT_IDS == {GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    assert prompts.PACK_COACH_VIEW_PROMPT_IDS == {GROUPED5, GROUPED6, GROUPED7, GROUPED8}
    # #742: BODY is carried by grouped_v8 alone, so the runner's stated build is inert
    # under every other prompt and a rollback to grouped_v7 removes it with no code change.
    assert prompts.BODY_PROMPT_IDS == {GROUPED8}


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
        assert prompts.is_intensity_read_prompt(pid) == has_feature(pid, F.INTENSITY_READ)
        assert prompts.is_intensity_mix_prompt(pid) == has_feature(pid, F.INTENSITY_MIX)
        assert prompts.is_metrics_coach_framed_prompt(pid) == has_feature(pid, F.METRICS_COACH_FRAMED)
        assert prompts.is_salience_dropped_prompt(pid) == has_feature(pid, F.SALIENCE_DROPPED)
        assert prompts.is_pack_coach_view_prompt(pid) == has_feature(pid, F.PACK_COACH_VIEW)


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
