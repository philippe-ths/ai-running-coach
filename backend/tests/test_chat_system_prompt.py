"""Pins for the chat system prompt (`CHAT_SYSTEM_TEMPLATE`).

The chat surface is NOT version-gated like the report prompts (lean_v1/v2); editing
the template changes prod chat on the next deploy. These pins guard the load-bearing
surface and the #653 week-frame discipline against silent loss, and prove the template
still renders (a stray brace would break the `.format()` assembly).
"""

from app.services.coach.chat import (
    CHAT_SYSTEM_TEMPLATE,
    _build_chat_system_prompt,
    _render_authority_tiering,
)

# A prompt carrying the FULL capability set (prod runs it) — every tier renders.
FULL_FEATURE_PROMPT = "coach_message_lean_v1"
# A prompt carrying NO runner-facing capability — every gated tier must drop.
FEATURE_POOR_PROMPT = "coach_report_v10"


def test_chat_template_renders_without_brace_errors():
    """`.format()` must survive — a literal `{`/`}` in the template would raise here."""
    rendered = _build_chat_system_prompt(
        context_pack={"training_volume": {"calendar_week": {}}},
        report={},
        profile={},
        splits=[],
        voice_block="",
        cross_activity_block="",
    )
    assert "running coach" in rendered.lower()
    # the injected context placeholders were filled, none left dangling
    assert "{context_pack_json}" not in rendered


def test_chat_carries_the_week_frame_mirroring_discipline():
    """#653 extended to chat: mirror the runner's calendar-week frame, read a partial
    week as on-pace not a shortfall, keep rolling-7d as the trailing-load read."""
    t = CHAT_SYSTEM_TEMPLATE.lower()
    assert "calendar-week frame" in t
    assert "training_volume.calendar_week" in t
    assert "on-pace" in t
    assert "not their week" in t


def test_chat_tiering_cites_memory_not_the_retired_sections():
    """ADR 0025 retired the narrative + believed_facts sections (now null stubs); the
    runner `memory` profile replaced them. Under a memory-aware prompt the chat
    authority-tiering must brief the live `memory` tier as the citable Stated-memory
    tier and must NOT re-brief the dead ones, matching the report/lean prompt."""
    t = _render_authority_tiering(FULL_FEATURE_PROMPT).lower()
    assert "memory" in t
    assert "the one tier you may cite as fact" in t
    assert "yields to this run's measured data" in t
    assert "believed_facts" not in t
    assert "narrative" not in t


def test_chat_tiering_gated_on_active_prompt_features():
    """#667: chat is NOT version-gated like the report, so it must brief only the tiers
    the ACTIVE prompt carries — otherwise a COACH_PROMPT_ID rollback leaves chat
    advertising a section the pack has dropped (the drift #653 fixed for the retired
    tiers). Each capability tier gates on the same PromptFeature the report side uses,
    exactly as the voice block already does."""
    full = _render_authority_tiering(FULL_FEATURE_PROMPT)
    poor = _render_authority_tiering(FEATURE_POOR_PROMPT)

    # The always-on floor survives in both: the header, the measured-data-wins ordering,
    # and the cross-activity continuity note (gated on the block, not the prompt).
    for text in (full, poor):
        assert "RELATIONSHIP MEMORY & AUTHORITY TIERING:" in text
        assert "the safety floor (rule 2) ALWAYS win" in text
        assert "- RELATIONSHIP CONVERSATION" in text

    # Under the full-feature prompt every capability tier is briefed.
    for tier in ("- VOICE", "- MEMORY", "- COACHING CORPUS & USER MATERIALS", "- TRAINING LOAD"):
        assert tier in full, f"full-feature prompt should brief {tier}"

    # Under the feature-poor prompt every capability tier is dropped.
    for tier in ("- VOICE", "- MEMORY", "- COACHING CORPUS", "- TRAINING LOAD"):
        assert tier not in poor, f"feature-poor prompt must NOT brief {tier}"


def test_chat_tiering_corpus_without_user_materials():
    """A CORPUS-but-not-USER_MATERIALS rollback target (v4/v5/v6) briefs the corpus tier
    without claiming uploaded materials the pack does not carry."""
    t = _render_authority_tiering("coach_message_v4")
    assert "- COACHING CORPUS (" in t  # corpus-only variant
    assert "USER MATERIALS" not in t
    assert "- MEMORY" not in t  # v4 predates memory


def test_chat_keeps_the_load_bearing_safety_surface():
    """The week-frame addition did not disturb the floor: no-diagnose, zone language,
    ground-every-claim, and conservative volume all still present."""
    t = CHAT_SYSTEM_TEMPLATE.lower()
    assert "never diagnose" in t
    assert "zones_calibrated" in t
    assert "ground every claim" in t
    assert "risky volume jumps" in t


def test_chat_rules_deduped_without_losing_intent():
    """#668: the overlapping RULES were folded, not just cut. The restatements are gone;
    their intent survives in the load-bearing rule (or the TOOLS section) they doubled."""
    t = CHAT_SYSTEM_TEMPLATE
    # the three restating rules are removed
    assert "Reference specific numbers from the data when relevant" not in t
    assert "Keep answers conversational but grounded" not in t
    assert "If the runner asks about training history you cannot see above, FETCH it" not in t
    # their intent survives where it belongs
    assert "citing the specific numbers" in t          # folded into ground-every-claim
    assert "conversational, a knowledgeable coach" in t  # folded into be-concise
    assert "once the tools have come up empty" in t     # folded into the TOOLS section
    # the list is now nine rules (no stray tenth after the fold)
    assert "\n9. WEEKLY FRAME" in t
    assert "\n10." not in t
