"""#844: the runner-facing rule STATEMENT is derived from `kind` + arguments,
never from the coach's `label`.

The defect: a `SpacingRule` carries a `kind` (what the predicate in `rules.py`
actually enforces) and a `label` written by the coach LLM, with nothing tying
the two together. A live draft's label read "Full rest or easy walk only the
day after the long run" against `kind=rest_day_after`, whose predicate forbids
ANY non-rest session that day — so a runner who planned from the label and
logged the walk it invited got a violation they could not explain.

These tests pin `describe_rule` (`services/schedule/rule_text.py`) as faithful
to each predicate, and pin that it is generated from `kind` + arguments alone —
never from `label` — so the statement a runner reads can never promise more
than the checker enforces.

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.schemas.schedule import SpacingRule
from app.services.schedule.rule_text import describe_rule
from app.services.schedule.rules import check_rules

MON = date(2026, 8, 10)


def _session(intent, start, end=None, *, session_id=None):
    """A duck-typed session: the checker reads id, intent and the window."""
    return SimpleNamespace(
        id=session_id or f"{intent}-{start.isoformat()}",
        intent=intent,
        window_start=start,
        window_end=end or start,
    )

# --- faithfulness: one statement per kind, checked against its predicate ----


def test_rest_day_after_statement_permits_nothing_but_rest():
    """The exact #844 defect: the statement must not read as inviting a walk,
    a jog, or any other non-rest session on the day after."""
    rule = SpacingRule(
        kind="rest_day_after", label="whatever the coach happened to write", intent="long"
    )

    statement = describe_rule(rule)

    assert statement == "Nothing but rest the day after a long session."
    # Named explicitly because it is THE thing the live label got wrong; the
    # equality above already pins the rest. (A bare `"or" not in statement`
    # substring check was tempting here and is a trap — it fires on "morning",
    # "workout" or "before" for reasons that have nothing to do with the rule.)
    assert "walk" not in statement.lower()


def test_no_intent_day_before_statement_names_both_intents_and_the_direction():
    rule = SpacingRule(
        kind="no_intent_day_before",
        label="anything",
        before_intent="quality",
        target_intent="long",
    )

    assert describe_rule(rule) == "No quality session the day before a long session."


def test_min_days_between_statement_names_the_gap_and_both_intents():
    rule = SpacingRule(
        kind="min_days_between",
        label="anything",
        intent_a="quality",
        intent_b="long",
        days=3,
    )

    assert (
        describe_rule(rule)
        == "At least 3 days apart between a quality session and a long session."
    )


def test_min_days_between_statement_collapses_to_one_intent_when_both_sides_match():
    rule = SpacingRule(
        kind="min_days_between",
        label="anything",
        intent_a="quality",
        intent_b="quality",
        days=3,
    )

    assert describe_rule(rule) == "At least 3 days apart between quality sessions."


def test_min_days_between_statement_uses_the_singular_for_a_one_day_gap():
    """`days=1` is inside the schema's 1..7 range, so "1 days" is reachable."""
    rule = SpacingRule(
        kind="min_days_between",
        label="anything",
        intent_a="quality",
        intent_b="quality",
        days=1,
    )

    assert describe_rule(rule) == "At least 1 day apart between quality sessions."


def test_preferred_days_statement_names_the_intent_and_the_allowed_weekdays():
    rule = SpacingRule(
        kind="preferred_days", label="anything", intent="long", weekdays=[5, 6]
    )

    assert describe_rule(rule) == "Long sessions only on Saturday and Sunday."


def test_preferred_days_statement_lists_three_or_more_weekdays_without_an_oxford_comma():
    rule = SpacingRule(
        kind="preferred_days", label="anything", intent="quality", weekdays=[1, 3, 5]
    )

    assert describe_rule(rule) == "Quality sessions only on Tuesday, Thursday and Saturday."


def test_preferred_days_statement_sorts_weekdays_into_week_order_regardless_of_input_order():
    rule = SpacingRule(
        kind="preferred_days", label="anything", intent="long", weekdays=[6, 5]
    )

    assert describe_rule(rule) == "Long sessions only on Saturday and Sunday."


