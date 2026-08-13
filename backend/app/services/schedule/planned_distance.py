"""How far a PLANNED session goes — one definition, three readers (#876).

A run can be sized two ways, and both are legal by deliberate decision. Most
sessions carry a `target_distance_m`. An interval session usually does not:
"6 x 400m off 90s" already is the prescription, so `plan_validator` accepts rep
structure in place of a distance — demanding a total there rejected three real
interval sessions in a live draft, and the requirement was judged wrong, not the
plan (see `plan_validator._validate_sessions`).

Every sum of planned running nonetheless read `target_distance_m` alone, so a
session sized the second legal way summed as ZERO. A real week showed 23.5 km
with a 6 x 400m sitting in it and its work nowhere — present as a card, counted
in "2 of 4 sessions", counted in the mix bar, absent from the only number the
runner reads as their week.

This module is the single answer, in the spirit of `disciplines.py`'s ONE
definition of a run: the headline, the three-month horizon and the volume
ceiling all ask here, so they cannot drift into three different opinions about
the same week.

A session is warm-up + work + cool-down
---------------------------------------
The reps are the WORK, not the session. The first fix counted only the reps, so
a 6 x 400m the runner had agreed as 4.5 km door to door still landed in the week
as 2.4 km — closer, and still wrong. The warm-up and cool-down were unreachable
then because the coach wrote them as TIME, in prose ("Warm up 10 min easy"), and
turning that into a distance means multiplying by an assumed pace: the app
inventing distance, which is what `effort.py` abstains from doing rather than
guess.

So the prescription changed instead of the arithmetic (#876). The coach now
states a warm-up and cool-down as a DISTANCE, in the same unit as everything
else on the card, and the total is addition rather than inference. Nothing here
estimates: a session's planned distance is the sum of the distances the coach
actually wrote, and a part they did not write contributes nothing.
"""

from typing import Any, Dict, Optional

# The keys of the rep shape, nested under `structure` once stored and carried
# flat on the drafted session on its way there.
_REP_KEYS = ("reps_planned", "rep_distance_m", "warmup_distance_m", "cooldown_distance_m")


def _structure(session: Any) -> Dict[str, Any]:
    """The rep shape, from whichever of the two shapes this reader was handed.

    `PlannedSessionRead` and the ORM row nest it under `structure`; the drafted
    session carries the same keys flat, because the coach's tool arguments are
    flat and `DraftedSession.structure()` only nests them on the way to storage.
    The volume ceiling runs BEFORE that, on the flat shape, so a helper that read
    only `structure` would abstain on every plan it is supposed to be guarding.
    """
    nested = getattr(session, "structure", None)
    if isinstance(nested, dict):
        return nested
    return {key: getattr(session, key, None) for key in _REP_KEYS}


def _positive(value: Any) -> float:
    """A stated distance, or nothing. Never a negative and never a guess."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


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


def structured_distance_m(session: Any) -> float:
    """Warm-up + work + cool-down, from what the coach actually wrote.

    Each part is independent: a session may state a warm-up and no cool-down, or
    reps with no distance on them but a warm-up either side. Whatever is stated
    counts, and whatever is not contributes nothing rather than an estimate.
    """
    structure = _structure(session)
    return (
        _positive(structure.get("warmup_distance_m"))
        + (rep_work_m(session) or 0.0)
        + _positive(structure.get("cooldown_distance_m"))
    )


def planned_distance_m(session: Any) -> float:
    """What this session prescribes, in metres. Zero when it prescribes none.

    `target_distance_m` WINS when both are present. A coach who writes a total
    has stated the whole session, warm-up and cool-down included, so adding the
    structured parts on top would count the work twice — and the total is the
    more complete of the two statements, not merely the first one checked.

    That precedence is load-bearing rather than incidental (#878). A tempo run
    has a warm-up and no reps, so its work has no field of its own: with a total
    stated this reads it, and without one the session contributes the warm-up and
    the cool-down and nothing for the tempo block between them. Both answers are
    the module's own rule — what the coach actually wrote — and the second is the
    same abstention a duration-sized session has always made, improved on rather
    than made worse by the edges being stated.
    """
    total = getattr(session, "target_distance_m", None)
    if total:
        return float(total)
    return structured_distance_m(session)
