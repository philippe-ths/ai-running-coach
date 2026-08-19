"""coach_message_lean_grouped_v10 (#655): depth is earned.

With several sessions a day, every run was getting the same long write-up, because
report depth had nothing to track. The mechanism was hiding in plain sight: salience is
computed deterministically on every run and then dropped from the fuller LLM view, and
under the production receipt cadence the fuller turn is the ONLY LLM call there is. So
the report writer was told to vary its length and given nothing to vary it on.

v10 is two halves of one change. The manifest half re-admits `salience`, trimmed to
`novelty`. The prose half takes the depth rule out of the disposition list, states it as
its own clause beside DELIVERY, and shows it in a matched pair of worked examples.

This file states v10's own claims and touches no earlier version's test.
"""

from app.services.coach import prompt_clauses as clauses
from app.services.coach import prompts
from app.services.coach.prompt_features import PromptFeature as F
from app.services.coach.prompt_features import features_for
from app.services.coach.service import active_schema_version

V10 = "coach_message_lean_grouped_v10"
V9 = "coach_message_lean_grouped_v9"


def test_v10_swaps_the_salience_drop_for_the_salience_depth_trim():
    """The first version in this lineage to REPLACE a view flag rather than add one.

    The two say opposite things about the same section, so a version carrying both
    would leave the view's behaviour decided by branch order instead of by declaration.
    """
    added = features_for(V10) - features_for(V9)
    removed = features_for(V9) - features_for(V10)

    assert added == {F.SALIENCE_DEPTH}
    assert removed == {F.SALIENCE_DROPPED}


def test_no_version_carries_both_salience_flags():
    """The mutual exclusion, asserted over the whole manifest rather than the one pair
    it currently concerns — a later version inheriting a row by copy-paste is exactly
    how a contradiction like this ships."""
    for prompt_id in prompts.PROMPT_VERSIONS:
        features = features_for(prompt_id)
        assert not (
            F.SALIENCE_DEPTH in features and F.SALIENCE_DROPPED in features
        ), prompt_id


def test_v10_is_a_salience_depth_prompt_and_v9_is_not():
    assert prompts.is_salience_depth_prompt(V10) is True
    assert prompts.is_salience_depth_prompt(V9) is False
    assert prompts.is_salience_dropped_prompt(V10) is False
    assert prompts.is_salience_dropped_prompt(V9) is True


def test_the_depth_rule_left_the_disposition_list():
    """A relocation, not a growth. The sentence that governed the shape of every report
    was one line in a list of personality traits; leaving a copy behind would have made
    this an addition dressed up as a move."""
    v9 = prompts.build_system_prompt(V9, mode="fuller")
    v10 = prompts.build_system_prompt(V10, mode="fuller")

    moved = "An unremarkable run earns a couple of honest sentences; an interesting one earns more."
    assert moved in v9
    assert moved not in v10
    # The rest of that bullet stays exactly where it was.
    assert "I sound like a person, not a template." in v10


def test_the_depth_clause_states_what_earns_length_and_what_does_not():
    """The clause's load-bearing sentences. A depth rule that only said "be shorter"
    would buy brevity by losing the reports that matter."""
    text = clauses.DEPTH.text

    assert "# How much to say" in text
    assert "Depth is earned, not owed." in text
    assert "a first of its kind" in text
    assert "earns two or three sentences" in text
    assert '"Nothing much to say about this one" is a complete and useful thing' in text


def test_the_depth_clause_never_equates_a_hard_session_with_a_long_report():
    """The invariant the novelty signal is built on (novelty.py: salience is NOT
    intensity or load). Every earner the clause names is something the runner could not
    have seen for themselves; none of them is "the session was hard"."""
    text = clauses.DEPTH.text.lower()

    for intensity_word in ("hard session", "intensity", "effort_score", "how hard"):
        assert intensity_word not in text