def test_max_sessions_per_day_statement_uses_singular_for_one():
    rule = SpacingRule(kind="max_sessions_per_day", label="anything", count=1)

    assert (
        describe_rule(rule)
        == "At most 1 session in a day, not counting a prescribed rest."
    )


def test_max_sessions_per_day_statement_uses_plural_for_more_than_one():
    rule = SpacingRule(kind="max_sessions_per_day", label="anything", count=2)

    assert (
        describe_rule(rule)
        == "At most 2 sessions in a day, not counting a prescribed rest."
    )


# --- derived from kind + args, never from label -----------------------------


def test_the_statement_is_identical_for_two_rules_sharing_kind_and_arguments_despite_wildly_different_labels():
    """The core #844 property: the label is decorative to the statement. Two
    rules with the same kind and arguments must produce the same statement no
    matter what the coach wrote as `label`."""
    honest = SpacingRule(
        kind="rest_day_after",
        label="A full rest day after the long run",
        intent="long",
    )
    misleading = SpacingRule(
        kind="rest_day_after",
        label="Full rest or easy walk only the day after the long run",
        intent="long",
    )

    assert describe_rule(honest) == describe_rule(misleading)


def test_the_844_regression_a_runner_following_the_labels_advice_cannot_be_told_that_advice_is_the_rule():
    """The real incident, made concrete as an acceptance criterion: a
    `rest_day_after` rule whose stored label is the actual misleading one from
    #844 must present a statement that does not invite a walk, and the
    violation the checker raises must carry that honest statement rather than
    the label that invited the walk.

    Note on the mechanism, because #844's own write-up gets it slightly wrong:
    the checker only ever sees PLANNED sessions (`week.build_week` feeds it
    `upcoming` `PlannedSession` rows), so LOGGING a walk cannot raise a
    violation — nothing turns a logged activity into a planned session. The
    harm is upstream of that: the runner is told a rule permits something their
    plan can never contain, since `plan_validator` would reject a draft that
    scheduled it. The session below is therefore a PLANNED easy session, which
    is the shape the predicate actually judges.
    """
    rule = SpacingRule(
        kind="rest_day_after",
        label="Full rest or easy walk only the day after the long run",
        intent="long",
    )
    long_day = MON + timedelta(days=5)  # Saturday
    walk_day = long_day + timedelta(days=1)  # Sunday, PINNED so it cannot move
    week = [_session("long", long_day), _session("easy", walk_day)]

    ok, violations = check_rules(week, [rule])

    assert ok is False
    assert len(violations) == 1
    statement = violations[0]["statement"]
    assert statement == "Nothing but rest the day after a long session."
    assert "walk" not in statement.lower()
    # The misleading label is still carried (it is the coach's note, not
    # deleted), but it is not what the violation's statement says.
    assert violations[0]["label"] == rule.label
    assert violations[0]["statement"] != violations[0]["label"]


# --- the statement AGREES WITH THE PREDICATE, not just with a literal --------
#
# Every test above pins `describe_rule` against a string the author typed, which
# is exactly the wrong oracle for this module: a template that misdescribes its
# own predicate stays green forever, and one did — `min_days_between` read "at
# least 3 days between" when its predicate (`abs(diff) < days`) permits Monday
# and Thursday, three days APART with only two clear days between. The tests
# below couple the two: build the placement the STATEMENT says is legal and
# assert `check_rules` agrees, then the one it says is illegal and assert it
# bites.
#
# Be honest about what this buys. No test can mechanically prove a sentence
# matches a predicate — the reading is human either way, and these tests encode
# one reading rather than deriving it. What the PAIR gives is that silent drift
# becomes impossible: change the wording and the literal tests above fail,
# change the predicate and the tests below fail, so either edit forces someone
# to look at the other half. Neither set alone would have caught the defect
# that prompted them.


def _days(*offsets):
    return [MON + timedelta(days=o) for o in offsets]


