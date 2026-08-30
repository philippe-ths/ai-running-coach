"""The single definition of "the week" (#676).

Historically every coach-pack and Trends surface open-coded the same
hardcoded-Monday idiom (`d - timedelta(days=d.weekday())`). That made the
week boundary impossible to change per-runner without editing six places and
risking drift between the coach pack and the Trends API. This module is the
one place that turns a date into its week boundary, parameterized by the
runner's chosen `week_starts_on`.

`week_starts_on` is an int in Python's `weekday()` space: 0=Monday .. 6=Sunday.
`starts_on=MONDAY` (the default) reproduces the prior idiom byte-for-byte, so a
runner who has not chosen otherwise sees byte-identical output. The product only
offers Monday or Sunday, but the arithmetic here is general (any weekday) so the
math is trivially testable and carries no special-cases.
"""

from datetime import date, timedelta
from typing import Any

MONDAY = 0
SUNDAY = 6


def resolve_week_start(profile: Any) -> int:
    """The effective week start for a runner, from their profile.

    Null (unset) or a missing profile resolves to Monday, keeping every existing
    runner's output byte-identical. Duck-typed on ``.week_starts_on`` so the pure
    week-math module stays free of an ORM import.
    """
    value = getattr(profile, "week_starts_on", None)
    return MONDAY if value is None else value


def _offset(d: date, starts_on: int) -> int:
    """Days from d back to the start of its week (0..6)."""
    return (d.weekday() - starts_on) % 7


def week_start(d: date, starts_on: int = MONDAY) -> date:
    """The first day of the week containing d, given the week starts on `starts_on`."""
    return d - timedelta(days=_offset(d, starts_on))


def days_into_week(d: date, starts_on: int = MONDAY) -> int:
    """1-based position of d within its week (1 = the week's first day .. 7 = its last)."""
    return _offset(d, starts_on) + 1


def is_last_day_of_week(d: date, starts_on: int = MONDAY) -> bool:
    """True when d is the final day of its week (the day before the next week_start)."""
    return _offset(d, starts_on) == 6


def week_end(d: date, starts_on: int = MONDAY) -> date:
    """The last day of the week containing d."""
    return week_start(d, starts_on) + timedelta(days=6)


def describe_week_span(d: date, starts_on: int = MONDAY) -> str:
    """The week containing d, written so a model does not have to derive it.

    "Mon 2026-08-31 to Sun 2026-09-06".

    A prompt that gives a model only a week's first day is asking it to do
    calendar arithmetic, and it gets it wrong (#1001). Told the plan's rule
    "long run on Saturday or Sunday" and the bare start `2026-08-31`, the
    amendment twice windowed the long run `2026-09-06..2026-09-07` - the Sunday
    of one week and the Monday of the next, which is the exact pair the prompt
    uses as its counter-example. It knew the rule and could not resolve the days
    to dates. Naming both ends anchors the weekday-to-date mapping at each edge,
    which is where the mistakes land.
    """
    first = week_start(d, starts_on)
    last = first + timedelta(days=6)
    return (
        f"{first.strftime('%a')} {first.isoformat()} "
        f"to {last.strftime('%a')} {last.isoformat()}"
    )
