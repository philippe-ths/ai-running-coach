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
}


def has_feature(prompt_id: Optional[str], feature: PromptFeature) -> bool:
    """True when ``prompt_id`` carries ``feature``. False for any unknown id or None."""
    return feature in PROMPT_FEATURES.get(prompt_id, frozenset())


def ids_with(feature: PromptFeature) -> frozenset[str]:
    """The frozenset of prompt ids carrying ``feature`` (derives the ``*_PROMPT_IDS``
    sets in ``prompts.py``)."""
    return frozenset(pid for pid, feats in PROMPT_FEATURES.items() if feature in feats)