def test_min_days_between_permits_exactly_the_gap_its_statement_claims():
    rule = SpacingRule(
        kind="min_days_between",
        label="anything",
        intent_a="quality",
        intent_b="quality",
        days=3,
    )
    mon, wed, thu = _days(0, 2, 3)

    # "At least 3 days apart" -> Mon + Thu is 3 apart, so it must be legal.
    ok, _ = check_rules([_session("quality", mon), _session("quality", thu)], [rule])
    assert ok is True

    # ...and Mon + Wed is only 2 apart, so it must bite.
    ok, violations = check_rules(
        [_session("quality", mon), _session("quality", wed)], [rule]
    )
    assert ok is False
    assert violations[0]["kind"] == "min_days_between"


def test_rest_day_after_permits_a_rest_card_and_nothing_else_as_its_statement_claims():
    rule = SpacingRule(kind="rest_day_after", label="anything", intent="long")
    sat, sun = _days(5, 6)

    ok, _ = check_rules([_session("long", sat), _session("rest", sun)], [rule])
    assert ok is True, "'Nothing but rest' must actually permit a rest card"

    ok, _ = check_rules([_session("long", sat), _session("easy", sun)], [rule])
    assert ok is False, "'Nothing but rest' must forbid an easy session"


def test_max_sessions_per_day_does_not_count_a_rest_as_its_statement_claims():
    rule = SpacingRule(kind="max_sessions_per_day", label="anything", count=2)
    (mon,) = _days(0)

    # "not counting a prescribed rest" -> two sessions plus a rest is legal.
    ok, _ = check_rules(
        [
            _session("easy", mon, session_id="a"),
            _session("quality", mon, session_id="b"),
            _session("rest", mon, session_id="c"),
        ],
        [rule],
    )
    assert ok is True

    ok, _ = check_rules(
        [
            _session("easy", mon, session_id="a"),
            _session("quality", mon, session_id="b"),
            _session("long", mon, session_id="c"),
        ],
        [rule],
    )
    assert ok is False


def test_preferred_days_permits_exactly_the_weekdays_its_statement_names():
    rule = SpacingRule(
        kind="preferred_days", label="anything", intent="long", weekdays=[5, 6]
    )
    fri, sat = _days(4, 5)

    ok, _ = check_rules([_session("long", sat)], [rule])
    assert ok is True, "Saturday is named in the statement"

    ok, _ = check_rules([_session("long", fri)], [rule])
    assert ok is False, "Friday is not named in the statement"


def test_no_intent_day_before_bites_only_on_the_day_its_statement_names():
    rule = SpacingRule(
        kind="no_intent_day_before",
        label="anything",
        before_intent="quality",
        target_intent="long",
    )
    fri, sat, sun = _days(4, 5, 6)

    ok, _ = check_rules([_session("quality", fri), _session("long", sun)], [rule])
    assert ok is True, "two days before is not 'the day before'"

    ok, _ = check_rules([_session("quality", sat), _session("long", sun)], [rule])
    assert ok is False


# --- unknown kind: an honest fallback, never the label -----------------------


def test_an_unrecognised_kind_gets_an_honest_fallback_never_the_label():
    rule = SimpleNamespace(
        kind="phase_of_the_moon", label="Long runs only on a waxing moon"
    )

    statement = describe_rule(rule)

    assert "phase_of_the_moon" in statement
    assert statement != rule.label
    assert "waxing moon" not in statement.lower()


# --- defensive edges: no crash ----------------------------------------------


def test_an_empty_weekday_list_does_not_crash_the_preferred_days_template():
    rule = SimpleNamespace(kind="preferred_days", label="anything", intent="long", weekdays=[])

    statement = describe_rule(rule)

    assert isinstance(statement, str)
    assert statement


def test_a_missing_weekdays_attribute_does_not_crash_the_preferred_days_template():
    rule = SimpleNamespace(kind="preferred_days", label="anything", intent="long")

    statement = describe_rule(rule)

    assert isinstance(statement, str)
    assert statement
