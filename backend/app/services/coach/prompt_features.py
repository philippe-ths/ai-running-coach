"""
Prompt-feature manifest — the single source of truth for which capabilities each
coach prompt id carries.

The coach prompt family grew by adding one capability per version: A4 two-stage
(ADR 0010), then P1.1 voice (ADR 0012/0013), P1.2 corpus (ADR 0014), P1.3 stance
(ADR 0015), P3 training-load (ADR 0016), and P4 user-materials (ADR 0017). Each
capability gates one runtime surface: the per-runner voice block
(``prompts.render_voice_block``), a context-pack section
(``context._build_*_context``), or the post-activity cadence
(``service.is_two_stage_prompt``). The static prompt addenda strings
(``Vn = V(n-1) + addendum``) are baked at module load and are NOT gated here.

This module is the one place that answers "what does prompt vN turn on": read the
``PROMPT_FEATURES`` row. The ``*_PROMPT_IDS`` frozensets and ``is_*_prompt``
predicates in ``prompts.py`` (and ``is_two_stage_prompt`` in ``service.py``) are
thin DERIVED VIEWS over this manifest; their names and semantics are unchanged, so
call sites and tests are untouched.

A prompt id absent from ``PROMPT_FEATURES`` (every legacy ``coach_report_*`` id,
``coach_message_v1``, ``None``, and any unknown id) carries no capabilities — exactly
the inert-under-rollback property each capability relies on, so flipping
``COACH_PROMPT_ID`` off a capability-bearing id leaves that capability wholly inert
with zero code change.

Pure data: this module imports nothing from the coach layer, so it sits below
``prompts.py`` / ``service.py`` with no import cycle.
"""

from enum import Enum
from typing import Optional


class PromptFeature(Enum):
    """A capability a coach prompt id can carry. Each gates one runtime surface."""

    TWO_STAGE = "two_stage"            # A4 two-stage Exchange cadence (ADR 0010)
    VOICE = "voice"                    # P1.1 per-runner voice block (ADR 0012/0013)
    CORPUS = "corpus"                  # P1.2 coaching-corpus section (ADR 0014)
    STANCE = "stance"                  # P1.3 emphasis-axes section (ADR 0015)
    TRAINING_LOAD = "training_load"    # P3 readiness section (ADR 0016)
    USER_MATERIALS = "user_materials"  # P4 distilled user materials (ADR 0017)
    VOLUME = "volume"                  # #400 frequency-/volume-vs-norm section
    STREAM_VIEW = "stream_view"        # #443 consolidated stream-view timeline in the default pack
    RECENT_TRAINING = "recent_training"  # #444 modality-aware recent-training picture
    TRAINING_HISTORY = "training_history"  # #561 multi-year LOD volume ladder + durability traits
    MEMORY = "memory"                  # runner memory profile section (ADR 0025); attached to coach_message_v13 in M3
    INTENSITY = "intensity"            # #578 deterministic intensity-distribution-and-trend section
    READINESS = "readiness"            # ADR 0026 Slice 2 (#670): `right_now.readiness` (renamed training_load)
    RECENT_WEEKS = "recent_weeks"      # ADR 0026 Slice 2 (#670): merged day-resolved `right_now.recent_weeks`
    TRAINING_HISTORY_2WK = "training_history_2wk"  # ADR 0026 Slice 2 (#670): `training_history` ladder rebased after the 2-week recent_weeks window (enriched: by_type + load + dates)
    INTENSITY_READ = "intensity_read"  # ADR 0026 Slice 3 (#673): merged this-run `this_run.intensity_read` + promoted `this_run.referral` (retires perceived_effort/calibration/intensity)
    INTENSITY_MIX = "intensity_mix"    # ADR 0026 Slice 3 (#673): recent intensity distribution + trend `right_now.intensity_mix` (the "how hard lately" half of the retired intensity section)
    METRICS_COACH_FRAMED = "metrics_coach_framed"  # ADR 0026 Slice 4 (#680): reframe the pack's numeric leaves to coach-native units/precision for the LLM view (km/pace/%max/MM:SS); presentation only, no new section


