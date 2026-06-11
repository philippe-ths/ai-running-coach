"""Tests for the A3 output contract (prose message + thin structured tail)."""

from types import SimpleNamespace

import pytest

from app.schemas.coach import CoachMessageReport
from app.services.coach.output_contract import (
    RECORD_COACH_TAIL_TOOL,
    TOOL_NAME,
    EmptyMessageError,
    ParsedBlocks,
    merge_opener,
    merge_report,
    parse_blocks,
)


def _text(s):
    return {"type": "text", "text": s}


def _tool(input_dict, name=TOOL_NAME):
    return {"type": "tool_use", "name": name, "input": input_dict}


_FULL_TAIL = {
    "headline": "Solid aerobic long run",
    "next_steps": [
        {"action": "Easy run", "details": "40 min", "why": "Recovery",
         "evidence": [{"field": "metrics.effort", "value": "easy"}]},
    ],
    "risks": [],
    "questions": [
        {"question": "How did it feel?", "reason": "No RPE logged",
         "options": [{"id": "rpe5", "label": "RPE 5", "kind": "rpe", "payload": 5}]},
    ],
}


class TestToolSchema:
    def test_tool_name_and_strict_shape(self):
        assert RECORD_COACH_TAIL_TOOL["name"] == TOOL_NAME
        schema = RECORD_COACH_TAIL_TOOL["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # strict subset forbids string maxLength — the headline bound is a prompt rule
        assert "maxLength" not in schema["properties"]["headline"]
        for key in ("headline", "next_steps", "risks", "questions"):
            assert key in schema["required"]


class TestParseBlocks:
    def test_text_and_tool(self):
        parsed = parse_blocks([_text("Nice run today."), _tool(_FULL_TAIL)])
        assert parsed.message == "Nice run today."
        assert parsed.tail == _FULL_TAIL

    def test_order_agnostic_tool_before_text(self):
        parsed = parse_blocks([_tool(_FULL_TAIL), _text("Nice run.")])
        assert parsed.message == "Nice run."
        assert parsed.tail == _FULL_TAIL

    def test_multiple_text_blocks_joined(self):
        parsed = parse_blocks([_text("Para one."), _text("Para two.")])
        assert parsed.message == "Para one.\n\nPara two."

    def test_text_only_no_tail(self):
        parsed = parse_blocks([_text("Just prose.")])
        assert parsed.message == "Just prose."
        assert parsed.tail is None

    def test_tool_only_empty_message(self):
        parsed = parse_blocks([_tool(_FULL_TAIL)])
        assert parsed.message == ""
        assert parsed.tail == _FULL_TAIL

    def test_multiple_tail_blocks_first_wins(self):
        first = dict(_FULL_TAIL, headline="first")
        second = dict(_FULL_TAIL, headline="second")
        parsed = parse_blocks([_tool(first), _tool(second)])
        assert parsed.tail["headline"] == "first"

    def test_unrecognised_tool_ignored(self):
        parsed = parse_blocks([_text("hi"), _tool(_FULL_TAIL, name="other_tool")])
        assert parsed.tail is None

    def test_sdk_object_blocks(self):
        blocks = [
            SimpleNamespace(type="text", text="From SDK."),
            SimpleNamespace(type="tool_use", name=TOOL_NAME, input=_FULL_TAIL),
        ]
        parsed = parse_blocks(blocks)
        assert parsed.message == "From SDK."
        assert parsed.tail == _FULL_TAIL

    def test_empty_blocks(self):
        parsed = parse_blocks([])
        assert parsed.message == ""
        assert parsed.tail is None


class TestMergeReport:
    def test_full_tail_builds_full_report(self):
        report = merge_report(ParsedBlocks(message="Great work.", tail=_FULL_TAIL))
        assert isinstance(report, CoachMessageReport)
        assert report.message == "Great work."
        assert report.headline == "Solid aerobic long run"
        assert report.tail_degraded is False
        assert len(report.next_steps) == 1
        assert report.next_steps[0].action == "Easy run"
        assert report.questions[0].options[0].kind == "rpe"
        assert report.questions[0].options[0].payload == 5

    def test_no_tail_degrades_but_keeps_message(self):
        report = merge_report(ParsedBlocks(message="Real coaching.", tail=None))
        assert report.message == "Real coaching."
        assert report.tail_degraded is True
        assert report.next_steps == []

    def test_empty_message_refused(self):
        with pytest.raises(EmptyMessageError):
            merge_report(ParsedBlocks(message="   ", tail=_FULL_TAIL))

    def test_malformed_tail_degrades(self):
        bad = {"next_steps": [{"action": "x"}]}  # missing required details/why
        report = merge_report(ParsedBlocks(message="Kept.", tail=bad))
        assert report.message == "Kept."
        assert report.tail_degraded is True
        assert report.next_steps == []

    def test_tail_with_unknown_key_ignored(self):
        # Pydantic ignores unknown keys by default, so a forward-compatible extra
        # field does not degrade the tail — the known fields still build.
        extra = dict(_FULL_TAIL, surprise="nope")
        report = merge_report(ParsedBlocks(message="Kept.", tail=extra))
        assert report.message == "Kept."
        assert report.headline == "Solid aerobic long run"
        assert report.tail_degraded is False

    def test_schedule_fuller_turn_in_tail_splats_in(self):
        # A4: the opener's schedule judgment rides the tail splat into the report.
        tail = dict(_FULL_TAIL, schedule_fuller_turn=True)
        report = merge_report(ParsedBlocks(message="Kept.", tail=tail))
        assert report.schedule_fuller_turn is True


class TestToolSchemaScheduleBit:
    def test_schedule_fuller_turn_is_optional_boolean(self):
        # A4: schedule_fuller_turn is a top-level tail property but NOT required
        # (opener-only; the fuller turn omits it), like evidence/payload.
        props = RECORD_COACH_TAIL_TOOL["input_schema"]["properties"]
        assert props["schedule_fuller_turn"]["type"] == "boolean"
        assert "schedule_fuller_turn" not in RECORD_COACH_TAIL_TOOL["input_schema"]["required"]


_OPENER_TAIL = {
    "headline": "Quick reaction",
    "next_steps": [],
    "risks": [],
    "questions": [
        {"question": "How did that feel?", "reason": "Calibrate intensity",
         "options": [{"id": "rpe", "label": "Rate 1-10", "kind": "rpe"},
                     {"id": "pain", "label": "Any pain?", "kind": "pain"}]},
    ],
    "schedule_fuller_turn": True,
}


class TestMergeOpener:
    def test_opener_prose_lands_in_opener_message(self):
        report = merge_opener(ParsedBlocks(message="Nice work out there!", tail=_OPENER_TAIL))
        assert report.opener_message == "Nice work out there!"
        assert report.message == ""  # fuller turn fills this later
        assert report.schedule_fuller_turn is True
        assert report.questions[0].options[0].kind == "rpe"
        assert report.tail_degraded is False

    def test_opener_carries_no_commitments(self):
        # next_steps/risks are never carried from an opener tail — the opener makes
        # no commitments and contributes nothing to the M7 learning loop.
        tail = dict(_OPENER_TAIL, next_steps=[
            {"action": "Add intervals", "details": "x", "why": "y"}
        ], risks=[{"flag": "high_drift", "explanation": "x", "mitigation": "y"}])
        report = merge_opener(ParsedBlocks(message="Reaction.", tail=tail))
        assert report.next_steps == []
        assert report.risks == []

    def test_opener_empty_prose_refused(self):
        with pytest.raises(EmptyMessageError):
            merge_opener(ParsedBlocks(message="  ", tail=_OPENER_TAIL))

    def test_opener_no_tail_degrades_keeps_prose(self):
        report = merge_opener(ParsedBlocks(message="Reaction only.", tail=None))
        assert report.opener_message == "Reaction only."
        assert report.message == ""
        assert report.tail_degraded is True
        # A degraded opener tail loses its schedule judgment; defaults to no-fuller
        # (the safety override is the deterministic backstop).
        assert report.schedule_fuller_turn is False
        assert report.questions == []

    def test_opener_defaults_schedule_when_absent(self):
        tail = {"headline": "h", "next_steps": [], "risks": [], "questions": []}
        report = merge_opener(ParsedBlocks(message="Reaction.", tail=tail))
        assert report.schedule_fuller_turn is False
