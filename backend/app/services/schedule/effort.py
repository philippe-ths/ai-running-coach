"""What a planned session is worth, in load — computed, never asked for (#830).

`effort_score` is the per-activity training-load primitive: Edwards-style zone
minutes, cumulative, growing with duration. It is what sizes the week's discipline
mix bar and the horizon's bar length, because it is the only unit that sums
honestly across a gym session and a turbo ride.

Why the coach is not asked for it
---------------------------------
The North Star names this exact number as the hard case: handed to an LLM raw it
fails both questions at once — a good coach does not need a bare load number, and
a model reads "high load" as "high intensity" (#168). Asking a model to ESTIMATE
one for a session it is inventing is worse still: it would be a number with no
provenance, sized by a guess, and then drawn as a bar the runner reads as fact.

So the division is: the coach chooses WHAT to do — the discipline, the intent, how
long, how far — and the app computes what that costs, from this runner's own
history. A 45-minute turbo is worth what a 45-minute turbo has actually cost this
runner, not what a model supposes.

It abstains rather than inventing. A runner with no history in a discipline gets
`None`, the bar simply does not size, and nothing claims a number it cannot
support — the same discipline the baseline buckets use when a bucket is too thin.
"""

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from app.services.schedule.disciplines import discipline_for_fact

# How far back the load model looks. Long enough to cover a discipline the runner
# trains occasionally (a monthly gym block), short enough that a year-old fitness
# level does not price today's session.
LOOKBACK_DAYS = 182

# A discipline needs this many sessions before its rate is used. Below it the
# median is one runner's one odd session rather than a rate.
MIN_SESSIONS_FOR_RATE = 3


@dataclass(frozen=True)
class LoadModel:
    """This runner's own cost of training, per discipline.

    `per_second` is the workhorse: load divided by moving time, which holds across
    session lengths. `per_session` is the fallback for a planned session given
    neither a duration nor a distance.
    """

    per_second: Dict[str, float] = field(default_factory=dict)
    per_metre: Dict[str, float] = field(default_factory=dict)
    per_session: Dict[str, float] = field(default_factory=dict)
    sessions_seen: Dict[str, int] = field(default_factory=dict)

    def knows(self, discipline: str) -> bool:
        return discipline in self.per_second or discipline in self.per_session


def build_load_model(
    facts: Sequence[Any], as_of: Optional[date] = None, *, lookback_days: int = LOOKBACK_DAYS
) -> LoadModel:
    """Median load rates per discipline, from the runner's own recent training.

    Medians rather than means: one 3-hour ride or one mis-recorded session should
    not move what a normal session is priced at.
    """
    as_of = as_of or date.today()
    cutoff = as_of - timedelta(days=lookback_days)

    seconds: Dict[str, List[float]] = {}
    metres: Dict[str, List[float]] = {}
    totals: Dict[str, List[float]] = {}

    for fact in facts:
        if fact.local_date < cutoff or fact.local_date > as_of:
            continue
        load = fact.effort_score or 0.0
        if load <= 0:
            continue
        discipline = discipline_for_fact(fact)
        totals.setdefault(discipline, []).append(load)
        moving = fact.moving_time_s or 0
        if moving > 0:
            seconds.setdefault(discipline, []).append(load / moving)
        distance = fact.distance_m or 0
        if distance > 0:
            metres.setdefault(discipline, []).append(load / distance)

    def _medians(source: Dict[str, List[float]]) -> Dict[str, float]:
        return {
            key: statistics.median(values)
            for key, values in source.items()
            if len(values) >= MIN_SESSIONS_FOR_RATE
        }

    return LoadModel(
        per_second=_medians(seconds),
        per_metre=_medians(metres),
        per_session=_medians(totals),
        sessions_seen={key: len(values) for key, values in totals.items()},
    )


def estimate_effort(
    model: LoadModel,
    discipline: str,
    *,
    duration_s: Optional[int] = None,
    distance_m: Optional[float] = None,
) -> Optional[float]:
    """What this planned session should cost this runner, or None to abstain.

    Duration first: load is cumulative in time, so a per-second rate is the most
    honest conversion and it works for every discipline. Distance is the fallback
    for a session prescribed in kilometres with no duration. A flat per-session
    median is the last resort, and no history at all abstains.
    """
    if duration_s and duration_s > 0:
        rate = model.per_second.get(discipline)
        if rate is not None:
            return round(rate * duration_s, 1)
    if distance_m and distance_m > 0:
        rate = model.per_metre.get(discipline)
        if rate is not None:
            return round(rate * distance_m, 1)
    typical = model.per_session.get(discipline)
    return round(typical, 1) if typical is not None else None
