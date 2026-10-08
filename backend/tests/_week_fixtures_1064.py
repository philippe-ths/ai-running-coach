"""Shared constructed data for the week tests (#1064).

Synthetic test setup that exercises code paths; none of it represents a real
runner. The runner built here is shaped like the case the work was written for, so
the arithmetic in the assertions can be checked by eye:

    usual week   run 2.7 h / 28 km, walk 4.9 h / 30 km (6 walks), ride 1.1 h /
                 25 km, strength 0.5 h
    zone 2+ share  run 98 %, ride 89 %, walk 27 %, strength 33 %

so their usual zone 2+ week is about 5.1 h and a 10 h challenge is a real climb.
TODAY is a Thursday (see `_season_fixtures_1064`).
"""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Dict, List
from uuid import uuid4

from app.schemas.season import SeasonPlan
from app.services.schedule.draft_contract import DraftedSession
from tests._season_fixtures_1064 import MONDAY, THIS_WEEK, TODAY
from tests._season_fixtures_1064 import fact as _fact

__all__ = [
    "TODAY", "THIS_WEEK", "MONDAY", "owner_facts", "session", "challenge_season",
    "SHARES", "goal_row", "fact",
]

# Heart-rate share at zone 2 or above, per activity, as built into `owner_facts`.
SHARES = {"run": 0.98, "bike": 0.89, "walk": 0.27, "strength": 0.33}


def fact(day, **kw):
    """A fact with the extra fields the volume norms read."""
    f = _fact(day, **kw)
    f.effort_score = 10.0
    f.activity_id = None
    return f


def _zones(seconds: int, share: float) -> Dict[str, float]:
    return {
        "Z1": seconds * (1 - share),
        "Z2": seconds * share,
        "Z3": 0, "Z4": 0, "Z5": 0,
    }


def owner_facts(weeks: int = 14, *, before: date = THIS_WEEK) -> List[SimpleNamespace]:
    """`weeks` complete weeks of the runner above, ending the week before `before`."""
    out = []
    for n in range(1, weeks + 1):
        monday = before - timedelta(weeks=n)
        for offset in (1, 3, 5):  # Tue, Thu, Sat: three runs of 54 min, 9.33 km
            out.append(fact(monday + timedelta(days=offset), kind="Run", seconds=3240,
                            distance_m=9333, zones=_zones(3240, SHARES["run"])))
        for offset in (0, 1, 2, 3, 4, 6):  # six walks of 49 min, 5 km
            out.append(fact(monday + timedelta(days=offset), kind="Walk", seconds=2940,
                            distance_m=5000, zones=_zones(2940, SHARES["walk"])))
        out.append(fact(monday + timedelta(days=2), kind="Ride", seconds=3960,
                        distance_m=25000, zones=_zones(3960, SHARES["bike"])))
        out.append(fact(monday + timedelta(days=4), kind="WeightTraining", seconds=1800,
                        distance_m=0, zones=_zones(1800, SHARES["strength"])))
    return out


def session(day, discipline="run", *, hours=None, seconds=None, km=None, intent="easy",
            title=None, commitment="committed", end=None) -> DraftedSession:
    """A drafted session on `day` (pinned unless `end` is given)."""
    seconds = int(seconds if seconds is not None else (hours or 1.0) * 3600)
    return DraftedSession(
        window_start=day,
        window_end=end or day,
        intent=intent,
        discipline=discipline,
        commitment=commitment,
        title=title or f"{intent.capitalize()} {discipline}",
        target_duration_s=seconds,
        target_distance_m=km * 1000 if km is not None else None,
    )


def goal_row(name, **kw):
    return SimpleNamespace(
        id=uuid4(), name=name, race_date=kw.get("race_date"),
        window_start=kw.get("window_start"), window_end=kw.get("window_end"),
        distance_m=kw.get("distance_m"), target_time_s=None, notes=kw.get("notes"),
        booked=kw.get("booked", False), priority=kw.get("priority", "A"),
        created_at=None,
    )


def challenge_season(
    challenge_goal,
    *,
    start=date(2026, 10, 12),
    weeks=10,
    hours=10.0,
    race=None,
    phases=None,
) -> SeasonPlan:
    """A season holding one 10 h zone 2+ challenge (and optionally a dated race).

    Phases: build to the week of 19 Oct then hold, unless `phases` is given.
    """
    goals = [
        {
            "goal_id": str(challenge_goal.id), "kind": "challenge",
            "success": "Ten straight weeks.", "approach": "Ramp then hold.",
            "challenge": {
                "metric": "zone_time_s", "min_zone": 2, "at_least": hours * 3600,
                "weeks": weeks, "start": start.isoformat(),
            },
        }
    ]
    if race is not None:
        goals.append(
            {
                "goal_id": str(race.id), "kind": "race", "success": "A PB.",
                "date": race.race_date.isoformat(), "approach": "Sharpen.",
            }
        )
    phases = phases or [
        {"kind": "build", "start": "2026-10-05", "end": "2026-10-18",
         "weekly_hours": 12, "run_km": 40, "long_run_km": 14, "focus": "easy volume"},
        {"kind": "base", "start": "2026-10-19", "end": "2026-12-27",
         "weekly_hours": 13, "run_km": 40},
    ]
    return SeasonPlan.model_validate(
        {"summary": "The challenge first.", "goals": goals, "phases": phases}
    )
