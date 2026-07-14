"""Wiring + characterization pins for the coach_message_lean_v1 EXPERIMENT.

lean_v1 is a ground-up, disposition-first rewrite of the coach system prompt drawn
from the "giving an LLM a voice" lesson (design from the disposition down;
demonstrate, don't describe; keep it tight) and Matt Pocock's "writing great skills"
(trust the agent intrinsically; cut any instruction that forces no observable
change). It is NOT part of the Vn = V(n-1) + addendum chain.

These tests pin what the experiment MUST hold to be a clean A/B against v14:
  - it is registered and dispatches to the schema-2.0 message family;
  - it has full prompt-feature parity with v14, so it receives the identical pack;
  - it is a genuine rewrite, not v14 + addenda (it contains none of the stacked
    addendum constants);
  - it is radically shorter than v14 (the falsifiable claim of the experiment);
  - it still carries the load-bearing output-contract, domain-fact, and safety
    surface (the rewrite compressed the prose, it did not drop the floor);
  - it ships INERT (the config default is unchanged).
"""

from app.core.config import settings
from app.services.coach import prompts
from app.services.coach.prompt_features import PROMPT_FEATURES, features_for
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER,
    SYSTEM_PROMPT_MESSAGE_V14,
    SYSTEM_PROMPT_MESSAGE_V14_OPENER,
    _OPENER_PROMPTS,
    build_system_prompt,
)
from app.services.coach.service import active_schema_version

LEAN = "coach_message_lean_v1"
V14 = "coach_message_v14"

# The stacked per-section addendum constants that make up the vN chain. A genuine
# ground-up rewrite must contain NONE of them verbatim.
_CHAIN_ADDENDA = [
    prompts._MESSAGE_V2_CONTINUITY,
    prompts._VOICE_ADDENDUM,
    prompts._CORPUS_ADDENDUM,
    prompts._EMPHASIS_ADDENDUM,
    prompts._READINESS_ADDENDUM,
    prompts._USER_MATERIALS_ADDENDUM,
    prompts._VOLUME_ADDENDUM,
    prompts._STREAM_VIEW_ADDENDUM,
    prompts._RECENT_TRAINING_ADDENDUM,
    prompts._TRAINING_HISTORY_ADDENDUM,
    prompts._MEMORY_ADDENDUM,
    prompts._INTENSITY_ADDENDUM,
]


def test_lean_v1_registered_in_message_family():
    assert LEAN in PROMPT_VERSIONS
    assert LEAN in _OPENER_PROMPTS  # a distinct opener form (two-stage)
    assert LEAN.startswith(MESSAGE_PROMPT_PREFIX)  # -> schema 2.0 by prefix
    # same schema family as v14, so a lean report caches/regenerates identically.
    assert active_schema_version(LEAN) == active_schema_version(V14)


def test_lean_v1_has_full_capability_parity_with_v14():
    """The experiment receives the IDENTICAL context pack as v14 (same gated sections),
    so the A/B isolates the system-prompt text. That parity is the whole design."""
    assert features_for(LEAN) == features_for(V14)
    # ADR 0026 Slice 2 (#670) added the mutually-exclusive READINESS/RECENT_WEEKS and (PR 2)
    # TRAINING_HISTORY_2WK alternatives; Slice 3 (#673) adds INTENSITY_READ/INTENSITY_MIX
    # (they REPLACE training_load/volume/recent_training/training_history and
    # perceived_effort/calibration/intensity under the grouped prompts), so no single prompt
    # carries the full union; lean_v1 carries every ADDITIVE feature.
    from app.services.coach.prompt_features import PromptFeature as _F
    every_additive = set().union(*PROMPT_FEATURES.values()) - {
        _F.READINESS, _F.RECENT_WEEKS, _F.TRAINING_HISTORY_2WK,
        _F.INTENSITY_READ, _F.INTENSITY_MIX,
    }
    assert features_for(LEAN) == every_additive


