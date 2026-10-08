"""Shared constructed data for the season tests (#1064).

All of it is synthetic test setup that exercises code paths; none of it
represents a real runner. Dates are pinned so the weekday arithmetic in the
assertions is checkable by eye: TODAY is a Thursday, THIS_WEEK the Monday before.
"""

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.season import SeasonPlan

TODAY = date(2026, 10, 8)  # Thursday
THIS_WEEK = date(2026, 10, 5)  # Monday
MONDAY = 0


def fact(day, *, kind="Run", seconds=3600, distance_m=10000, zones=None):
    return SimpleNamespace(
        local_date=day,
        activity_type=kind,
        moving_time_s=seconds,
        distance_m=distance_m,
        time_in_zones=zones,
    )


def steady_weeks(weeks=14, *, hours=6.0, z2_share=0.5, before=THIS_WEEK):
    """One fact a week for `weeks` complete weeks before `before`: `hours` of
    heart-rate-measured running, `z2_share` of it in zone 2 or above."""
    out = []
    for n in range(1, weeks + 1):
        day = before - timedelta(weeks=n) + timedelta(days=1)  # a Tuesday
        seconds = int(hours * 3600)
        out.append(
            fact(
                day,
                seconds=seconds,
                zones={
                    "Z1": seconds * (1 - z2_share),
                    "Z2": seconds * z2_share,
                    "Z3": 0, "Z4": 0, "Z5": 0,
                },
            )
        )
    return out


def goal(name, *, race_date=None, window=None, booked=False, priority="A", **extra):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        race_date=race_date,
        window_start=window[0] if window else None,
        window_end=window[1] if window else None,
        distance_m=extra.get("distance_m"),
        target_time_s=extra.get("target_time_s"),
        notes=extra.get("notes"),
        booked=booked,
        priority=priority,
        created_at=None,
    )


def standard_goals():
    """A booked 10k, an unbooked 10-week challenge, a marathon in a window, and a
    someday goal: one of each way a goal can be held."""
    return {
        "race": goal("Chatham 10k", race_date=date(2026, 11, 8), booked=True, priority="B",
                     distance_m=10000),
        "challenge": goal("10h a week", window=(date(2026, 10, 1), date(2026, 12, 31)),
                          priority="C"),
        "marathon": goal("First marathon", window=(date(2027, 5, 1), date(2027, 6, 30))),
        "someday": goal("Backyard ultra", priority="C"),
    }


def valid_payload(g):
    """A season that passes every check against `standard_goals()` when the
    runner's zone 2+ level is 18000 s (5 h) a week."""
    return {
        "summary": "Chatham first, then the challenge, then the marathon build.",
        "goals": [
            {
                "goal_id": str(g["race"].id), "kind": "race",
                "success": "A PB.", "date": "2026-11-08",
                "approach": "Sharpen for four weeks.",
            },
            {
                "goal_id": str(g["challenge"].id), "kind": "challenge",
                "success": "Ten straight weeks.", "approach": "Ramp, then hold.",
                "challenge": {
                    "metric": "zone_time_s", "min_zone": 2, "at_least": 36000,
                    "weeks": 10, "start": "2026-12-14",
                },
            },
            {
                "goal_id": str(g["marathon"].id), "kind": "finish",
                "success": "Run it well.", "window_start": "2027-05-01",
                "window_end": "2027-06-30", "approach": "Build through winter.",
                "events": [{"name": "Some Marathon", "url": "https://example.com/m",
                            "why": "not confirmed"}],
            },
            {
                "goal_id": str(g["someday"].id), "kind": "someday",
                "success": "One day.", "approach": "Not yet.",
            },
        ],
        "phases": [
            {"kind": "build", "start": "2026-10-05", "end": "2026-11-01"},
            {"kind": "taper", "start": "2026-11-02", "end": "2026-11-07"},
            {"kind": "race", "start": "2026-11-08", "end": "2026-11-08",
             "goal_id": str(g["race"].id)},
            {"kind": "base", "start": "2026-11-09", "end": "2027-06-30"},
        ],
    }


def valid_plan(g):
    return SeasonPlan.model_validate(valid_payload(g))


def level(value):
    """A `levels` callable that reports the same current weekly value for any rule."""
    return lambda rule: value
