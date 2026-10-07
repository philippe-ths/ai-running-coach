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

from datetime import date
from typing import Any, Dict, Optional


def ready_by(goal: Any) -> Optional[date]:
    """The date a plan should have the runner ready by, or None for an undated goal."""
    return getattr(goal, "race_date", None) or getattr(goal, "window_start", None)


def validator_race(races: Any) -> Optional[tuple]:
    """The `(date, distance)` the plan validator treats as the race week, or None.

    The goal the plan is built for (A, else the soonest), and only when it has an
    exact date: a window or no date has no day for a race week to fall on.
    """
    target = next((r for r in races if r.priority == "A"), races[0] if races else None)
    if target is None or target.race_date is None:
        return None
    return (target.race_date, target.distance_m)


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


def when_text(goal: Any) -> str:
    """The goal's date, with its precision stated so it cannot be read as more exact."""
    if goal.race_date is not None:
        booked = "booked" if goal.booked else "not booked yet"
        return f"{goal.race_date.day} {goal.race_date:%B %Y} (exact date, {booked})"
    if goal.window_start is not None:
        start, end = goal.window_start, goal.window_end or goal.window_start
        span = (
            _month(start)
            if (start.year, start.month) == (end.year, end.month)
            else f"{_month(start)} to {_month(end)}"
        )
        return f"around {span} (approximate, no event chosen yet)"
    return "no date (a direction, not a deadline)"


def format_duration(seconds: int) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def weeks_away(goal: Any, today: date) -> Optional[float]:
    when = ready_by(goal)
    return None if when is None else round((when - today).days / 7, 1)


def for_coach(goal: Any, today: date) -> Dict[str, Any]:
    """One goal as every coach surface receives it. Absent facts are omitted, not nulled."""
    out: Dict[str, Any] = {
        "name": goal.name,
        "priority": goal.priority,
        "when": when_text(goal),
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


def prompt_line(goal: Any, today: date) -> str:
    """One goal as a line in a drafting or report prompt."""
    return line_from(for_coach(goal, today))


def line_from(facts: Dict[str, Any]) -> str:
    """A `for_coach` dict as one prompt line (for packs that store the dict)."""
    parts = [f"- {facts['name']} (priority {facts['priority']}): {facts['when']}"]
    if "weeks_away" in facts:
        parts.append(f"{facts['weeks_away']:.0f} weeks away")
    parts.append(f"{facts['distance_km']:g} km" if "distance_km" in facts else "no fixed distance")
    if "target_time" in facts:
        parts.append(f"target {facts['target_time']}")
    line = ", ".join(parts)
    if "their_note" in facts:
        line += f'. In their words: "{facts["their_note"]}"'
    return line
