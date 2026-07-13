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

# A pack with every relationship-memory section present (prod's full-feature pack).
FULL_PACK = {
    "memory": {"who_you_are": ["marathoner"]},
    "corpus": {"school": {"stance": "aerobic"}, "user_materials": [{"stance": "x"}]},
    "training_load": {"fitness": 40.0},
}


def _full_tiering():
    return _render_authority_tiering(FULL_PACK, voice_present=True, cross_activity_present=True)


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
    runner `memory` profile replaced them. When the pack carries a `memory` section the
    chat authority-tiering must brief the live `memory` tier as the citable Stated-memory
    tier and must NOT re-brief the dead ones, matching the report/lean prompt."""
    t = _full_tiering().lower()
    assert "memory" in t
    assert "the one tier you may cite as fact" in t
    assert "yields to this run's measured data" in t
    assert "believed_facts" not in t
    assert "narrative" not in t


def test_chat_tiering_gated_on_pack_contents():
    """#667: chat replays the report's STORED pack, so it must brief only the tiers whose
    section is actually IN that pack. A section dropped by a COACH_PROMPT_ID rollback OR a
    kill switch drops its key byte-stably (#493), so gating on the pack contents un-briefs
    it everywhere at once — the fix for chat advertising sections the pack no longer has."""
    full = _full_tiering()
    # empty pack, no voice, no cross-activity: only the floor header survives.
    bare = _render_authority_tiering({}, voice_present=False, cross_activity_present=False)

    # The floor header (measured data + safety win) is always emitted.
    for text in (full, bare):
        assert "RELATIONSHIP MEMORY & AUTHORITY TIERING:" in text
        assert "the safety floor (rule 2) ALWAYS win" in text

    # With every section present each tier is briefed.
    for tier in ("- VOICE", "- MEMORY", "- COACHING CORPUS & USER MATERIALS", "- TRAINING LOAD"):
        assert tier in full, f"full pack should brief {tier}"
    assert "- RELATIONSHIP CONVERSATION" in full  # cross-activity digest present

    # With an empty pack (nothing in front of the coach) every tier is dropped.
    for tier in ("- VOICE", "- MEMORY", "- COACHING CORPUS", "- TRAINING LOAD", "- RELATIONSHIP CONVERSATION"):
        assert tier not in bare, f"empty pack must NOT brief {tier}"


def test_chat_tiering_drops_tier_gated_off_even_when_prompt_would_carry_it():
    """The kill-switch / frozen-pack case: the memory tier is briefed off the pack's OWN
    `memory` key, not the prompt version — so a pack whose memory section was dropped
    (COACH_MEMORY_ENABLED off, or a stale pre-memory report) is NOT briefed on memory,
    while the sections it DOES carry still are."""
    pack = {"corpus": {"user_materials": [{"x": 1}]}, "training_load": {"fitness": 1.0}}
    t = _render_authority_tiering(pack, voice_present=True, cross_activity_present=False)
    assert "- MEMORY" not in t                          # dropped: no memory in the pack
    assert "- COACHING CORPUS & USER MATERIALS" in t     # kept: present
    assert "- TRAINING LOAD" in t                        # kept: present
    assert "- RELATIONSHIP CONVERSATION" not in t        # no cross-activity digest


def test_chat_tiering_corpus_without_user_materials():
    """A pack carrying the corpus school but no uploaded materials briefs the corpus tier
    without claiming materials the pack does not carry."""
    t = _render_authority_tiering(
        {"corpus": {"school": {"stance": "x"}}}, voice_present=False, cross_activity_present=False
    )
    assert "- COACHING CORPUS (" in t  # corpus-only variant
    assert "USER MATERIALS" not in t
    assert "- MEMORY" not in t


def test_chat_voice_tier_follows_the_voice_block_presence():
    """Voice is not a pack section, so its tier follows the rendered voice block. When the
    block is absent (voice not active / kill-switched), the voice tier drops too."""
    off = _render_authority_tiering(FULL_PACK, voice_present=False, cross_activity_present=True)
    assert "- VOICE" not in off
    on = _render_authority_tiering(FULL_PACK, voice_present=True, cross_activity_present=True)
    assert "- VOICE" in on


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
