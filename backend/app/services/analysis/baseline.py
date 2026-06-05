"""
Runner baseline trend substrate (M2).

Computes and persists the `RunnerBaseline` row: per-user rolling scalars plus a
`bucketed_trends` JSON map of context-bucketed per-run signal trends. Buckets
group runs by (effort, terrain, temperature band) so a trend only ever compares
like-for-like efforts; a bucket with fewer than `MIN_SAMPLES_FOR_TREND` samples
abstains rather than emitting a noisy trend.

This module is pure where it can be (the `temp_band`, `efficiency_factor`,
`bucket_key`, `compute_trend`, `compute_bucketed_trends`,
`compute_runner_baseline_scalars` functions take plain values and return plain
data) and only the `recompute_runner_baseline` persistence service touches the
DB. It is backend-only: nothing here is exposed via the API (M4 consumes it).

Signal sourcing:
- Efficiency factor reuses the per-activity `efficiency_analysis.average`
  already computed by `metrics.calculate_efficiency` when present, falling back
  to a summary-level speed/HR estimate.
- Decoupling is the already-computed `hr_drift` on the DerivedMetric; this
  module does NOT invent a separate decoupling number.
- Heart-rate recovery (HRR) is intentionally omitted: current activity streams
  have no post-effort recovery data to derive it from. It is deferred until a
  data source exists rather than fabricated.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from statistics import median
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Activity, DerivedMetric, RunnerBaseline

logger = logging.getLogger(__name__)

# Open-question threshold from the brief: a bucket needs at least this many
# samples before a trend is trustworthy enough to emit. Owner-tunable.
MIN_SAMPLES_FOR_TREND = 4

# A magnitude smaller than this (in percent) reads as "no real change".
_STABLE_PCT = 2.0


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _has_usable_hr(activity) -> bool:
    """Shared eligibility predicate: the activity has a positive average HR.

    Used for both the bucketed samples and the scalar sample_count so the two
    can never drift apart.
    """
    return bool(activity.avg_hr) and activity.avg_hr > 0


def temp_band(average_temp: Optional[float]) -> str:
    """Bucket an ambient temperature (degrees C) into a coarse band.

    Defensively coerces a non-numeric value (the source is untyped Strava
    `raw_summary` JSON) to "unknown" rather than raising, mirroring the same
    guard in `discount_signals.py` for the identical field.
    """
    if average_temp is None:
        return "unknown"
    try:
        average_temp = float(average_temp)
    except (TypeError, ValueError):
        return "unknown"
    if average_temp < 10:
        return "cold"
    if average_temp < 20:
        return "cool"
    if average_temp < 25:
        return "mild"
    return "hot"


def efficiency_factor(
    avg_speed_mps: Optional[float],
    avg_hr: Optional[float],
    efficiency_analysis: Optional[dict],
) -> Optional[float]:
    """Efficiency factor in m/min per bpm.

    Prefers the stream-derived `efficiency_analysis["average"]` when it is a
    positive number; otherwise estimates from summary speed and HR; returns
    None when neither is available.
    """
    if efficiency_analysis:
        avg = efficiency_analysis.get("average")
        if isinstance(avg, (int, float)) and not isinstance(avg, bool) and avg > 0:
            return float(avg)
    if avg_speed_mps and avg_hr and avg_hr > 0:
        return round(avg_speed_mps * 60.0 / avg_hr, 3)
    return None


def bucket_key(
    effort: Optional[str],
    is_hilly: Optional[bool],
    average_temp: Optional[float],
) -> str:
    """Composite context bucket: effort | terrain | temperature band."""
    terrain = "hilly" if is_hilly else "flat"
    return f"{effort or 'unknown'}|{terrain}|{temp_band(average_temp)}"


def compute_trend(values: list[float], *, higher_is_better: bool) -> Optional[dict]:
    """Least-squares trend over a chronological (oldest-first) value series.

    Returns a dict {direction, magnitude_pct, slope, n} or None when there are
    fewer than two points. `magnitude_pct` is the percent change between the
    fitted first and fitted last values; `direction` is "stable" when that
    magnitude is below `_STABLE_PCT`, otherwise "improving"/"declining"
    interpreted through `higher_is_better`.
    """
    n = len(values)
    if n < 2:
        return None

    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    slope = numerator / denominator if denominator else 0.0
    intercept = y_mean - slope * x_mean

    fitted_first = intercept + slope * xs[0]
    fitted_last = intercept + slope * xs[-1]

    if fitted_first != 0:
        magnitude_pct = (fitted_last - fitted_first) / abs(fitted_first) * 100.0
        is_stable = abs(magnitude_pct) < _STABLE_PCT
    else:
        # The fitted line crosses 0 at the first point, so percent change is
        # undefined; judge direction from the slope itself rather than wrongly
        # collapsing a real slope to "stable".
        magnitude_pct = 0.0
        is_stable = abs(slope) < 1e-9

    if is_stable:
        direction = "stable"
    else:
        rising = slope > 0
        if not higher_is_better:
            rising = not rising
        direction = "improving" if rising else "declining"

    return {
        "direction": direction,
        "magnitude_pct": round(magnitude_pct, 1),
        "slope": round(slope, 4),
        "n": n,
    }


def compute_bucketed_trends(
    samples: list[dict],
    min_samples: int = MIN_SAMPLES_FOR_TREND,
) -> dict:
    """Group per-run samples by bucket and emit a trend per bucket.

    `samples` is a list of {"date", "bucket", "ef", "hr_drift"} dicts. For each
    bucket, samples are ordered by date ascending. A bucket with fewer than
    `min_samples` samples abstains. Otherwise efficiency-factor and hr-drift
    trends are computed over the non-None values in date order.
    """
    grouped: dict[str, list[dict]] = {}
    for s in samples:
        grouped.setdefault(s["bucket"], []).append(s)

    result: dict[str, dict] = {}
    for bucket, items in grouped.items():
        ordered = sorted(items, key=lambda s: s["date"])
        count = len(ordered)
        if count < min_samples:
            result[bucket] = {"sample_count": count, "abstained": True}
            continue

        efs = [s["ef"] for s in ordered if s["ef"] is not None]
        drifts = [s["hr_drift"] for s in ordered if s["hr_drift"] is not None]
        result[bucket] = {
            "sample_count": count,
            "abstained": False,
            "efficiency_factor": compute_trend(efs, higher_is_better=True),
            "hr_drift": compute_trend(drifts, higher_is_better=False),
        }
    return result


def _percentile(values: list[float], pct: float) -> Optional[float]:
    """Linear-interpolated percentile of a value list (pct in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def compute_runner_baseline_scalars(rows: list[tuple]) -> dict:
    """Compute the scalar baseline fields from (activity, metric) pairs.

    Easy efforts (effort in {recovery, easy} with a positive avg_hr) drive the
    "typical easy" scalars. Weekly volume averages over the distinct ISO weeks
    present. Each field defaults to None when it has no supporting data.
    """
    easy_hrs: list[float] = []
    easy_paces: list[float] = []
    easy_effs: list[float] = []

    long_durations: list[int] = []  # from duration_class == "long"
    all_durations: list[int] = []   # fallback population for the 90th pct

    weekly_distance: dict[tuple, float] = {}
    weekly_count: dict[tuple, int] = {}

    eligible = 0

    for activity, metric in rows:
        if metric is None or not _has_usable_hr(activity):
            continue
        eligible += 1

        # Weekly volume — keyed by ISO (year, week).
        iso_year, iso_week, _ = activity.start_date.isocalendar()
        wk = (iso_year, iso_week)
        weekly_distance[wk] = weekly_distance.get(wk, 0.0) + (activity.distance_m or 0)
        weekly_count[wk] = weekly_count.get(wk, 0) + 1

        if activity.moving_time_s:
            all_durations.append(activity.moving_time_s)
        if getattr(metric, "duration_class", None) == "long" and activity.moving_time_s:
            long_durations.append(activity.moving_time_s)

        # Easy-effort population.
        if metric.effort in {"recovery", "easy"}:
            easy_hrs.append(activity.avg_hr)
            if activity.distance_m and activity.distance_m > 0:
                easy_paces.append(activity.moving_time_s / (activity.distance_m / 1000.0))
            ef = efficiency_factor(
                activity.average_speed_mps, activity.avg_hr,
                getattr(metric, "efficiency_analysis", None),
            )
            if ef is not None:
                easy_effs.append(ef)

    if long_durations:
        typical_long = max(long_durations)
    elif all_durations:
        p90 = _percentile(all_durations, 90.0)
        typical_long = int(round(p90)) if p90 is not None else None
    else:
        typical_long = None

    n_weeks = len(weekly_distance)
    typical_weekly_distance_m = (
        sum(weekly_distance.values()) / n_weeks if n_weeks else None
    )
    typical_weekly_run_count = (
        int(round(sum(weekly_count.values()) / n_weeks)) if n_weeks else None
    )

    return {
        "typical_easy_hr": round(median(easy_hrs), 1) if easy_hrs else None,
        "typical_easy_pace_s_per_km": round(median(easy_paces), 1) if easy_paces else None,
        "typical_efficiency": round(median(easy_effs), 3) if easy_effs else None,
        "typical_long_run_duration_s": typical_long,
        "typical_weekly_distance_m": (
            round(typical_weekly_distance_m, 1) if typical_weekly_distance_m is not None else None
        ),
        "typical_weekly_run_count": typical_weekly_run_count,
        "sample_count": eligible,
    }


# ---------------------------------------------------------------------------
# Persistence service
# ---------------------------------------------------------------------------

def recompute_runner_baseline(db: Session, user_id) -> Optional[RunnerBaseline]:
    """Recompute and upsert the single RunnerBaseline row for `user_id`.

    Idempotent: queries the user's non-deleted activities (with their
    DerivedMetric), builds per-run samples for activities that have a metric and
    an avg_hr, computes scalars + bucketed_trends, and writes them into the one
    RunnerBaseline row (creating it if absent). Returns the row, or None when the
    user has no eligible activity at all.
    """
    if not isinstance(user_id, uuid.UUID):
        user_id = uuid.UUID(str(user_id))

    stmt = (
        select(Activity)
        .options(selectinload(Activity.metrics))
        .where(Activity.user_id == user_id)
        .where(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.start_date.asc())
    )
    activities = db.execute(stmt).scalars().all()

    rows: list[tuple] = []
    samples: list[dict] = []
    for activity in activities:
        metric = activity.metrics
        if metric is None or not _has_usable_hr(activity):
            continue
        rows.append((activity, metric))

        average_temp = None
        if activity.raw_summary:
            average_temp = activity.raw_summary.get("average_temp")

        samples.append({
            "date": activity.start_date,
            "bucket": bucket_key(metric.effort, metric.is_hilly, average_temp),
            "ef": efficiency_factor(
                activity.average_speed_mps, activity.avg_hr, metric.efficiency_analysis
            ),
            "hr_drift": metric.hr_drift,
        })

    if not rows:
        return None

    scalars = compute_runner_baseline_scalars(rows)
    bucketed = compute_bucketed_trends(samples)

    baseline = (
        db.query(RunnerBaseline).filter(RunnerBaseline.user_id == user_id).first()
    )
    created = baseline is None
    if created:
        baseline = RunnerBaseline(user_id=user_id)
        db.add(baseline)

    baseline.typical_easy_hr = scalars["typical_easy_hr"]
    baseline.typical_easy_pace_s_per_km = scalars["typical_easy_pace_s_per_km"]
    baseline.typical_efficiency = scalars["typical_efficiency"]
    baseline.typical_long_run_duration_s = scalars["typical_long_run_duration_s"]
    baseline.typical_weekly_distance_m = scalars["typical_weekly_distance_m"]
    baseline.typical_weekly_run_count = scalars["typical_weekly_run_count"]
    baseline.sample_count = scalars["sample_count"]
    baseline.bucketed_trends = bucketed
    baseline.computed_at = datetime.now(timezone.utc)

    try:
        db.commit()
    except IntegrityError:
        # A concurrent recompute for the same user inserted the row first
        # (unique(user_id)); retry as an update of the now-existing row.
        if not created:
            raise
        db.rollback()
        return recompute_runner_baseline(db, user_id)
    db.refresh(baseline)
    return baseline