def test_the_depth_clause_sits_after_the_floor_and_before_the_examples():
    """Position is the argument. Beside DELIVERY it finishes "stop when you have said
    what matters"; after SAFETY_FLOOR nothing in it can read as licence to trim a safety
    item; before the examples, the pair that follows demonstrates the rule just stated."""
    names = clauses.clause_names(V10)

    assert names.index("safety_floor") < names.index("depth")
    assert names.index("delivery") < names.index("depth")
    assert names.index("depth") < names.index("worked_examples_depth")
    # And it is nowhere near the disposition block it came out of.
    assert names.index("disposition_depth_relocated") < names.index("depth")


def test_the_worked_examples_show_the_contrast_rather_than_describing_it():
    """One short exemplar told the model that brevity exists. The pair shows the same
    coach on two of the same runner's sessions with the reason for the length visible in
    the long one, which is a thing to imitate rather than a rule to weigh."""
    text = clauses.WORKED_EXAMPLES_DEPTH.text

    assert "The same runner, two sessions apart." in text
    assert "Nothing in it I hadn't seen before, so it stays short:" in text
    assert "Something in it I couldn't have got from any of the others, so it earns the room:" in text
    # The long one's length is earned by a first-of-its-kind and a number that moved,
    # never by the session being hard.
    assert "First interval session you've done" in text
    # The short exemplar's own words survive the restructure.
    assert "Nothing else to say about this one; save it for tomorrow." in text


def test_the_depth_prose_reaches_v10_and_no_earlier_version():
    """Every live version except v10 keeps its prose byte-for-byte, so a rollback is a
    pure config flip and this experiment has exactly one subject."""
    for prompt_id in clauses.COMPOSED_PROMPT_IDS:
        text = prompts.build_system_prompt(prompt_id, mode="fuller")
        expected = prompt_id == V10
        assert (clauses.DEPTH.text in text) is expected, prompt_id
        assert (clauses.WORKED_EXAMPLES_DEPTH.text in text) is expected, prompt_id


def test_the_prose_and_the_signal_ship_together():
    """The depth prose spends a read only `PromptFeature.SALIENCE_DEPTH` serves. Half of
    this change without the other half is either an instruction with no signal or a
    signal nothing tells the coach to use."""
    for prompt_id in clauses.COMPOSED_PROMPT_IDS:
        carries_prose = clauses.ProseVariant.DEPTH_EARNED in clauses.PROSE_VARIANTS[prompt_id]
        carries_signal = F.SALIENCE_DEPTH in features_for(prompt_id)
        assert carries_prose is carries_signal, prompt_id


def test_v10_carries_the_safety_floor_in_both_modes():
    """Structural, not remembered: `compose` refuses a clause set with no floor, and
    v10's set is composed like every other."""
    assert clauses.SAFETY_FLOOR in clauses.fuller_clauses(V10)
    assert clauses.OPENER_SAFETY_FLOOR in clauses.opener_clauses(V10)
    assert clauses.SAFETY_FLOOR.text in prompts.build_system_prompt(V10, mode="fuller")
    assert clauses.OPENER_SAFETY_FLOOR.text in prompts.build_system_prompt(V10, mode="opener")


def test_v10_leaves_the_opener_exactly_as_v9_wrote_it():
    """The opener is a brief immediate reaction and already had its own depth line; it
    carries no disposition clause and gains nothing here."""
    assert prompts.build_system_prompt(V10, mode="opener") == prompts.build_system_prompt(
        V9, mode="opener"
    )


def test_v10_keeps_v9s_capabilities_the_schema_version_and_the_cadence():
    """Everything else about the pack and the cache identity is v9's, so the flip is a
    config change and the rollback is one too."""
    unchanged = features_for(V9) - {F.SALIENCE_DROPPED}
    assert unchanged <= features_for(V10)
    assert active_schema_version(V10) == active_schema_version(V9)
    assert F.TWO_STAGE in features_for(V10)
    assert F.GROUPED_PACK in features_for(V10)
    assert F.SCHEDULE in features_for(V10)
    assert F.BODY in features_for(V10)


def test_v10_is_registered_as_a_composed_prompt():
    assert V10 in clauses.COMPOSED_PROMPT_IDS
    assert V10 in prompts.PROMPT_VERSIONS
