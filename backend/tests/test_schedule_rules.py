"""#830: spacing rules — the actual content of a flexible plan.

For a runner whose sessions float, "no quality run the day before the long run"
IS the plan. This file pins that each of the five closed rule kinds has a
predicate that both passes and BITES, that the four rules the design names are
all expressible in that vocabulary, that a week the rules cannot satisfy is
reported as unsatisfiable with the blocking rule NAMED, and that a rule kind the
checker does not understand is reported rather than silently passed.

Weekday numbers throughout are Python's own (0 = Monday .. 6 = Sunday).

All row data is synthetic test setup (exercises code paths; represents no real
runner).
"""

import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.schedule import SpacingRule
from app.services.schedule.rules import RULE_KINDS, check_rules, find_assignment

MON = date(2026, 8, 10)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)
THU = MON + timedelta(days=3)
FRI = MON + timedelta(days=4)
SAT = MON + timedelta(days=5)
SUN = MON + timedelta(days=6)

# The design's four rules, in the vocabulary. Named here once so every test that
# uses one reads as the runner-facing sentence it stands for.
NO_QUALITY_BEFORE_LONG = SpacingRule(
    kind="no_intent_day_before",
    label="No quality run the day before the long run",
    before_intent="quality",
    target_intent="long",
)
NO_HEAVY_LEGS_BEFORE_QUALITY = SpacingRule(
    kind="no_intent_day_before",
    label="No heavy legs the day before a quality run",
    before_intent="strength",
    target_intent="quality",
)
REST_AFTER_LONG = SpacingRule(
    kind="rest_day_after",
    label="A full rest day after the long run",
    intent="long",
)
LONG_NEEDS_A_FREE_MORNING = SpacingRule(
    kind="preferred_days",
    label="Long run needs a free morning — Sat or Sun",
    intent="long",
    weekdays=[5, 6],
)


def _session(intent, start, end=None, *, session_id=None):
    """A duck-typed session: the checker reads id, intent and the window."""
    return SimpleNamespace(
        id=session_id or f"{intent}-{start.isoformat()}",
        intent=intent,
        window_start=start,
        window_end=end or start,
    )


def _kinds(violations):
    return [v["kind"] for v in violations]


def _labels(violations):
    return [v["label"] for v in violations]


# --- one passing and one violating case per kind ---------------------------


def test_rest_day_after_passes_and_bites():
    rules = [REST_AFTER_LONG]

    ok, violations = check_rules(
        [_session("long", SAT), _session("easy", MON)], rules
    )
    assert (ok, violations) == (True, [])

    ok, violations = check_rules(
        [_session("long", SAT), _session("easy", SUN)], rules
    )
    assert ok is False
    assert _kinds(violations) == ["rest_day_after"]


def test_a_prescribed_rest_is_not_work_so_it_satisfies_rest_day_after():
    """A rest CARD the day after the long run is the rule being honoured, not
    broken — the day after carries no session, it carries an instruction."""
    ok, violations = check_rules(
        [_session("long", SAT), _session("rest", SUN)], [REST_AFTER_LONG]
    )

    assert (ok, violations) == (True, [])


def test_no_intent_day_before_passes_and_bites():
    rules = [NO_QUALITY_BEFORE_LONG]

    ok, violations = check_rules(
        [_session("quality", THU), _session("long", SUN)], rules
    )
    assert (ok, violations) == (True, [])

    ok, violations = check_rules(
        [_session("quality", SAT), _session("long", SUN)], rules
    )
    assert ok is False
    assert _kinds(violations) == ["no_intent_day_before"]


def test_min_days_between_passes_and_bites():
    rules = [
        SpacingRule(
            kind="min_days_between",
            label="Three days between hard sessions",
            intent_a="quality",
            intent_b="long",
            days=3,
        )
    ]

    ok, violations = check_rules(
        [_session("quality", WED), _session("long", SAT)], rules
    )
    assert (ok, violations) == (True, [])

    ok, violations = check_rules(
        [_session("quality", FRI), _session("long", SAT)], rules
    )
    assert ok is False
    assert _kinds(violations) == ["min_days_between"]


