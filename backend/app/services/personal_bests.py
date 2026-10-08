"""The runner's personal bests at the distances runners race (#1068).

Strava has no "list my PBs" endpoint. What it gives us is `pr_rank` on each run's
best efforts (see `best_efforts.py`), and only on runs we ingested in DETAIL: a run
we hold from the summary list carries no efforts at all. So the fastest effort we
hold at a distance is not automatically the runner's PB, and the coach must never
be handed it as one without saying how far it can be trusted.

Each derived PB carries one of three Strava-side statuses:

  - CONFIRMED: Strava ranked it fastest-ever when it was set, and every later run
    long enough to contain the distance has its efforts on record, so nothing we
    cannot see could have beaten it.
  - MAY_BE_BEATEN: Strava ranked it fastest-ever when it was set, but a later run
    long enough to contain the distance is one we hold without efforts.
  - FASTER_EXISTS: Strava did NOT rank our fastest held effort fastest-ever, so a
    faster effort exists outside our records. This is a bound, not a PB.

The rule holds whether Strava fixes `pr_rank` at upload (its documented reading)
or recomputes it later: either way a rank-1 effort was never beaten by an earlier
one, and a non-rank-1 fastest-held effort means Strava knows a faster one we lack.

What the rule cannot see: a run we never stored at all (a sync gap) does not count
as a later run held without efforts, and neither does an older, faster run uploaded
after the PB. Either can leave a PB labelled CONFIRMED that is not.

The runner can also STATE a PB (`UserProfile.stated_pbs`), for the ones our records
cannot see. Owner decision on #1068: the faster of stated and derived is the PB the
coach is shown, with its source named.

`personal_bests` is pure. `for_runner` does the reads, so any surface (the coach
tool, the schedule drafter) gets the same answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from app.services.best_efforts import Effort
from app.services.coach.coach_units import duration_precise

# Strava's best-effort label -> metres, in the order the coach reads them. The
# names are Strava's own, so a stated PB and a derived one share one key.
DISTANCES: Dict[str, float] = {
    "1 mile": 1609.34,
    "5K": 5000.0,
    "10K": 10000.0,
    "Half-Marathon": 21097.5,
    "Marathon": 42195.0,
}

# How the runner reads each distance, for anything shown to them.
DISPLAY_NAMES: Dict[str, str] = {"Half-Marathon": "half marathon", "Marathon": "marathon"}

# The envelope a stated PB must fall inside, in seconds: just under the world
# record at the fast end, about 15 min/km at the slow end. Not a judgement about
# the runner. It catches a unit slip (a marathon typed as 3:30 minutes:seconds)
# that would otherwise reach the coach as a fact.
STATED_TIME_BOUNDS: Dict[str, tuple] = {
    "1 mile": (220, 1500),
    "5K": (750, 4500),
    "10K": (1570, 9000),
    "Half-Marathon": (3450, 19000),
    "Marathon": (7200, 38000),
}

CONFIRMED = "confirmed"
MAY_BE_BEATEN = "may_be_beaten"
FASTER_EXISTS = "faster_exists"
STATED = "runner_stated"

_READINGS = {
    CONFIRMED: (
        "Their PB, measured by Strava. Strava ranked it their fastest ever when they "
        "ran it, and every later run long enough to contain this distance is on record."
    ),
    MAY_BE_BEATEN: (
        "Their PB as far as we can tell, measured by Strava and ranked their fastest "
        "ever when they ran it. Some later runs long enough to contain this distance "
        "are held without their best efforts, so one of them could have beaten it."
    ),
    FASTER_EXISTS: (
        "NOT their PB. Strava knows a faster effort at this distance that is not in "
        "our records, so their PB is faster than this time by an unknown margin. If "
        "the exact PB matters, ask them for it."
    ),
    STATED: (
        "Their PB as they told us. Not measured by us; take it as their word."
    ),
}


@dataclass(frozen=True)
class HeldRun:
    """One run-family activity we hold: its local day, length, and stored efforts.
    `efforts` is empty when we hold the run without its best efforts."""

    on: date
    distance_m: float
    efforts: Sequence[Effort]


@dataclass(frozen=True)
class StatedPB:
    distance: str
    time_s: int
    on: Optional[date] = None


@dataclass(frozen=True)
class _Derived:
    effort: Effort
    on: date
    status: str
    later_runs_without_efforts: int


def _derive(distance: str, runs: Sequence[HeldRun]) -> Optional[_Derived]:
    metres = DISTANCES[distance]
    held = [
        (e, r.on)
        for r in runs
        for e in r.efforts
        if e.name == distance
    ]
    if not held:
        return None
    # On a tie, the effort Strava ranked first, then the earlier run.
    best, on = min(
        held, key=lambda pair: (pair[0].elapsed_time_s, pair[0].pr_rank != 1, pair[1])
    )
    if best.pr_rank != 1:
        return _Derived(best, on, FASTER_EXISTS, 0)
    blind = sum(
        1 for r in runs if r.on >= on and not r.efforts and r.distance_m >= metres
    )
    return _Derived(best, on, MAY_BE_BEATEN if blind else CONFIRMED, blind)


def _entry(distance: str, time_s: int, on: Optional[date], status: str) -> dict:
    return {
        "distance": distance,
        "time": duration_precise(time_s),
        "time_s": time_s,
        "set_on": on.isoformat() if on else None,
        "source": "the runner told us" if status == STATED else "Strava best effort",
        "status": status,
        "reading": _READINGS[status],
    }


def personal_bests(runs: Sequence[HeldRun], stated: Sequence[StatedPB]) -> List[dict]:
    """One entry per standard distance we know anything about, shortest first.

    The faster of a stated and a derived time wins. A FASTER_EXISTS bound is never
    the faster one in practice (the runner's real PB beats it), but if the runner's
    stated time is slower than the bound, the bound is kept, labelled as one, and the
    stale stated time named.
    """
    stated_by = {s.distance: s for s in stated if s.distance in DISTANCES}
    out: List[dict] = []
    for distance in DISTANCES:
        derived = _derive(distance, runs)
        said = stated_by.get(distance)
        if derived is None and said is None:
            continue
        if said is not None and (
            derived is None or said.time_s < derived.effort.elapsed_time_s
        ):
            out.append(_entry(distance, said.time_s, said.on, STATED))
            continue
        entry = _entry(distance, derived.effort.elapsed_time_s, derived.on, derived.status)
        if said is not None and said.time_s > derived.effort.elapsed_time_s:
            # What they told us is slower than what Strava measured, so it is out of
            # date. Say so, or the coach asks for a PB the runner already gave.
            entry["reading"] += (
                f" They told us {duration_precise(said.time_s)}, which is slower "
                f"than this, so their figure is out of date."
            )
        if derived.later_runs_without_efforts:
            entry["later_runs_without_efforts"] = derived.later_runs_without_efforts
        out.append(entry)
    return out


# Strava's run family, as `query_tools` counts it for the runner's own lookups.
RUN_TYPES = ("run", "virtualrun", "trailrun")


def stated_from_profile(raw: Optional[Sequence[Any]]) -> List[StatedPB]:
    """`UserProfile.stated_pbs` as `StatedPB`s. A malformed row is skipped, never
    guessed at, so one bad row cannot take the rest down with it."""
    out: List[StatedPB] = []
    for item in raw or []:
        try:
            on = item.get("on")
            out.append(
                StatedPB(
                    distance=str(item["distance"]),
                    time_s=int(item["time_s"]),
                    on=date.fromisoformat(on) if on else None,
                )
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def for_runner(db, user_id) -> dict:
    """This runner's PBs, with when our Strava best efforts begin and the distances
    we hold nothing at. Owner-scoped: every read filters on `user_id`."""
    from sqlalchemy import func

    from app.models import Activity, UserProfile
    from app.services import activity_facts as af
    from app.services.best_efforts import efforts as parse_efforts

    rows = (
        db.query(
            Activity.start_date,
            Activity.start_date_local,
            Activity.distance_m,
            Activity.raw_summary["best_efforts"].label("best_efforts"),
        )
        .filter(
            Activity.user_id == user_id,
            Activity.is_deleted == False,  # noqa: E712
            func.lower(Activity.type).in_(RUN_TYPES),
        )
        .all()
    )
    runs = [
        HeldRun(
            on=af.local_day(r.start_date, r.start_date_local),
            distance_m=r.distance_m or 0,
            efforts=parse_efforts({"best_efforts": r.best_efforts}),
        )
        for r in rows
    ]
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    found = personal_bests(runs, stated_from_profile(getattr(profile, "stated_pbs", None)))

    covered = [r.on for r in runs if r.efforts]
    out: dict = {
        "personal_bests": found,
        "strava_best_efforts_on_record_from": min(covered).isoformat() if covered else None,
    }
    missing = [d for d in DISTANCES if d not in {p["distance"] for p in found}]
    if missing:
        out["no_record_at"] = missing
    return out
