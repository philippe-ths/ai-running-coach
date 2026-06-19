"""Weekly training-load report for the Load view (#209).

Aggregates per-activity ``effort_score`` (the cumulative training load in
Edwards "zone-minutes", #168/#186 — a load number, never an intensity verdict)
into calendar weeks (Monday start) and judges each week against the runner's own
recent norm: the trailing ``CHRONIC_WEEKS``-week average, with an optimal band of
``BAND_LOW``..``BAND_HIGH`` times that average (Strava Relative Effort style).
A week with fewer than ``CHRONIC_WEEKS`` prior weeks of history, or a zero
chronic average, abstains with ``no_baseline`` rather than judging.

Since #186 ``effort_score`` is one comparable scale across activities with and
without HR, so summing it across a mixed history is valid.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Activity, DerivedMetric
from app.schemas.trends import LoadActivityPoint, LoadResponse, LoadWeek
from app.services.analysis.classifier import Classification, compose_headline

# The chronic baseline window and the optimal band around it.
CHRONIC_WEEKS = 4
BAND_LOW = 0.8
BAND_HIGH = 1.3

# History served to the long-term trend chart.
MAX_WEEKS = 52


@dataclass
class LoadFact:
    """One activity's contribution, decoupled from the ORM for pure logic."""

    activity_id: UUID
    name: str
    day: date
    effort_score: float
    headline: Optional[str] = None


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _status(score: float, target_min: Optional[float], target_max: Optional[float]) -> str:
    if target_min is None or target_max is None:
        return "no_baseline"
    if score < target_min:
        return "below"
    if score > target_max:
        return "high"
    return "optimal"


def build_load_report(
    facts: list[LoadFact],
    today: date,
    series_start: Optional[date] = None,
) -> LoadResponse:
    """Pure aggregation: facts -> chronological weekly report ending this week.

    ``series_start`` lets the caller pin the first week of the series when it
    has bounded ``facts`` to a window but still knows older history exists (so
    the series length matches an unbounded build). When None, the first week is
    derived from ``facts`` as before. Either way it is clamped to the
    MAX_WEEKS floor.
    """
    current_week = week_start(today)
    # Bound the series so per-request cost stays flat as history grows.
    earliest_served = current_week - timedelta(weeks=MAX_WEEKS - 1)
    if series_start is not None:
        first_week = series_start
    elif facts:
        first_week = min(week_start(f.day) for f in facts)
    else:
        first_week = current_week
    first_week = max(first_week, earliest_served)

    by_week: dict[date, list[LoadFact]] = {}
    for f in facts:
        by_week.setdefault(week_start(f.day), []).append(f)

    n_weeks = (current_week - first_week).days // 7 + 1
    starts = [first_week + timedelta(weeks=i) for i in range(n_weeks)]
    scores = [round(sum(f.effort_score for f in by_week.get(s, [])), 1) for s in starts]

    weeks: list[LoadWeek] = []
    for i, start in enumerate(starts):
        target_min = target_max = None
        if i >= CHRONIC_WEEKS:
            chronic = sum(scores[i - CHRONIC_WEEKS:i]) / CHRONIC_WEEKS
            if chronic > 0:
                target_min = round(chronic * BAND_LOW, 1)
                target_max = round(chronic * BAND_HIGH, 1)

        daily = [0.0] * 7
        members = sorted(by_week.get(start, []), key=lambda f: f.day)
        for f in members:
            daily[f.day.weekday()] = round(daily[f.day.weekday()] + f.effort_score, 1)

        weeks.append(
            LoadWeek(
                week_start=start,
                score=scores[i],
                daily=daily,
                target_min=target_min,
                target_max=target_max,
                status=_status(scores[i], target_min, target_max),
                activities=[
                    LoadActivityPoint(
                        id=f.activity_id,
                        name=f.name,
                        date=f.day,
                        effort_score=round(f.effort_score, 1),
                        headline=f.headline,
                    )
                    for f in members
                ],
            )
        )

    return LoadResponse(weeks=weeks)


def get_load_report(db: Session, today: Optional[date] = None) -> LoadResponse:
    # Bound the scan to the served window (#362). build_load_report only keeps
    # weeks from `earliest_served` onward, so an activity older than that Monday
    # is discarded anyway — fetching it was wasted work on an unbounded
    # full-history scan. We compute `today` once and reuse it for both the SQL
    # bound and the pure report build so a midnight rollover can't disagree.
    resolved_today = today or date.today()
    earliest_served = week_start(resolved_today) - timedelta(weeks=MAX_WEEKS - 1)

    # Project only the columns the report needs instead of materializing full
    # Activity + DerivedMetric ORM objects. This avoids loading the per-row
    # DerivedMetric JSON blobs (time_in_zones, interval_structure,
    # discount_signals, ...) the report never reads. raw_summary is still
    # loaded because the headline depends on its sport_type/trainer keys
    # (see classifier.compose_headline); see PR notes for why it cannot be
    # dropped without changing output. The inner join + skip mirrors the prior
    # joinedload + `metrics is None` filter exactly.
    rows = (
        db.query(
            Activity.id,
            Activity.name,
            Activity.start_date,
            Activity.type,
            Activity.distance_m,
            Activity.raw_summary,
            DerivedMetric.effort_score,
            DerivedMetric.effort,
            DerivedMetric.duration_class,
            DerivedMetric.structure,
            DerivedMetric.is_hilly,
            DerivedMetric.is_race,
        )
        .join(DerivedMetric, DerivedMetric.activity_id == Activity.id)
        .filter(Activity.is_deleted.is_(False))
        .filter(Activity.start_date >= earliest_served)
        # Deterministic chronological order. build_load_report sorts members
        # by date only (a LoadFact carries no time), so a stable sort preserves
        # this query order WITHIN a day — without an explicit ORDER BY the
        # same-day activity order is whatever physical order the scan yields
        # (it was unordered before too; the #362 join just perturbed it). Order
        # by start_date so same-day activities read chronologically, with id as
        # a total-order tiebreaker.
        .order_by(Activity.start_date.asc(), Activity.id.asc())
        .all()
    )

    # Preserve the series length exactly. The unbounded build clamped the
    # first week to `earliest_served` whenever any contributing activity was
    # older than the window; we reproduce that with one cheap existence probe
    # instead of materializing the old rows. No older activity -> the series
    # starts at the first in-window activity, as before.
    older_exists = (
        db.query(Activity.id)
        .join(DerivedMetric, DerivedMetric.activity_id == Activity.id)
        .filter(Activity.is_deleted.is_(False))
        .filter(Activity.start_date < earliest_served)
        .first()
    ) is not None
    series_start = earliest_served if older_exists else None

    facts = []
    for r in rows:
        if r.effort_score is None:
            continue
        start = r.start_date.date() if isinstance(r.start_date, datetime) else r.start_date
        metrics = SimpleNamespace(
            effort_score=r.effort_score,
            effort=r.effort,
            duration_class=r.duration_class,
            structure=r.structure,
            is_hilly=r.is_hilly,
            is_race=r.is_race,
        )
        activity = SimpleNamespace(
            type=r.type,
            distance_m=r.distance_m,
            raw_summary=r.raw_summary,
        )
        facts.append(
            LoadFact(
                activity_id=r.id,
                name=r.name,
                day=start,
                effort_score=float(r.effort_score),
                headline=compose_headline(activity, Classification.from_metrics(metrics)),
            )
        )
    return build_load_report(facts, resolved_today, series_start=series_start)