def test_preferred_days_passes_and_bites():
    rules = [LONG_NEEDS_A_FREE_MORNING]

    ok, violations = check_rules([_session("long", SAT)], rules)
    assert (ok, violations) == (True, [])

    ok, violations = check_rules([_session("long", WED)], rules)
    assert ok is False
    assert _kinds(violations) == ["preferred_days"]


def test_max_sessions_per_day_passes_and_bites():
    rules = [
        SpacingRule(
            kind="max_sessions_per_day", label="One session a day", count=1
        )
    ]

    ok, violations = check_rules(
        [_session("easy", WED), _session("quality", THU)], rules
    )
    assert (ok, violations) == (True, [])

    ok, violations = check_rules(
        [_session("easy", WED), _session("quality", WED)], rules
    )
    assert ok is False
    assert _kinds(violations) == ["max_sessions_per_day"]


def test_a_rest_card_does_not_use_up_a_days_session_allowance():
    ok, violations = check_rules(
        [_session("easy", WED), _session("rest", WED)],
        [SpacingRule(kind="max_sessions_per_day", label="One a day", count=1)],
    )

    assert (ok, violations) == (True, [])


# --- the four rules the design is actually made of -------------------------


def test_the_designs_four_rules_hold_together_on_a_legal_week():
    week = [
        _session("strength", MON),
        _session("quality", WED),
        _session("easy", FRI),
        _session("long", SAT),
        _session("rest", SUN),
    ]

    ok, violations = check_rules(
        week,
        [
            NO_QUALITY_BEFORE_LONG,
            NO_HEAVY_LEGS_BEFORE_QUALITY,
            REST_AFTER_LONG,
            LONG_NEEDS_A_FREE_MORNING,
        ],
    )

    assert (ok, violations) == (True, [])


@pytest.mark.parametrize(
    "week, rule",
    [
        # A quality session on the Saturday before a Sunday long run.
        ([_session("quality", SAT), _session("long", SUN)], NO_QUALITY_BEFORE_LONG),
        # Heavy legs on the Tuesday before a Wednesday quality session.
        (
            [_session("strength", TUE), _session("quality", WED)],
            NO_HEAVY_LEGS_BEFORE_QUALITY,
        ),
        # An easy run on the day after the long run.
        ([_session("long", SAT), _session("easy", SUN)], REST_AFTER_LONG),
        # The long run stranded midweek, where the runner has no free morning.
        ([_session("long", WED)], LONG_NEEDS_A_FREE_MORNING),
    ],
)
def test_each_of_the_designs_four_rules_is_enforceable_not_just_readable(week, rule):
    """Each rule is carried with a runner-facing label, but it is the PREDICATE
    that is enforced — so each sentence has to actually catch its own breach."""
    ok, violations = check_rules(week, [rule])

    assert ok is False
    assert _kinds(violations) == [rule.kind]
    assert _labels(violations) == [rule.label]


# --- the flexible week: does a legal arrangement EXIST ---------------------


def test_a_realistic_floating_week_finds_a_feasible_arrangement():
    """Most of this week has not been placed yet, so the honest question is not
    "does this arrangement break a rule" but "does a legal one exist"."""
    week = [
        _session("long", SAT, SUN),
        _session("quality", TUE, THU),
        _session("easy", MON, FRI),
        _session("easy", MON, SUN),
    ]
    rules = [NO_QUALITY_BEFORE_LONG, REST_AFTER_LONG, LONG_NEEDS_A_FREE_MORNING]

    ok, violations = check_rules(week, rules)

    assert (ok, violations) == (True, [])

    placement = find_assignment(week, rules)
    assert placement is not None
    assert len(placement) == len(week)
    for placed in placement:
        original = next(s for s in week if s.id == placed.session_id)
        assert original.window_start <= placed.day <= original.window_end


