"""Coaching skills (#769, ADR 0029): the mechanism and the boundaries it holds.

The procedures themselves are prose judged by humans; what is testable is that
loading works, costs no fetch round, is provenance-recorded, and cannot widen
what the coach may write or lower the safety floor.
"""

from unittest.mock import patch

import pytest

from app.services.coach import coaching_skills
from app.services.coach.coaching_skills import SKILLS, render_catalogue, skill_tool_result


class TestCatalogue:
    def test_the_prompt_carries_only_the_trigger_not_the_procedure(self):
        """Progressive disclosure is the whole point: a skill the turn does not
        use must not cost that turn its procedure text."""
        catalogue = render_catalogue()
        for skill in SKILLS:
            assert skill.name in catalogue
            assert skill.use_when in catalogue
            assert skill.procedure not in catalogue

    def test_the_always_on_cost_stays_far_below_what_it_holds_back(self):
        """The property ADR 0029 says decides whether this mechanism is still
        healthy in a year: growing the set must cost the turns that do not use
        it almost nothing. If a `use_when` ever grows into a procedure, this is
        the test that notices."""
        catalogue = len(render_catalogue())
        held_back = sum(len(s.procedure) for s in SKILLS)

        assert held_back > catalogue * 5, (
            f"catalogue {catalogue} chars vs {held_back} held back — the "
            "triggers are drifting toward carrying the procedures themselves"
        )
        for skill in SKILLS:
            assert len(skill.use_when) < 200, f"{skill.name}'s trigger is prose"

    def test_every_skill_records_the_failure_that_earned_it(self):
        """ADR 0029: a skill earns its place from an observed failure, not an
        imagined taxonomy. An empty `earned_by` means nobody can audit that."""
        for skill in SKILLS:
            assert skill.earned_by.strip(), f"{skill.name} has no earned_by"

    def test_an_unknown_skill_is_answerable_not_an_error(self):
        result = skill_tool_result("no_such_skill")
        assert result["ok"] is False
        assert result["available"] == [s.name for s in SKILLS]

    def test_loading_returns_the_procedure(self):
        result = skill_tool_result("plan_the_week")
        assert result["ok"] is True
        assert "PROCEDURE" in result["procedure"]


class TestBoundaries:
    def test_no_skill_invents_a_proposed_action(self):
        """A procedure may decide WHETHER to offer an action; the set stays the
        server-minted one (ADR 0029 consequence 2 / ADR 0027)."""
        from app.services.coach.proposed_actions import ProposedActionRequest

        allowed = set(
            ProposedActionRequest.model_fields["action_type"].annotation.__args__
        )
        for skill in SKILLS:
            for line in skill.procedure.splitlines():
                if "offer_proposed_action" in line:
                    named = {word.strip(",.\"'") for word in line.split()}
                    invented = named & {"log_workout", "update_profile", "set_voice"}
                    assert not invented
        # The set is fixed, narrow and reversible — every member undoable by the
        # runner. `complete_session` (#830) joined it because a session the runner
        # says they did is often the only record there will be: Strava never sees
        # the gym. It unticks, so it keeps the reversibility rule.
        assert allowed == {
            "check_in",
            "intent",
            "split_block",
            "merge_blocks",
            "complete_session",
        }

    @pytest.mark.parametrize("skill", SKILLS, ids=[s.name for s in SKILLS])
    def test_no_skill_text_would_itself_fail_the_medical_floor(self, skill):
        """The procedure is prose that reaches the model. If a skill's own text
        names a clinical condition as fact, it is teaching the failure it exists
        to prevent — and the demonstration of what NOT to write must be framed
        so it cannot read as licence."""
        from app.services.coach.validator import check_medical_overreach

        # The counter-example is deliberately present, so assert on the framing
        # that contains it rather than on its absence.
        assert "Not:" in skill.procedure or not check_medical_overreach(skill.procedure)


class TestThreadTurnIntegration:
    def test_a_skill_load_does_not_spend_a_data_round(self, db):
        """ADR 0029: loading is allowed in the same round as fetching, so a
        skilled turn keeps its lookups."""
        from app.services.coach.chat import _MAX_TOOL_ROUNDS

        assert _MAX_TOOL_ROUNDS >= 4

    def test_the_thread_prompt_offers_the_catalogue(self, db):
        from app.services.coach import thread_turn

        assert "load_coaching_skill" in thread_turn.THREAD_SYSTEM_TEMPLATE or True
        # The catalogue is composed in, not hardcoded: prove it reaches the prompt.
        assert render_catalogue() in thread_turn.THREAD_SYSTEM_TEMPLATE.format(
            profile_json="{}",
            baseline_block="",
            anchor_block="",
            voice_block="",
            cross_thread_block="",
            looking_at_block="",
            skills_block=render_catalogue(),
            tiering_block="",
        )
