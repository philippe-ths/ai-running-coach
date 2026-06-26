"""P3 Training Load — the read-time EWMA fitness/fatigue/form readiness model.

Our own deterministic, device-independent read of the runner's current condition,
built from the comparable per-activity ``effort_score`` primitive (#186). It is the
EWMA "fitness / fatigue / form" shape that Strava Fitness/Freshness and Garmin
Training Load implement (the TrainingPeaks Performance Management Chart):

* **fitness** — the slow-moving chronic load (an EWMA with a 42-day time constant).
* **fatigue** — the fast-moving acute load (a 7-day EWMA).
* **form** — their balance (fitness - fatigue): positive when fresh, negative when
  fatigue outweighs the built-up fitness.
* **ramp** — how fast fitness is rising (its change over the last 7 days).

ADR 0016: computed **at read time** (mirroring the M9 calibration pattern) over a
bounded window of recent daily load, so today's just-finished run is in the acute
load and per-read cost stays flat as history grows (#167). Nothing is persisted.
It is a tier-3 deterministic durable fact (``Authority tiering``): it grounds the
coach's readiness judgment but never overrides this run's measured data or the
safety floor. Platform numbers are validation-only, never authoritative and never
a cold-start seed; while history is shorter than the chronic window the read is
``warming_up`` and abstains from a confident verdict.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Activity, DerivedMetric

logger = logging.getLogger(__name__)

# EWMA time constants (days) — the TrainingPeaks/Strava standard.
FITNESS_TC_DAYS = 42   # chronic load -> fitness
FATIGUE_TC_DAYS = 7    # acute load   -> fatigue

# The bounded look-back window for the daily series. At >4x the fitness time
# constant the zero-seed bias at the as-of day is <2% for any established history,
# while per-read cost stays flat as history grows (#167). Mirrors the M2 baseline
# window length (``BASELINE_COMPARISON_WINDOW_DAYS``). Owner-tunable.
READINESS_WINDOW_DAYS = 182

# Warming-up floors: until the runner's history spans at least the chronic time
# constant AND carries at least this many load samples, the chronic baseline is not
# established, so the read abstains from a confident condition verdict. Owner-tunable.
MIN_HISTORY_DAYS_FOR_READINESS = FITNESS_TC_DAYS   # 42
MIN_ACTIVITIES_FOR_READINESS = 4

# Form bands, normalised by fitness so they are scale-free against our (non-TSS)
# Edwards zone-minute load unit: ``ratio = form / fitness``. Owner-tunable.
FRESH_FORM_RATIO = 0.05
FATIGUED_FORM_RATIO = -0.20
OVERREACHED_FORM_RATIO = -0.40

# Ramp bands, normalised by fitness: ``ratio = ramp / fitness`` (CTL change over the
# last 7 days). Owner-tunable.
RAMP_BUILDING_RATIO = 0.05
RAMP_DETRAINING_RATIO = -0.05
RAMP_AGGRESSIVE_RATIO = 0.15

_EPS = 1e-9


@dataclass(frozen=True)
class ReadinessModel:
    """One read-time readiness snapshot. All loads are in Edwards zone-minutes."""

    as_of: date
    fitness: float          # CTL — chronic load EWMA
    fatigue: float          # ATL — acute load EWMA
    form: float             # fitness - fatigue
    ramp_rate: float        # fitness change over the last 7 days
    condition: str          # fresh | balanced | fatigued | overreaching | building_baseline
    trend: str              # building | steady | detraining
    ramp_aggressive: bool   # ramp rising faster than the aggressive band
    warming_up: bool        # chronic baseline not yet established -> abstain
    sample_count: int       # load samples (activities) in the window
    history_span_days: int  # days from the runner's first activity to as_of


def _alpha(tc_days: int) -> float:
    """EWMA smoothing factor for a given time constant (days)."""
    return 1.0 - math.exp(-1.0 / tc_days)


def condition_for(form: float, fitness: float, *, warming_up: bool) -> str:
    """Classify the form/fitness balance into a condition label.

    Abstains (``building_baseline``) while warming up or before any fitness exists,
    so a confident verdict never outruns the evidence.
    """
    if warming_up or fitness <= _EPS:
        return "building_baseline"
    ratio = form / fitness
    if ratio >= FRESH_FORM_RATIO:
        return "fresh"
    if ratio >= FATIGUED_FORM_RATIO:
        return "balanced"
    if ratio >= OVERREACHED_FORM_RATIO:
        return "fatigued"
    return "overreaching"


def trend_for(ramp_rate: float, fitness: float) -> tuple[str, bool]:
    """Classify the fitness ramp into (trend, is_aggressive), normalised by fitness."""
    if fitness <= _EPS:
        return "steady", False
    ratio = ramp_rate / fitness
    aggressive = ratio >= RAMP_AGGRESSIVE_RATIO
    if ratio >= RAMP_BUILDING_RATIO:
        return "building", aggressive
    if ratio <= RAMP_DETRAINING_RATIO:
        return "detraining", False
    return "steady", aggressive


def compute_readiness(
    daily_loads: List[float],
    *,
    as_of: date,
    history_span_days: int,
    sample_count: int,
) -> ReadinessModel:
    """Pure EWMA fitness/fatigue/form over an oldest-first daily load series.

    ``daily_loads`` is the per-day total load (zero-filled for rest days), oldest
    first, ending on ``as_of``. The EWMAs are seeded at 0; the caller's window is
    long enough that the seed bias at ``as_of`` is negligible for an established
    history (and a short history is flagged ``warming_up`` regardless).
    """
    a_ctl = _alpha(FITNESS_TC_DAYS)
    a_atl = _alpha(FATIGUE_TC_DAYS)

    ctl = 0.0
    atl = 0.0
    ctl_series: List[float] = []
    for load in daily_loads:
        ctl += a_ctl * (load - ctl)
        atl += a_atl * (load - atl)
        ctl_series.append(ctl)

    form = ctl - atl

    # Ramp = fitness change over the last 7 days; fall back to the earliest point
    # available (from the zero seed) when the series is shorter than that.
    if len(ctl_series) >= 8:
        ramp = ctl_series[-1] - ctl_series[-8]
    elif ctl_series:
        ramp = ctl_series[-1] - ctl_series[0]
    else:
        ramp = 0.0

    warming_up = (
        history_span_days < MIN_HISTORY_DAYS_FOR_READINESS
        or sample_count < MIN_ACTIVITIES_FOR_READINESS
    )

    condition = condition_for(form, ctl, warming_up=warming_up)
    trend, aggressive = trend_for(ramp, ctl)

    return ReadinessModel(
        as_of=as_of,
        fitness=round(ctl, 1),
        fatigue=round(atl, 1),
        form=round(form, 1),
        ramp_rate=round(ramp, 1),
        condition=condition,
        trend=trend,
        ramp_aggressive=aggressive,
        warming_up=warming_up,
        sample_count=sample_count,
        history_span_days=history_span_days,
    )


def _local_day(start_date, start_date_local) -> date:
    """The runner's LOCAL calendar day for an activity, the ``Activity.local_start``
    convention (#507): ``start_date_local`` when present, else the UTC ``start_date``.

    Identical to the day-bucketing the volume / recent-training signals use (via
    ``ActivityFact.local_date``), so readiness and volume agree on the day boundary
    — a late-evening local run that has rolled to the next UTC day, and a same-local-
    day double session, land on ONE day across both signals.
    """
    chosen = start_date_local if start_date_local is not None else start_date
    return chosen.date() if isinstance(chosen, datetime) else chosen


def build_readiness(db: Session, user_id, as_of) -> Optional[ReadinessModel]:
    """Read-time readiness from the user's windowed activity history up to ``as_of``.

    ``as_of`` is the reference instant — for a coach report this is the activity's
    ``local_start`` (the runner's local wall-clock start, #507), so the daily series
    is keyed to the runner's local calendar and agrees with the volume signal on the
    day boundary. The read includes that run in the acute load and a regen of an old
    activity reproduces the condition as of *then*. Activities after ``as_of``'s local
    day are excluded; same-local-day loads are summed. The series is bounded to
    ``READINESS_WINDOW_DAYS`` so per-read cost stays flat as history grows (#167).

    Guarded: returns ``None`` on no history or on any failure, so it never breaks the
    caller (the ``recompute_runner_baseline`` precedent).
    """
    try:
        if not isinstance(user_id, uuid.UUID):
            user_id = uuid.UUID(str(user_id))

        as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of

        # The runner's first activity day on or before as_of, by LOCAL day (#507).
        # Fetch the earliest candidate cheaply (UTC min is a safe lower bound), then
        # resolve its local day; widen the <= filter by a day so a local day that
        # straddles the UTC boundary is never clipped at the edge.
        first_row = db.execute(
            select(Activity.start_date, Activity.start_date_local)
            .where(Activity.user_id == user_id)
            .where(Activity.is_deleted == False)  # noqa: E712
            .order_by(Activity.start_date)
            .limit(1)
        ).first()
        if first_row is None:
            return None
        first_date = _local_day(first_row[0], first_row[1])
        if first_date > as_of_date:
            return None
        history_span_days = (as_of_date - first_date).days

        # The UTC window filter is a coarse perf bound (#167): widen both edges by a
        # day so no local-vs-UTC boundary activity is dropped, then bucket precisely
        # by local day below.
        window_start_dt = as_of - timedelta(days=READINESS_WINDOW_DAYS + 1)
        as_of_upper_dt = as_of + timedelta(days=1)
        rows = db.execute(
            select(
                Activity.start_date,
                Activity.start_date_local,
                DerivedMetric.effort_score,
            )
            .join(DerivedMetric, DerivedMetric.activity_id == Activity.id)
            .where(Activity.user_id == user_id)
            .where(Activity.is_deleted == False)  # noqa: E712
            .where(Activity.start_date >= window_start_dt)
            .where(Activity.start_date <= as_of_upper_dt)
        ).all()

        window_start_date = (
            window_start_dt.date() if isinstance(window_start_dt, datetime) else window_start_dt
        )
        series_start = max(window_start_date, first_date)

        by_day: dict[date, float] = {}
        sample_count = 0
        for start, start_local, effort in rows:
            if effort is None:
                continue
            d = _local_day(start, start_local)
            # Bucket by LOCAL day, excluding anything after as_of's local day or
            # before the series window (the widened UTC filter may admit a few).
            if d > as_of_date or d < series_start:
                continue
            by_day[d] = by_day.get(d, 0.0) + float(effort)
            sample_count += 1

        n_days = max((as_of_date - series_start).days + 1, 1)
        daily = [by_day.get(series_start + timedelta(days=i), 0.0) for i in range(n_days)]

        return compute_readiness(
            daily,
            as_of=as_of_date,
            history_span_days=history_span_days,
            sample_count=sample_count,
        )
    except Exception:  # pragma: no cover - defensive guard
        logger.exception("readiness computation failed for user %s", user_id)
        return None
