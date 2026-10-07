"""A goal's timing and how the coach is told about it (#1042).

A goal is held as precisely as the runner holds it: an exact date ("8 Nov,
booked"), an approximate window ("~March", "May to June"), or no date at all
("backyard ultra, someday"). Everything that needs ONE date to act on (sorting,
weeks away, which goal a plan builds towards, where the horizon draws a marker)
reads `ready_by`, so the choice is made once: the exact date, else the START of
the window, since "~March" means ready by early March and a plan aimed at the end
of the window would peak late.

Everything that tells the coach about a goal goes through `when_text` and
`for_coach`, which state the precision in words. A bare ISO date for a "~March"
goal reads as a booked date, and an LLM handed one will treat it as fixed.

Pure: no I/O.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional


def ready_by(goal: Any) -> Optional[date]:
    """The date a plan should have the runner ready by, or None for an undated goal."""
    return getattr(goal, "race_date", None) or getattr(goal, "window_start", None)


def target_goal(races: Any) -> Optional[Any]:
    """The goal a plan is built towards: the runner's A, else the soonest, among
    goals with a date to build backwards from. An undated goal never anchors a
    plan. `races` must already be soonest first (`store.list_goal_races`).

    One definition, read by the plan's anchor, how far the plan reaches and the
    drafting prompt's wording, so the three cannot name different goals.
    """
    dated = [r for r in races if ready_by(r) is not None]
    return next((r for r in dated if r.priority == "A"), dated[0] if dated else None)


def validator_races(races: Any) -> List[tuple]:
    """Every `(date, distance)` the plan validator treats as a race week (#1043).

    Every goal with an exact date, whatever its priority: a B race between here
    and the A goal is still a fixed distance on a fixed day that the runner chose,
    not training volume the ceiling has a view on. Only the target used to be
    exempt, so a booked half three months before the marathon counted its 21 km
    against the ceiling as though the coach had prescribed it. A window has no
    day for a race week to fall on, so it is never one.
    """
    return [
        (r.race_date, r.distance_m) for r in races if r.race_date is not None
    ]


# How long after a dated goal a plan keeps going: the recovery weeks the drafting
# prompt asks for, so the plan does not stop dead at the race.
RECOVERY_WEEKS_AFTER_GOAL = 2


def reach_end(goal: Any) -> Optional[date]:
    """The last day a plan built towards this goal has to cover (#1043).

    An exact date reaches past the race by the recovery weeks. A window reaches
    its END: the event falls somewhere inside it, and a plan that stopped at the
    start would leave the runner with no plan at the moment the goal arrives.
    The plan still aims to be ready by `ready_by`, the window's start.
    """
    if goal.race_date is not None:
        return goal.race_date + timedelta(weeks=RECOVERY_WEEKS_AFTER_GOAL)
    return goal.window_end or goal.window_start


def is_upcoming(goal: Any, today: date) -> bool:
    """Still ahead: an exact date not yet passed, a window not yet closed, or no date."""
    if goal.race_date is not None:
        return goal.race_date >= today
    if goal.window_end is not None:
        return goal.window_end >= today
    return True


def sort_key(goal: Any):
    """Soonest first; undated goals last, in the order they were added."""
    when = ready_by(goal)
    added = getattr(goal, "created_at", None)
    return (when is None, when or date.max, added.timestamp() if added else 0.0)


def _month(d: date) -> str:
    return d.strftime("%B %Y")


def when_text(goal: Any, today: Optional[date] = None) -> str:
    """The goal's date, with its precision stated so it cannot be read as more exact.

    A window says nothing about an event: "10h a week, October to December" is a
    window too, and wording it as an event not yet chosen had a drafted plan taper
    for a race the runner never stated. A window already open says so, because
    "-1 weeks away" reads as a date to plan towards rather than one being lived.
    """
    if goal.race_date is not None:
        # Unbooked says nothing either way: goals stored before `booked` existed
        # never recorded it, and "not booked yet" would state a fact nobody gave.
        booked = ", booked" if goal.booked else ""
        return f"{goal.race_date.day} {goal.race_date:%B %Y} (exact date{booked})"
    if goal.window_start is not None:
        start, end = goal.window_start, goal.window_end or goal.window_start
        span = (
            _month(start)
            if (start.year, start.month) == (end.year, end.month)
            else f"{_month(start)} to {_month(end)}"
        )
        if today is not None and start <= today:
            return f"around {span} (approximate, under way now)"
        return f"around {span} (approximate, nothing booked)"
    return "no date (a direction, not a deadline)"


def format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def weeks_away(goal: Any, today: date) -> Optional[float]:
    """Weeks until the ready-by date; None for an undated goal or one already under way."""
    when = ready_by(goal)
    if when is None or when < today:
        return None
    return round((when - today).days / 7, 1)


def for_coach(goal: Any, today: date) -> Dict[str, Any]:
    """One goal as every coach surface receives it. Absent facts are omitted, not nulled."""
    out: Dict[str, Any] = {
        "name": goal.name,
        "priority": goal.priority,
        "when": when_text(goal, today),
    }
    away = weeks_away(goal, today)
    if away is not None:
        out["weeks_away"] = away
    if goal.distance_m:
        out["distance_km"] = round(goal.distance_m / 1000, 1)
    if goal.target_time_s:
        out["target_time"] = format_duration(goal.target_time_s)
    if goal.notes:
        out["their_note"] = goal.notes
    return out


def prompt_line(goal: Any, today: date, *, suffix: str = "") -> str:
    """One goal as a line in a drafting or report prompt; `suffix` lands before the
    runner's note so it cannot read as part of their words."""
    return line_from(for_coach(goal, today), suffix=suffix)


def line_from(facts: Dict[str, Any], *, suffix: str = "") -> str:
    """A `for_coach` dict as one prompt line (for packs that store the dict)."""
    parts = [f"- {facts['name']} (priority {facts['priority']}): {facts['when']}"]
    if "weeks_away" in facts:
        parts.append(f"{facts['weeks_away']:.0f} weeks away")
    parts.append(f"{facts['distance_km']:g} km" if "distance_km" in facts else "no fixed distance")
    if "target_time" in facts:
        parts.append(f"target {facts['target_time']}")
    line = ", ".join(parts) + suffix
    if "their_note" in facts:
        # One line per goal: a note's line breaks are flattened so a note can never
        # start a line that reads as another goal with a firmer date.
        note = " ".join(str(facts["their_note"]).split())
        line += f'. In their words: "{note}"'
    return line
