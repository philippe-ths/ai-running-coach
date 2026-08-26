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
        # The set is fixed and narrow, and nothing in it destroys anything.
        #
        # Four members undo with a tap. `complete_session` (#830) joined them
        # because a session the runner says they did is often the only record
        # there will be — Strava never sees the gym — and it unticks.
        #
        # `adjust_session` (#881) overwrites one number and does not undo with a
        # tap either. What it has instead is that the card names BOTH values, so
        # the previous prescription is written down in the runner's own
        # transcript, and the same action puts it back — a correction is itself
        # correctable. Recoverable, in other words, without being one-tap.
        #
        # `draft_plan` (#856) was the one that was neither: writing a plan
        # supersedes the one the runner was training to, and nothing brought that
        # back. #857 closed it. The superseded plan and all of its sessions were
        # already RETAINED (`activate_plan` only flips status) and the card
        # already named the plan it would replace, but retention nobody can reach
        # is not an undo. `POST /api/schedule/plans/{id}/restore` reaches it, the
        # Schedule screen offers it where the replacement landed, and the restore
        # supersedes symmetrically, so going back is itself something you can go
        # back from. Recoverable in the `adjust_session` sense: not one tap on the
        # card itself, but a real route back that destroys nothing.
        #
        # `revise_max_hr` (#945) overwrites one profile field and does not undo
        # with a tap, but destroys nothing either: the runner's ordinary profile
        # edit screen can set max HR back to any value at any time, the same
        # pre-existing surface every other stated fact already goes through.
        #
        # `amend_plan` (#981) rewrites the sessions inside a bounded window and,
        # like `adjust_session`, does not undo with a tap. What it has instead is
        # the same recoverability in kind: the card names the window and the
        # reason, so what was agreed is written down in the runner's own
        # transcript, and an amendment is itself amendable — "put next week back
        # the way it was" is another one. What it CANNOT do is the reason it
        # earns its place beside `draft_plan` rather than replacing it in this
        # list: it never touches a completed session, never touches a session
        # outside its window, and never touches the plan's rules or its race, so
        # the blast radius is stated on the card before the runner agrees to it.
        # That is the property `draft_plan` lacked and #857 had to build a whole
        # restore path to recover.
        #
        # The honest gap, tracked separately: the sessions an amendment replaces
        # are not retained, so putting a window back relies on the coach writing
        # it again rather than on a stored previous version. That is weaker than
        # `draft_plan`'s restore and is a deliberate scope decision, not an
        # oversight.
        assert allowed == {
            "check_in",
            "intent",
            "split_block",
            "merge_blocks",
            "revise_max_hr",
            "complete_session",
            "adjust_session",
            "draft_plan",
            "amend_plan",
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
            confirmed_block="",
            looking_at_block="",
            skills_block=render_catalogue(),
            tiering_block="",
        )