# One row per prompt id, listing its FULL capability set. Read a row to know
# everything that prompt activates. Each coach_message version adds one capability
# to the prior version's set; the rows are spelled out in full (not chained) so a
# prompt's capability set is readable without walking the lineage.
#
# This is the ONLY place prompt ids and capabilities are paired. Everything else
# derives. Any prompt id not present here carries no capabilities.
_F = PromptFeature
PROMPT_FEATURES: dict[str, frozenset[PromptFeature]] = {
    "coach_message_v2": frozenset({_F.TWO_STAGE}),
    "coach_message_v3": frozenset({_F.TWO_STAGE, _F.VOICE}),
    "coach_message_v4": frozenset({_F.TWO_STAGE, _F.VOICE, _F.CORPUS}),
    "coach_message_v5": frozenset({_F.TWO_STAGE, _F.VOICE, _F.CORPUS, _F.STANCE}),
    "coach_message_v6": frozenset(
        {_F.TWO_STAGE, _F.VOICE, _F.CORPUS, _F.STANCE, _F.TRAINING_LOAD}
    ),
    "coach_message_v7": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
        }
    ),
    # #266 — v8 is a philosophy retune, NOT a new capability: it carries exactly v7's
    # feature set (the first message version to add no capability). Its novelty is the
    # retuned base prose; every gated surface behaves identically to v7.
    "coach_message_v8": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
        }
    ),
    # #400 — v9 = v8 + the VOLUME capability (the frequency-/volume-vs-norm section
    # and its addendum). Same retuned base prose as v8; adds one capability.
    "coach_message_v9": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
        }
    ),
    # #443 — v10 = v9 + the STREAM_VIEW capability (the consolidated stream-view
    # timeline in the default pack, plus its reading addendum). Same retuned base
    # prose as v8/v9; adds one capability. Ships INERT (the config default stays v8);
    # the owner flips COACH_PROMPT_ID to coach_message_v10 to activate.
    "coach_message_v10": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
        }
    ),
    # #444 — v11 = v10 + the RECENT_TRAINING capability (the modality-aware
    # recent-training picture in the pack, plus its reading addendum). Same retuned
    # base prose; adds one capability (an additive superset of v10). Ships INERT
    # (the config default stays v8); the owner flips COACH_PROMPT_ID to activate.
    "coach_message_v11": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
        }
    ),
    # #561 — v12 = v11 + the TRAINING_HISTORY capability (the multi-year LOD volume
    # ladder + durability traits section, plus its reading addendum). Same retuned
    # base prose; adds one capability (an additive superset of v11). Ships INERT (the
    # config default stays v8); the owner flips COACH_PROMPT_ID to activate.
    "coach_message_v12": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
            _F.TRAINING_HISTORY,
        }
    ),
    # ADR 0025 — v13 = v12 + the MEMORY capability (the runner memory profile
    # addendum + the `memory` pack section + the background update-pass enqueue).
    "coach_message_v13": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
            _F.TRAINING_HISTORY,
            _F.MEMORY,
        }
    ),
    # #578 — v14 = v13 + the INTENSITY capability (the deterministic intensity-
    # distribution-and-trend section + its addendum). Same retuned base prose as v8+;
    # adds one capability (an additive superset of v13). Ships INERT (the config default
    # stays v8); the owner flips COACH_PROMPT_ID to coach_message_v14 to activate.
    "coach_message_v14": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
            _F.TRAINING_HISTORY,
            _F.MEMORY,
            _F.INTENSITY,
        }
    ),
    # EXPERIMENT — coach_message_lean_v1 is a ground-up prose rewrite (voice-lesson +
    # Matt-Pocock disciplines), NOT a vN increment. It carries the SAME full capability
    # set as v14 ON PURPOSE: prompt-feature parity means it receives the byte-identical
    # context pack, so flipping COACH_PROMPT_ID to it is a clean A/B on the system-prompt
    # TEXT alone. Ships INERT (config default unchanged).
    "coach_message_lean_v1": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
            _F.TRAINING_HISTORY,
            _F.MEMORY,
            _F.INTENSITY,
        }
    ),
    # ADR 0026 Slice 1 — coach_message_lean_grouped_v1 is lean_v1 served the GROUPED
    # pack (the same content re-nested into the five coaching-question groups). It
    # carries the IDENTICAL twelve capabilities as lean_v1 (feature parity), so it
    # receives the same pack CONTENT; only the SHAPE differs (prompts.is_grouped_pack_
    # prompt drives to_grouped_dict serving). The A/B is grouped-vs-flat shape on the
    # same disposition-first prose. Ships INERT.
    "coach_message_lean_grouped_v1": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.TRAINING_LOAD,
            _F.USER_MATERIALS,
            _F.VOLUME,
            _F.STREAM_VIEW,
            _F.RECENT_TRAINING,
            _F.TRAINING_HISTORY,
            _F.MEMORY,
            _F.INTENSITY,
        }
    ),
    # ADR 0026 Slice 2 (#670) — coach_message_lean_grouped_v2 is the grouped prompt whose
    # `right_now` group carries the REDEFINED content: TRAINING_LOAD -> READINESS (a keyed
    # rename) and VOLUME + RECENT_TRAINING -> RECENT_WEEKS (the merged day-resolved read).
    # PR 2 additionally redefines `the_runner.training_history`: TRAINING_HISTORY ->
    # TRAINING_HISTORY_2WK (the SAME section, its ladder rebased to begin after the 2-week
    # recent_weeks window instead of the retired 60d recent_training window, and enriched).
    # It carries every OTHER grouped_v1 capability unchanged; under it the three old
    # `right_now` sections + the 60d training-history ladder drop and their redefined
    # replacements appear. Every prior prompt keeps the originals (byte-stable). Ships INERT.
    "coach_message_lean_grouped_v2": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.READINESS,
            _F.USER_MATERIALS,
            _F.RECENT_WEEKS,
            _F.STREAM_VIEW,
            _F.TRAINING_HISTORY_2WK,
            _F.MEMORY,
            _F.INTENSITY,
        }
    ),
    # ADR 0026 Slice 3 (#673) — coach_message_lean_grouped_v3 collapses the four this-run
    # intensity lenses into `this_run.intensity_read`, promotes the safety nudge to
    # `this_run.referral`, and moves the recent intensity distribution to
    # `right_now.intensity_mix`. It carries INTENSITY_READ + INTENSITY_MIX INSTEAD of
    # INTENSITY; under it the standalone perceived_effort/calibration/intensity sections
    # retire and the two new reads appear. Every OTHER grouped_v2 capability is unchanged.
    # Every prior prompt keeps the originals (byte-stable). Ships INERT.
    "coach_message_lean_grouped_v3": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.READINESS,
            _F.USER_MATERIALS,
            _F.RECENT_WEEKS,
            _F.STREAM_VIEW,
            _F.TRAINING_HISTORY_2WK,
            _F.MEMORY,
            _F.INTENSITY_READ,
            _F.INTENSITY_MIX,
        }
    ),
    # ADR 0026 Slice 4 (#680) — coach_message_lean_grouped_v4 reframes the pack's numeric
    # leaves to coach-native units and precision for the outgoing LLM view (km not metres,
    # min:sec/km not m/s, minute-granularity session durations + second-resolution interval/
    # zone times, bpm with a % of max supplement on the two headline HRs, dropped efficiency
    # curve + per-rep offsets, trimmed decimals). It carries every grouped_v3 capability plus
    # METRICS_COACH_FRAMED; the reframing is a one-way serialization VIEW (report + chat pack)
    # over the canonical unframed pack, so re-parse/validator/eval are untouched and every
    # prior prompt is byte-identical. Ships INERT.
    "coach_message_lean_grouped_v4": frozenset(
        {
            _F.TWO_STAGE,
            _F.VOICE,
            _F.CORPUS,
            _F.STANCE,
            _F.READINESS,
            _F.USER_MATERIALS,
            _F.RECENT_WEEKS,
            _F.STREAM_VIEW,
            _F.TRAINING_HISTORY_2WK,
            _F.MEMORY,
            _F.INTENSITY_READ,
            _F.INTENSITY_MIX,
            _F.METRICS_COACH_FRAMED,
        }
    ),
}


