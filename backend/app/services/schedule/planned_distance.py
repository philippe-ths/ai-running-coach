"""How far a PLANNED session goes — one definition, three readers (#876).

A run can be sized two ways, and both are legal by deliberate decision. Most
sessions carry a `target_distance_m`. An interval session usually does not:
"6 x 400m off 90s" already is the prescription, so `plan_validator` accepts rep
structure in place of a distance — demanding a total there rejected three real
interval sessions in a live draft, and the requirement was judged wrong, not the
plan (see `plan_validator._validate_sessions`).

Every sum of planned running nonetheless read `target_distance_m` alone, so a
session sized the second legal way summed as ZERO. A real week showed 23.5 km
with a 6 x 400m sitting in it and its 2.4 km of work nowhere — present as a card,
counted in "2 of 4 sessions", counted in the mix bar, absent from the only number
the runner reads as their week.

This module is the single answer, in the spirit of `disciplines.py`'s ONE
definition of a run: the headline, the three-month horizon and the volume
ceiling all ask here, so they cannot drift into three different opinions about
the same week.

What is NOT counted
-------------------
The warm-up and cool-down. They live in the session's prose ("Warm up 10 min
easy... Cool down 10 min easy") and nothing structured states them, so counting
them would mean inventing distance the plan never specified — the same abstention
`effort.py` makes when a runner's history is too thin to size a session. The
consequence is honest and known: an interval session's planned figure is its
WORK, and reads under what the runner will actually cover door to door.
"""

from typing import Any, Dict, Optional


def _structure(session: Any) -> Dict[str, Any]:
    """The rep shape, from whichever of the two shapes this reader was handed.

    `PlannedSessionRead` and the ORM row nest it under `structure`; the drafted
    session carries `reps_planned`/`rep_distance_m` flat, because the coach's
    tool arguments are flat and `DraftedSession.structure()` only nests them on
    the way to storage. The volume ceiling runs BEFORE that, on the flat shape,
    so a helper that read only `structure` would abstain on every plan it is
    supposed to be guarding.
    """
    nested = getattr(session, "structure", None)
    if isinstance(nested, dict):
        return nested
    return {
        "reps_planned": getattr(session, "reps_planned", None),
        "rep_distance_m": getattr(session, "rep_distance_m", None),
    }


def rep_work_m(session: Any) -> Optional[float]:
    """`reps x rep_distance`, or None when the reps carry no distance.

    "6 reps" with no distance on them is a real and legal prescription — the
    runner's card says exactly that — but it is not a distance, and guessing one
    would put a number the coach never wrote into the week's total.
    """
    structure = _structure(session)
    reps = structure.get("reps_planned")
    distance = structure.get("rep_distance_m")
    if not reps or not distance:
        return None
    return float(reps) * float(distance)


def planned_distance_m(session: Any) -> float:
    """What this session prescribes, in metres. Zero when it prescribes none.

    `target_distance_m` WINS when both are present. A coach who writes a total
    has stated the whole session, warm-up and cool-down included, so adding the
    reps on top would count the work twice — and the total is the more complete
    of the two statements, not merely the first one checked.
    """
    total = getattr(session, "target_distance_m", None)
    if total:
        return float(total)
    return rep_work_m(session) or 0.0
