"""Why this activity stands out for THIS runner (#1032, parent #1035).

The coach used to receive every activity as a routine session, so the owner's goal
half marathon — Strava's fastest-ever at eight distances — was coached as a hard long
run and opened as a missed target, and a hike up Scafell Pike (a climb 4.7x anything
they had done on foot in a year) earned nothing. This module decides what is notable
and frames each reason so it cannot be misread as a warning or a verdict.

Three kinds of reason, each measured against the runner's own history:

  - a RACE: the run was one (see `analysis.classifier.race_source`);
  - BEST EFFORTS: Strava's per-distance segments of this run that Strava ranks in the
    runner's top three, plus the race distance on a race, with the previous best when
    our records can actually answer it;
  - RECORDS: a measure (distance, moving time, climb) that clearly beats the most the
    runner did in the past year at the same kind of activity.

THE RECORD RULE IS CALIBRATED, NOT GUESSED. A plain "longest in N days" fires on
every long run of a progressive build and then falls silent for months; the owner
named that risk. Replayed over the owner's 13 months of history, "more than 10% past
the most in the past 365 days, among at least 10 activities of that kind" fired 14
times in total — about once a month across every activity type, never on the
build's weekly long runs, and on the Scafell hike by a factor of 4.7. Change the
constants below by replaying again, not by intuition.

Pure: the ORM reads that feed it live in `context._build_notable_context`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional, Sequence

from app.schemas.coach_context import (
    NotableBestEffort,
    NotableContext,
    NotableRace,
    NotableRecord,
)
from app.services.best_efforts import Effort
from app.services.coach.coach_units import duration_precise

RECORD_WINDOW_DAYS = 365
RECORD_MARGIN = 0.10
RECORD_MIN_HISTORY = 10

# Only kinds of activity where distance, time and climb mean what they say. A padel
# match's GPS "distance" or a gym session's duration is not a record anyone set.
RECORD_DISCIPLINES = {
    "run": "runs",
    "walk": "walks and hikes",
    "bike": "rides",
}

# (fact attribute, coach-facing name, formatter)
_RECORD_MEASURES = (
    ("distance_m", "distance", lambda v: f"{v / 1000:.1f} km"),
    ("moving_time_s", "moving time", lambda v: duration_precise(v)),
    ("elev_gain_m", "climb", lambda v: f"{v:.0f} m"),
)

# The distances runners race, in the order the coach reads them. Only a fastest-ever at
# one of these is notable on its own (see `_choose_efforts`).
_STANDARD_EFFORTS = ("Marathon", "Half-Marathon", "10K", "5K", "1 mile")
MAX_BEST_EFFORTS = 5

# A whole earlier run counts as "about this distance" for the fallback comparison
# when it is at least the effort's distance and no more than this much over it.
_WHOLE_RUN_FALLBACK_SLACK = 1.05

NONE_HELD = (
    "We hold no earlier effort at this distance; any earlier one predates our records."
)

MARGIN_UNKNOWN = (
    "Margin not known: an earlier, faster effort may predate our records of their "
    "best efforts."
)

_RANK_LABEL = {1: "fastest ever", 2: "2nd fastest ever", 3: "3rd fastest ever"}

RACE_READING = (
    "A race. Effort near the limit, heart rate near max and a spike in load are what "
    "racing costs, not warning signs; the recovery it asks for is real."
)


def _fmt_date(d: date) -> str:
    return f"{d.day} {d.strftime('%b %Y')}"


# --- race --------------------------------------------------------------------


def build_race(source: Optional[str], goal_race: Optional[Any]) -> Optional[NotableRace]:
    if source is None:
        return None
    return NotableRace(
        source=source,
        name=getattr(goal_race, "name", None) if goal_race is not None else None,
        priority=getattr(goal_race, "priority", None) if goal_race is not None else None,
        reading=RACE_READING,
    )


# --- best efforts ------------------------------------------------------------


@dataclass(frozen=True)
class PriorEffort:
    """An effort on an earlier activity, from our store."""

    effort: Effort
    on: date


@dataclass(frozen=True)
class PriorRun:
    """An earlier run-family activity: whether we hold its efforts, and its whole-run figures."""

    on: date
    distance_m: float
    moving_time_s: int
    has_efforts: bool


def _choose_efforts(found: Sequence[Effort], race_distance_m: Optional[float]) -> List[Effort]:
    """The efforts worth the coach's attention: a fastest-ever at a standard race
    distance, plus the race's own distance on a race.

    Strava ranks every segment it measures, down to 400 m, and also ranks a 2nd and
    3rd fastest. Counting all of those fired on 28% of the owner's runs through one
    build, mostly a half-mile or a 1K inside an ordinary run: noise, not news. A
    fastest-ever at a distance runners actually race is the news.
    """
    race_effort: Optional[Effort] = None
    if race_distance_m:
        race_effort = min(
            found,
            key=lambda e: abs(e.distance_m - race_distance_m),
            default=None,
        )
        if race_effort is not None and abs(race_effort.distance_m - race_distance_m) > 0.1 * race_distance_m:
            race_effort = None
    chosen = [
        e
        for e in found
        if e is race_effort or (e.pr_rank == 1 and e.name in _STANDARD_EFFORTS)
    ]

    def order(e: Effort):
        if e is race_effort:
            return (0, 0)
        return (1, _STANDARD_EFFORTS.index(e.name))

    return sorted(chosen, key=order)[:MAX_BEST_EFFORTS]


def build_best_efforts(
    found: Sequence[Effort],
    prior_efforts: Sequence[PriorEffort],
    prior_runs: Sequence[PriorRun],
    *,
    race_distance_m: Optional[float] = None,
) -> Optional[List[NotableBestEffort]]:
    """This run's notable best efforts, each set against the best earlier effort WE HOLD.

    Strava's rank is against the runner's whole history; our store holds best efforts
    only from `best_efforts_recorded_since` on, so the comparison is named for what it
    is ("the best we hold") and never claimed as their previous best or a first time.
    It is given only when EVERY earlier run we hold long enough to contain the
    distance has its efforts stored; otherwise an effort we hold could be beaten by
    one we cannot see, and the note offers the fastest whole earlier run of about that
    distance instead, labelled as a whole-run time.
    """
    out: List[NotableBestEffort] = []
    for effort in _choose_efforts(found, race_distance_m):
        long_enough = [r for r in prior_runs if r.distance_m >= effort.distance_m]
        complete = all(r.has_efforts for r in long_enough)
        earlier = [p for p in prior_efforts if p.effort.name == effort.name]
        best = min(earlier, key=lambda p: p.effort.elapsed_time_s, default=None)

        best_held = margin = note = None
        if complete and best is not None:
            best_held = (
                f"{duration_precise(best.effort.elapsed_time_s)} ({_fmt_date(best.on)})"
            )
            diff = effort.elapsed_time_s - best.effort.elapsed_time_s
            if diff < 0:
                margin = f"{duration_precise(-diff)} faster than that"
            elif diff > 0:
                margin = f"{duration_precise(diff)} slower than that"
            else:
                margin = "the same time as that"
        elif complete:
            note = NONE_HELD
        else:
            whole = [
                r
                for r in long_enough
                if not r.has_efforts
                and r.distance_m <= effort.distance_m * _WHOLE_RUN_FALLBACK_SLACK
                and r.moving_time_s
            ]
            note = MARGIN_UNKNOWN
            fastest_whole = min(whole, key=lambda r: r.moving_time_s, default=None)
            if fastest_whole is not None:
                note += (
                    f" Their fastest earlier run of about this distance was "
                    f"{fastest_whole.distance_m / 1000:.1f} km in "
                    f"{duration_precise(fastest_whole.moving_time_s)} "
                    f"({_fmt_date(fastest_whole.on)}): whole-run moving time, not a "
                    f"measured effort."
                )
        out.append(
            NotableBestEffort(
                distance=effort.name,
                time=duration_precise(effort.elapsed_time_s),
                strava_rank=_RANK_LABEL.get(effort.pr_rank) if effort.pr_rank else None,
                best_we_hold_before=best_held,
                margin=margin,
                note=note,
            )
        )
    return out or None


# --- records -----------------------------------------------------------------


def build_records(
    discipline: str,
    this: Any,
    on: date,
    prior_same_discipline: Sequence[Any],
) -> Optional[List[NotableRecord]]:
    """Measures on which ``this`` clearly beats the runner's past-year most.

    ``this`` and ``prior_same_discipline`` expose ``distance_m``, ``moving_time_s``,
    ``elev_gain_m`` and ``local_date`` (the fact-stream shape). ``prior_same_discipline``
    is every earlier activity of the same discipline we hold, before ``this``.
    """
    label = RECORD_DISCIPLINES.get(discipline)
    if label is None:
        return None
    window = [p for p in prior_same_discipline if (on - p.local_date).days <= RECORD_WINDOW_DAYS]
    if len(window) < RECORD_MIN_HISTORY:
        return None
    out: List[NotableRecord] = []
    for attr, name, fmt in _RECORD_MEASURES:
        value = getattr(this, attr, 0) or 0
        top = max(window, key=lambda p: getattr(p, attr, 0) or 0)
        top_value = getattr(top, attr, 0) or 0
        if top_value <= 0 or value <= top_value * (1 + RECORD_MARGIN):
            continue
        all_time = max((getattr(p, attr, 0) or 0) for p in prior_same_discipline)
        out.append(
            NotableRecord(
                measure=name,
                value=fmt(value),
                previous_most=f"{fmt(top_value)} ({_fmt_date(top.local_date)})",
                times_previous=round(value / top_value, 1),
                compared_with=f"their {label} over the past 12 months ({len(window)} of them)",
                beats_all_we_hold=value > all_time,
            )
        )
    return out or None


def recorded_since(prior_runs: Sequence[PriorRun], this_day: date) -> str:
    """When our best-effort records begin: the earliest covered run, else this one."""
    first = min((r.on for r in prior_runs if r.has_efforts), default=this_day)
    return _fmt_date(first)


def build_notable(
    *,
    race: Optional[NotableRace],
    best_efforts: Optional[List[NotableBestEffort]],
    records: Optional[List[NotableRecord]],
    best_efforts_recorded_since: Optional[str] = None,
) -> Optional[NotableContext]:
    """The section, or None when nothing stands out — the usual case."""
    if race is None and not best_efforts and not records:
        return None
    return NotableContext(
        race=race,
        best_efforts=best_efforts,
        best_efforts_recorded_since=best_efforts_recorded_since if best_efforts else None,
        records=records,
    )
