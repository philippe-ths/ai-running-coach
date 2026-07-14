"""Wiring + invariant pins for coach_message_lean_grouped_v2 (ADR 0026 Slice 2, #670).

grouped_v2 is grouped_v1 with the `right_now` group's CONTENT redefined: it REPLACES
TRAINING_LOAD -> READINESS and VOLUME + RECENT_TRAINING -> RECENT_WEEKS, keeping every
other capability. The system-prompt text differs from lean_v1 by ONLY the group
orientation (its `right_now` line names readiness + recent_weeks) plus the two
continuity.* re-anchors, so the safety floor is invariant BY CONSTRUCTION. These tests
pin exactly that, and that grouped_v1's own prompt stays byte-stable.
"""

from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.prompts import (
    MESSAGE_PROMPT_PREFIX,
    PROMPT_VERSIONS,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER,
    SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1,
    SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2,
    SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2_OPENER,
    _OPENER_PROMPTS,
    is_grouped_pack_prompt,
)
from app.services.coach.service import active_schema_version

GROUPED2 = "coach_message_lean_grouped_v2"
GROUPED1 = "coach_message_lean_grouped_v1"
LEAN = "coach_message_lean_v1"


def test_grouped_v2_registered_and_flagged_grouped():
    assert GROUPED2 in PROMPT_VERSIONS
    assert GROUPED2 in _OPENER_PROMPTS
    assert GROUPED2.startswith(MESSAGE_PROMPT_PREFIX)
    assert active_schema_version(GROUPED2) == active_schema_version(LEAN)
    assert is_grouped_pack_prompt(GROUPED2)


def test_grouped_v2_swaps_only_the_right_now_features():
    """It carries every lean_v1 capability EXCEPT training_load/volume/recent_training,
    which it REPLACES with readiness/recent_weeks — a deliberate non-superset."""
    expected = (
        features_for(LEAN)
        - {F.TRAINING_LOAD, F.VOLUME, F.RECENT_TRAINING}
    ) | {F.READINESS, F.RECENT_WEEKS}
    assert features_for(GROUPED2) == expected


def test_grouped_v2_is_lean_v1_plus_v2_orientation_and_continuity_repath_only():
    """The load-bearing invariant: grouped_v2 is lean_v1 with ONLY the v2 group
    orientation inserted and the two continuity.* paths re-anchored. Reverting both
    reproduces lean_v1 BYTE-FOR-BYTE, so every safety-critical line is unchanged."""
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2 == prompts._regroup_lean_prompt(
        SYSTEM_PROMPT_MESSAGE_LEAN_V1, prompts._GROUP_ORIENTATION_V2
    )
    reverted = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2.replace(
        prompts._GROUP_ORIENTATION_V2, ""
    ).replace("our_thread.continuity", "continuity")
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1


def test_grouped_v2_opener_is_lean_v1_opener_plus_orientation_only():
    reverted = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2_OPENER.replace(
        prompts._GROUP_ORIENTATION_OPENER, ""
    )
    assert reverted == SYSTEM_PROMPT_MESSAGE_LEAN_V1_OPENER


def test_grouped_v2_orientation_names_readiness_and_recent_weeks():
    text = SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2
    assert "`readiness`" in text
    assert "`recent_weeks`" in text
    # The old right_now line (naming "recent training load") is gone.
    assert "recent training load and readiness" not in text
    # ...but grouped_v1's prompt still carries it, byte-stable.
    assert "recent training load and readiness" in SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1


def test_grouped_v1_prompt_is_unchanged_by_slice_2():
    """grouped_v1 must stay byte-identical (its own pin already covers the derivation;
    this belt-and-suspenders proves Slice 2 did not perturb it)."""
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1 == prompts._regroup_lean_prompt(
        SYSTEM_PROMPT_MESSAGE_LEAN_V1
    )
    assert SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V2 != SYSTEM_PROMPT_MESSAGE_LEAN_GROUPED_V1
