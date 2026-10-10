"""The runner's own scheduling rules, merged with the plan's (#1080).

A plan's rules are the coach's and are rewritten wholesale on every redraft
(`draft.py` replaces `plan.rules`), so a limit the RUNNER sets cannot live
there: the next redraft would drop it. It lives on the runner's profile and is
added to the plan's rules at every place rules are checked or shown, so the week
view, the draft and amend checks, repair and the coach's view all see the same
set.

Only one runner rule exists today: the most activities in a day, as a
`max_sessions_per_day` rule. That predicate counts every non-rest session,
walks included, which is what the runner asked for: a walk is an activity like
any other.
"""

from typing import Any, List, Sequence

from app.schemas.schedule import SpacingRule
from app.services.schedule.rule_text import describe_rule

# An absurdity floor on how many sessions may land on one day. Every other
# nonsense has a floor; without this one a week could pin all twenty-one permitted
# sessions to a Tuesday unless the coach happened to write a rule against it, and
# "the model polices itself" is not a check. Deliberately high: three sessions in
# a day is a real thing this runner does (a walk, a run and a gym session), so
# this catches the impossible rather than expressing an opinion. It also caps the
# runner's own daily limit: a setting above it would promise days the plan
# checker rejects anyway.
ABSURD_SESSIONS_PER_DAY = 4

# Read by the runner, under the rule on their schedule, and by the coach when a
# drafted week breaks it (the rewrite feedback quotes the label). Neutral wording
# so it reads true to both; the coach's planning view uses `describe_for_coach`.
RUNNER_DAILY_LIMIT_LABEL = "Daily limit set by the runner; walks count as activities"


def runner_rules(profile: Any) -> List[SpacingRule]:
    """The rules the runner has set for themselves, or none."""
    limit = getattr(profile, "max_activities_per_day", None) if profile else None
    if not limit:
        return []
    return [
        SpacingRule(
            kind="max_sessions_per_day",
            label=RUNNER_DAILY_LIMIT_LABEL,
            source="runner",
            count=limit,
        )
    ]


def with_runner_rules(rules: Sequence[Any], profile: Any) -> List[Any]:
    """The runner's own rules, then the plan's.

    Runner rules come first because some readers cap the list (the coach's view
    keeps `MAX_RULES`), and the rule the coach may not change is the last one
    that should fall off it.
    """
    return runner_rules(profile) + list(rules)


def describe_for_coach(rule: Any) -> str:
    """The derived statement, plus whose rule it is when it is the runner's.

    A bare "at most 3 sessions in a day" leaves two things to be guessed: that
    the coach may not relax it, and that a walk counts toward it. Both are
    stated in code from the rule's `source` and `kind`, never from a label, so
    the coach cannot be told more than the predicate enforces (#844).
    """
    statement = describe_rule(rule)
    if getattr(rule, "source", None) != "runner":
        return statement
    note = "The runner set this themselves: plan within it and never change it."
    if getattr(rule, "kind", None) == "max_sessions_per_day":
        note += " Walks count toward it like any other activity."
    return f"{statement} {note}"