# The runner-facing capabilities. TWO_STAGE is the cadence shape (an operational
# choice, inert under the receipt cadence and rolled back deliberately), so a prompt
# that carries ONLY two-stage is not what we mean by "feature-bearing": this set is
# the runner-visible coaching surface (voice/corpus/stance/training-load/
# user-materials/volume) whose silent loss #424 guards against.
RUNNER_FACING_FEATURES: frozenset[PromptFeature] = frozenset(
    {
        PromptFeature.VOICE,
        PromptFeature.CORPUS,
        PromptFeature.STANCE,
        PromptFeature.TRAINING_LOAD,
        PromptFeature.USER_MATERIALS,
        PromptFeature.VOLUME,
        # ADR 0026 Slice 2 (#670): the redefined right_now content is runner-facing too —
        # readiness replaces training_load, recent_weeks replaces volume.
        PromptFeature.READINESS,
        PromptFeature.RECENT_WEEKS,
    }
)


def has_feature(prompt_id: Optional[str], feature: PromptFeature) -> bool:
    """True when ``prompt_id`` carries ``feature``. False for any unknown id or None."""
    return feature in PROMPT_FEATURES.get(prompt_id, frozenset())


def features_for(prompt_id: Optional[str]) -> frozenset[PromptFeature]:
    """The full capability set ``prompt_id`` carries (empty for any unknown id/None)."""
    return PROMPT_FEATURES.get(prompt_id, frozenset())


