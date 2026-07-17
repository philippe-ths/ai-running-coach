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