def test_lean_v1_is_a_rewrite_not_v14_plus_addenda():
    """It is NOT in the Vn chain: it contains none of the stacked addendum constants,
    and it is not a prefix/superset relationship with v14."""
    for addendum in _CHAIN_ADDENDA:
        assert addendum not in SYSTEM_PROMPT_MESSAGE_LEAN_V1, addendum[:40]
        assert addendum not in SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER, addendum[:40]
    assert not SYSTEM_PROMPT_MESSAGE_LEAN_V1.startswith(SYSTEM_PROMPT_MESSAGE_V14)
    assert not SYSTEM_PROMPT_MESSAGE_V14.startswith(SYSTEM_PROMPT_MESSAGE_LEAN_V1)


def test_lean_v1_is_radically_shorter_than_v14():
    """The falsifiable claim of the experiment: a radical reduction, not a few tweaks.
    Measured ~16% (fuller) / ~7% (opener) of v14; the thresholds leave headroom."""
    assert len(SYSTEM_PROMPT_MESSAGE_LEAN_V1) < 0.30 * len(SYSTEM_PROMPT_MESSAGE_V14)
    assert len(SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER) < 0.20 * len(
        SYSTEM_PROMPT_MESSAGE_V14_OPENER
    )


def test_lean_v1_builds_for_both_modes():
    fuller = build_system_prompt(LEAN, mode="fuller", voice=None)
    opener = build_system_prompt(LEAN, mode="opener", voice=None)
    assert SYSTEM_PROMPT_MESSAGE_LEAN_V1 in fuller
    assert SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER in opener
    # voice-aware => the per-runner voice block is appended (default persona when None).
    assert "YOUR VOICE FOR THIS RUNNER" in fuller
    assert "YOUR VOICE FOR THIS RUNNER" in opener


def test_lean_v1_keeps_the_load_bearing_surface():
    """The rewrite compressed the prose; it did not drop the output contract, the
    domain facts the model would otherwise misread, or the safety lane."""
    fuller = SYSTEM_PROMPT_MESSAGE_LEAN_V1.lower()
    # output contract
    assert "record_coach_tail" in fuller
    # domain facts that must survive (model misreads these by default)
    assert "effort_score" in fuller and "load" in fuller
    assert "discount_signals" in fuller
    assert "zones_calibrated" in fuller
    # the one authority rule + the citable memory tier
    assert "ground truth" in fuller
    assert "memory" in fuller
    # safety lane (also enforced downstream by validate_message_policy)
    assert "diagnos" in fuller and "dose" in fuller
    assert "pain_score" in fuller
    # opener carries its own scheduling + safety surface
    opener = SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER.lower()
    assert "schedule_fuller_turn" in opener
    assert "diagnos" in opener
    assert "zones_calibrated" in opener


def test_lean_v1_carries_the_living_threads_discipline():
    """#651: the coach resolves an open thread against the runner's actions + the run
    data (closing a data-answered question rather than re-asking it), keeps asking only
    what the data cannot settle, and HOLDS a date-tied thread rather than computing an
    expiry it cannot ground. Fuller-only: thread-resolution is analytical work the
    opener does not do."""
    fuller = SYSTEM_PROMPT_MESSAGE_LEAN_V1.lower()
    # the disposition: read a thread's state and close it from the data, not re-ask
    assert "open threads" in fuller
    assert "instead of re-asking" in fuller
    # the over-correction guard: answer what you can, ask only what you can't
    assert "ask only what it can't" in fuller
    # the honest temporal limit: hold a date-tied thread, never compute the expiry
    assert "a date i can't work out" in fuller
    # demonstrate the hard case (the flagship interval re-ask, now answered)
    assert "a thread the data has already closed" in fuller
    # the disposition is fuller-only; the opener stays a brief reaction
    assert "open threads" not in SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER.lower()


def test_lean_v1_ships_inert():
    """Dormant until the runner flips COACH_PROMPT_ID locally; the config default is
    unchanged by this experiment."""
    assert settings.COACH_PROMPT_ID != LEAN
