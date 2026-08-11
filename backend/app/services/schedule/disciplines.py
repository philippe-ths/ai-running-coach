"""Logged activity type -> the schedule's discipline vocabulary (#830).

The runner trains holistically — running, walking, indoor biking, strength,
indoor rowing — so the schedule plans and measures the whole thing rather than
treating everything as a run.

ONE definition of "a run"
-------------------------
`run` here is defined by `activity_facts.is_run`, not by a list of its own. That
predicate is strictly `activity_type.lower() == "run"`, so `TrailRun` and
`VirtualRun` are NOT runs by the product's current definition and therefore land
in `other` here too. That is deliberate: the schedule's running-km headline has
to be the same number the Trends page shows, and a second, more generous run set
living here would make the two disagree about the same week with nothing to say
which was right. Widening what counts as a run is #644's job, in `is_run`, once —
and this module inherits the fix for free on the day it lands.
"""

from typing import Any

from app.services.activity_facts import is_run

# Everything the mapping is sure about. A type absent from here is `other`,
# which is honest — it is logged, it carries load, and it is not claimed to be
# something it might not be.
_DISCIPLINE_BY_TYPE = {
    "walk": "walk",
    "hike": "walk",
    "ride": "bike",
    "virtualride": "bike",
    "ebikeride": "bike",
    "mountainbikeride": "bike",
    "gravelride": "bike",
    "handcycle": "bike",
    "rowing": "row",
    "virtualrow": "row",
    "weighttraining": "strength",
    "crossfit": "strength",
}


def discipline_for_activity_type(activity_type: Any) -> str:
    """The schedule discipline a logged activity belongs to."""
    raw = (activity_type or "").strip().lower()
    if raw == "run":
        return "run"
    return _DISCIPLINE_BY_TYPE.get(raw, "other")


def discipline_for_fact(fact: Any) -> str:
    """Same, for a fact from the stream — routed through `is_run` so the run
    boundary is decided in exactly one place."""
    if is_run(fact):
        return "run"
    return _DISCIPLINE_BY_TYPE.get(
        (getattr(fact, "activity_type", "") or "").strip().lower(), "other"
    )
