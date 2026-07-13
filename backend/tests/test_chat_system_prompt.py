"""Pins for the chat system prompt (`CHAT_SYSTEM_TEMPLATE`).

The chat surface is NOT version-gated like the report prompts (lean_v1/v2); editing
the template changes prod chat on the next deploy. These pins guard the load-bearing
surface and the #653 week-frame discipline against silent loss, and prove the template
still renders (a stray brace would break the `.format()` assembly).
"""

from app.services.coach.chat import CHAT_SYSTEM_TEMPLATE, _build_chat_system_prompt


def test_chat_template_renders_without_brace_errors():
    """`.format()` must survive — a literal `{`/`}` in the template would raise here."""
    rendered = _build_chat_system_prompt(
        context_pack={"training_volume": {"calendar_week": {}}},
        report={},
        profile={},
        trends={},
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
    runner `memory` profile replaced them. The chat authority-tiering must brief the
    live `memory` tier as the citable Stated-memory tier and must NOT re-brief the dead
    ones, matching the report/lean prompt."""
    t = CHAT_SYSTEM_TEMPLATE.lower()
    assert "memory" in t
    assert 'the one tier you may cite as fact' in t
    assert "yields to this run's measured data" in t
    assert "believed_facts" not in t
    assert "narrative" not in t


def test_chat_keeps_the_load_bearing_safety_surface():
    """The week-frame addition did not disturb the floor: no-diagnose, zone language,
    ground-every-claim, and conservative volume all still present."""
    t = CHAT_SYSTEM_TEMPLATE.lower()
    assert "never diagnose" in t
    assert "zones_calibrated" in t
    assert "ground every claim" in t
    assert "risky volume jumps" in t
