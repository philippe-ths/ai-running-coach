"""The runner-facing rule STATEMENT, derived from `kind` + arguments (#844).

The defect this module closes: a `SpacingRule` carries a `kind` (whose predicate
in `rules.py` is what actually runs) and a `label` written by the coach LLM. The
two are independent — nothing ties them together — so a coach can write a label
that promises something its own predicate does not enforce. A live draft did
exactly that: `label="Full rest or easy walk only the day after the long run"`
against `kind=rest_day_after`, whose predicate forbids ANY non-rest session that
day (see `_rest_day_after`/`_working_days` in `rules.py`). The runner planned
from the label, logged the walk the label invited, and got a violation they could
not explain from what they had been told the rule was.

The fix is not to make the coach write better labels — an LLM's prose and a
predicate's logic can always drift apart again. It is to stop asking the coach to
describe the rule at all: `describe_rule` renders the runner-facing STATEMENT in
code, from the same `kind` + arguments the predicate itself reads, using
house-authored templates. The statement can therefore never claim more or less
than the predicate enforces, because it is generated from the predicate's own
inputs rather than typed independently.

`label` is kept — it still carries whatever the coach wrote — but it is
downgraded to a subordinate note: the coach's own phrasing/reasoning, rendered
alongside the derived statement and never in place of it. See
`app/schemas/schedule.py`'s `SpacingRuleRead`/`RuleViolation` and
`services/schedule/week.py` for where the two are threaded through to the API.

Pure function, no I/O, no LLM: the same house-floor idiom as `corpus.py`,
`coaching_skills.py` and `receipt.py` — text the runner reads that the coach
never gets to phrase.
"""

from typing import Any

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def _join(names: list) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _weekday_names(weekdays: Any) -> str:
    days = sorted({d for d in (weekdays or ()) if 0 <= d <= 6})
    return _join([_WEEKDAY_NAMES[d] for d in days])


def _rest_day_after_text(rule: Any) -> str:
    intent = getattr(rule, "intent", None) or "?"
    return f"Nothing but rest the day after a {intent} session."


def _no_intent_day_before_text(rule: Any) -> str:
    before = getattr(rule, "before_intent", None) or "?"
    target = getattr(rule, "target_intent", None) or "?"
    return f"No {before} session the day before a {target} session."


def _min_days_between_text(rule: Any) -> str:
    """APART, not "between".

    The predicate violates on `abs(day difference) < days`, so `days=3` permits
    Monday and Thursday — three days APART, but only two clear days BETWEEN
    them. "At least 3 days between" reads as the stricter Monday-to-Friday, and
    a statement that claims more than its predicate enforces is the very defect
    this module exists to close.
    """
    intent_a = getattr(rule, "intent_a", None) or "?"
    intent_b = getattr(rule, "intent_b", None) or "?"
    days = getattr(rule, "days", None)
    days_text = days if days is not None else "?"
    # `days=1` is valid (the schema allows 1..7), so the singular is reachable.
    noun = "day" if days == 1 else "days"
    if intent_a == intent_b:
        return f"At least {days_text} {noun} apart between {intent_a} sessions."
    return (
        f"At least {days_text} {noun} apart between a {intent_a} session and a "
        f"{intent_b} session."
    )


def _preferred_days_text(rule: Any) -> str:
    intent = getattr(rule, "intent", None) or "?"
    names = _weekday_names(getattr(rule, "weekdays", None))
    if not names:
        return f"{intent.capitalize()} sessions — no days set for this rule."
    return f"{intent.capitalize()} sessions only on {names}."


def _max_sessions_per_day_text(rule: Any) -> str:
    count = getattr(rule, "count", None)
    if count is None:
        return "At most an unset number of sessions in a day, not counting a prescribed rest."
    noun = "session" if count == 1 else "sessions"
    return f"At most {count} {noun} in a day, not counting a prescribed rest."


_TEMPLATES = {
    "rest_day_after": _rest_day_after_text,
    "no_intent_day_before": _no_intent_day_before_text,
    "min_days_between": _min_days_between_text,
    "preferred_days": _preferred_days_text,
    "max_sessions_per_day": _max_sessions_per_day_text,
}


def describe_rule(rule: Any) -> str:
    """The runner-facing statement of what this rule enforces.

    Derived from `rule.kind` plus its kind-specific arguments — never from
    `rule.label` — so the sentence a runner plans from can never promise more
    than `rules.PREDICATES[rule.kind]` actually checks. An unrecognised kind
    (defensive only: the write-time schema already rejects one, but
    `rules.check_rules` still has to handle a duck-typed rule at runtime) gets an
    honest fallback rather than falling back to the coach's label, which would
    reintroduce the exact mismatch this function exists to prevent.
    """
    kind = getattr(rule, "kind", None)
    template = _TEMPLATES.get(kind)
    if template is None:
        return f"A rule this app cannot check ({kind})."
    return template(rule)