def carries_runner_facing_features(prompt_id: Optional[str]) -> bool:
    """True when ``prompt_id`` activates at least one runner-visible coaching feature.

    False for a legacy ``coach_report_*`` id, ``coach_message_v1``, a TWO_STAGE-only
    id, ``None``, or any unknown id. This is the predicate #424 uses to detect a
    silently degraded active prompt at startup.
    """
    return bool(features_for(prompt_id) & RUNNER_FACING_FEATURES)


def ids_with(feature: PromptFeature) -> frozenset[str]:
    """The frozenset of prompt ids carrying ``feature`` (derives the ``*_PROMPT_IDS``
    sets in ``prompts.py``)."""
    return frozenset(pid for pid, feats in PROMPT_FEATURES.items() if feature in feats)


# Features that REPLACE an earlier additive capability rather than ADD a new pack
# section (ADR 0026): a grouped prompt carries these INSTEAD of the section(s) they
# supersede (READINESS replaces training_load; RECENT_WEEKS replaces volume +
# recent_training; TRAINING_HISTORY_2WK rebases training_history; INTENSITY_READ /
# INTENSITY_MIX replace perceived_effort/calibration/intensity). A prompt bearing them
# is an ALTERNATIVE SHAPE, not a fuller superset, so raw feature count no longer tracks
# "the fullest pack" (the chain stopped being monotonic at Slice 2). `fullest_message_
# prompt_id` ranks by ADDITIVE features so it always resolves to a genuine full-superset
# prompt whose pack turns on every original gated section.
ALTERNATIVE_FEATURES: frozenset[PromptFeature] = frozenset(
    {
        PromptFeature.READINESS,
        PromptFeature.RECENT_WEEKS,
        PromptFeature.TRAINING_HISTORY_2WK,
        PromptFeature.INTENSITY_READ,
        PromptFeature.INTENSITY_MIX,
        # ADR 0026 Slice 4 (#680): a presentation-only leaf reframing, not a new pack
        # section, so it must not count toward "the fullest pack" additive ranking.
        PromptFeature.METRICS_COACH_FRAMED,
    }
)


def fullest_message_prompt_id() -> str:
    """The ``coach_message`` prompt id whose pack turns on every ORIGINAL gated section,
    so its serialized pack is the FULLEST and its duplication/byte-stability surface the
    LARGEST.

    Derived from the manifest so structural pack guards (the no-duplicate-content and
    single-home-facts tests) auto-track the newest gated section instead of pinning a
    hardcoded version that silently goes stale the next time a section is added — the
    exact gap that let a new section's overlap ship unseen. Ranked by ADDITIVE feature
    count (``ALTERNATIVE_FEATURES`` excluded), because the ADR 0026 grouped prompts carry
    alternative features that REPLACE original sections rather than add them — a raw count
    would pick such an alternative, whose pack is NOT a superset. Ties resolve to the
    newest (last-declared) id.
    """
    best: Optional[str] = None
    best_n = -1
    for pid, feats in PROMPT_FEATURES.items():
        if not pid.startswith("coach_message"):
            continue
        additive_n = len(feats - ALTERNATIVE_FEATURES)
        if additive_n >= best_n:
            best, best_n = pid, additive_n
    assert best is not None  # PROMPT_FEATURES is never empty
    return best