def test_an_impossible_week_is_unsatisfiable_and_names_the_blocking_rule():
    """A pinned Sunday long run and a pinned Saturday quality session cannot both
    stand alongside "no quality the day before the long run". Nothing can be
    moved, so the week is reported unsatisfiable — and the rule actually in the
    way is named rather than the whole rule set being dumped on the runner."""
    week = [_session("long", SUN), _session("quality", SAT)]
    rules = [NO_QUALITY_BEFORE_LONG, LONG_NEEDS_A_FREE_MORNING, REST_AFTER_LONG]

    ok, violations = check_rules(week, rules)

    assert ok is False
    assert _kinds(violations) == ["no_intent_day_before"]
    assert _labels(violations) == ["No quality run the day before the long run"]
    assert find_assignment(week, rules) is None


# --- a rule nobody can evaluate -------------------------------------------


def test_an_unknown_rule_kind_is_reported_never_silently_passed():
    """Silently passing a rule nobody can evaluate is how a plan comes to claim a
    discipline it never had."""
    unknown = SimpleNamespace(
        kind="phase_of_the_moon", label="Long runs only on a waxing moon"
    )

    ok, violations = check_rules([_session("long", SAT)], [unknown])

    assert ok is False
    assert _kinds(violations) == ["phase_of_the_moon"]
    assert violations[0]["detail"]


def test_an_unknown_rule_kind_cannot_reach_the_store_in_the_first_place():
    """The report above is the second line of defence; coercion is the first."""
    assert "phase_of_the_moon" not in RULE_KINDS
    with pytest.raises(ValidationError):
        SpacingRule.model_validate(
            {"kind": "phase_of_the_moon", "label": "Long runs on a waxing moon"}
        )


def test_an_unknown_rule_is_reported_alongside_the_week_it_could_not_constrain():
    """The known rules are still checked; the unknown one is reported on top."""
    ok, violations = check_rules(
        [_session("long", WED)],
        [
            LONG_NEEDS_A_FREE_MORNING,
            SimpleNamespace(kind="phase_of_the_moon", label="Waxing moon only"),
        ],
    )

    assert ok is False
    assert set(_kinds(violations)) == {"phase_of_the_moon", "preferred_days"}


# --- the schema's own guard ------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "rest_day_after", "label": "rest after"},
        {"kind": "no_intent_day_before", "label": "not before", "before_intent": "quality"},
        {"kind": "min_days_between", "label": "spaced", "intent_a": "quality", "intent_b": "long"},
        {"kind": "preferred_days", "label": "weekend", "intent": "long"},
        {"kind": "max_sessions_per_day", "label": "one a day"},
    ],
)
def test_a_rule_missing_its_kinds_required_argument_is_rejected(payload):
    with pytest.raises(ValidationError):
        SpacingRule.model_validate(payload)


def test_weekdays_outside_monday_to_sunday_are_rejected():
    with pytest.raises(ValidationError):
        SpacingRule.model_validate(
            {
                "kind": "preferred_days",
                "label": "Long run on day seven",
                "intent": "long",
                "weekdays": [7],
            }
        )


# --- sanity: the search settles on a week-sized problem --------------------


def test_a_week_of_eight_freely_floating_sessions_resolves_quickly():
    """A week has seven days and a handful of sessions; the search must settle
    immediately rather than exploring the whole product of the domains."""
    week = [
        _session("long", MON, SUN, session_id="long"),
        _session("quality", MON, SUN, session_id="quality"),
        *[
            _session("easy", MON, SUN, session_id=f"easy-{index}")
            for index in range(6)
        ],
    ]
    rules = [
        NO_QUALITY_BEFORE_LONG,
        REST_AFTER_LONG,
        LONG_NEEDS_A_FREE_MORNING,
        SpacingRule(
            kind="max_sessions_per_day", label="At most two a day", count=2
        ),
    ]

    started = time.monotonic()
    ok, violations = check_rules(week, rules)
    elapsed = time.monotonic() - started

    assert (ok, violations) == (True, [])
    assert elapsed < 2.0, f"the week search took {elapsed:.2f}s"
