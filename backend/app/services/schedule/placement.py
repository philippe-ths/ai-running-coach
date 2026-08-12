"""Placement, effective window and status — all derived, none stored (#830).

A planned session stores one thing about where it sits: an inclusive
`[window_start, window_end]`. Everything the screen shows about placement is
computed from that pair and today's date.

Why derive rather than store
----------------------------
A `placement` column would be a second copy of a fact the dates already carry,
and the two could disagree — the mode would say "pinned" while the window said
Thursday to Saturday, and nothing would say which was true. Deriving also gives
the design's narrowing behaviour for free: as days pass, the EFFECTIVE window
starts at today, so "window narrowed from Thu-Sat" is a comparison between the
stored window and the effective one. No job runs per session to make that
happen, and the stored window never moves, so a session can always say what it
was originally for.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional, Tuple

from app.services.weeks import MONDAY, week_start

WEEK_LENGTH_DAYS = 7


def validate_session_window(
    window_start: date, window_end: date, starts_on: int = MONDAY
) -> None:
    """Raise `ValueError` unless the window is ordered and inside ONE week.

    Two invariants the rest of the package relies on and that nothing else
    enforces, so they are enforced HERE and every writer calls this:

    - Ordered. An inverted window would read as `pinned` (width <= 0) while
      simultaneously having no days left at all, so a session could be pinned
      and missed at the same time.
    - Within one week. `store.sessions_in_range` places a session in a week by
      its `window_start`, and the horizon rolls weeks up on that basis; a window
      spilling past the week boundary would put a session's load in one week and
      its actual day in another.

    There is no writer in this slice — the coach's drafting path is the first —
    which is exactly why this ships now rather than being remembered later.
    """
    if window_start > window_end:
        raise ValueError(
            f"window_start {window_start} is after window_end {window_end}"
        )
    if window_end - window_start >= timedelta(days=WEEK_LENGTH_DAYS):
        raise ValueError(
            f"window {window_start}..{window_end} spans more than one week"
        )
    if week_start(window_start, starts_on) != week_start(window_end, starts_on):
        raise ValueError(
            f"window {window_start}..{window_end} crosses a week boundary"
        )


def derive_placement(
    window_start: date, window_end: date, starts_on: int = MONDAY
) -> str:
    """`pinned` | `window` | `week`, from the width of the stored window."""
    if window_start >= window_end:
        return "pinned"
    ws = week_start(window_start, starts_on)
    if window_start == ws and window_end >= ws + timedelta(days=WEEK_LENGTH_DAYS - 1):
        return "week"
    return "window"


def effective_window(
    window_start: date, window_end: date, today: date
) -> Optional[Tuple[date, date]]:
    """The days still available to this session, or None once none remain.

    Days already gone cannot hold a session that has not happened, so the window
    starts at today. Before the window opens it is untouched — a session planned
    for next Saturday has not narrowed just because today is Monday.
    """
    start = max(window_start, today)
    if start > window_end:
        return None
    return start, window_end


def has_narrowed(window_start: date, window_end: date, today: date) -> bool:
    """True when time has eaten into the window the session was given."""
    effective = effective_window(window_start, window_end, today)
    return effective is not None and effective[0] > window_start


def session_status(session: Any, today: date) -> str:
    """`done` | `dismissed` | `missed` | `upcoming`.

    Order matters: a session that was completed reads as done even if its window
    has since passed, and a dismissed suggestion reads as dismissed rather than
    missed — the runner made a choice there, they did not fail to act.
    """
    if getattr(session, "completed_at", None) is not None:
        return "done"
    if getattr(session, "dismissed_at", None) is not None:
        return "dismissed"
    if effective_window(session.window_start, session.window_end, today) is None:
        return "missed"
    return "upcoming"


@dataclass(frozen=True)
class PlacedSession:
    """A session pinned to one concrete day, for rule checking."""

    session_id: Any
    intent: str
    day: date


def candidate_days(session: Any, today: Optional[date] = None) -> list:
    """Every day this session could still land on, earliest first.

    Rule checking asks whether a legal week EXISTS, so it needs each session's
    domain. A pinned session has a domain of one; that is the whole difference
    between pinned and floating at this layer.
    """
    window = (session.window_start, session.window_end)
    if today is not None:
        narrowed = effective_window(session.window_start, session.window_end, today)
        if narrowed is None:
            return []
        window = narrowed
    start, end = window
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]
