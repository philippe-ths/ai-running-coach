"""The fact stream, and the questions asked of it (#804).

"What did this runner do in this window" was re-derived in nine modules. The
projection (soft-delete + owner + date), the local-day derivation, the range
arithmetic, the day/week/period bucketing, the continuous fill, the zone
accumulation and the per-day norm each had several homes, and the coach pack and
the Trends page kept agreeing only because each already reached into the other's
private helpers to borrow one.

This module is the layer underneath ``app.services.weeks``. ``weeks`` owns *where
a week begins*; this owns *the stream of facts and every question asked of it*:

  1. **The fact.** ``ActivityFact`` and the single owner-scoped, soft-delete-aware
     projection ``query_facts`` that produces it, plus ``local_day`` — the one
     UTC-to-runner-local calendar-day derivation.
  2. **Windows.** Range keys, rolling and calendar framings, the previous-period
     comparison window, the calendar period for a range.
  3. **Buckets.** One ``Bucket`` for every granularity, one continuous fill, one
     edge-coverage rule.
  4. **Zones.** One 5-to-3 zone collapse and one accumulation.
  5. **Rollups.** The per-metric sum, the runs-only distinction, the clamped
     per-day norm and the deadbanded direction verdict.

Consumers ask questions; nobody slices lists or re-states a filter.

Distinctions that look like duplication and are NOT, all preserved here
explicitly, because collapsing any one of them silently changes a number:

  * **Rolling vs calendar framing.** A rolling window trails ``today``; a calendar
    window snaps to the runner's week/month/quarter/half/year. They resolve to
    different ``since`` values, different previous-period comparisons and
    different bucket keys.
  * **The clamped per-day norm.** ``baseline_window`` clamps to the runner's first
    activity so a short history is not deflated by dividing it across weeks the
    runner had not started training (#451).
  * **Runs-only vs all-activity totals.** Reported side by side, never merged, so a
    walk is never read as a run.
  * **Edge-bucket coverage.** A bucket straddling the window boundary keeps its
    in-period totals separate from its out-of-period ones (#566).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, DerivedMetric
from app.services.weeks import MONDAY, week_start

import calendar as _cal

# ---------------------------------------------------------------------------
# 1. The fact and its projection
# ---------------------------------------------------------------------------


def local_day(start_date, start_date_local) -> date:
    """The runner's LOCAL calendar day for an activity (#399/#507).

    ``start_date_local`` when present, else the UTC ``start_date`` — the
    ``Activity.local_start`` convention, expressed once so every signal that
    buckets by day (trends, volume, recent training, readiness, training load)
    agrees on the boundary. A late-evening local run that has rolled to the next
    UTC day, and a same-local-day double session, land on ONE day everywhere.

    Accepts either a ``datetime`` or an already-narrowed ``date``.
    """
    chosen = start_date_local if start_date_local is not None else start_date
    return chosen.date() if isinstance(chosen, datetime) else chosen


class ActivityFact:
    """One row per activity — the minimal projection needed for trend charts."""

    __slots__ = (
        "activity_id", "local_date", "activity_type", "user_intent",
        "distance_m", "moving_time_s", "elapsed_time_s",
        "elev_gain_m", "avg_hr", "avg_cadence", "average_speed_mps",
        "effort_score", "effort", "time_in_zones",
        # #650: session shape, projected ONLY when a caller opts in
        # (include_session_shape); None otherwise, so the wide/10-year scans stay lean.
        "structure", "interval_structure", "duration_class",
        # ADR 0026 Slice 2 (#670): within-run cardiac drift for the recent_weeks per-
        # session read, projected under the same opt-in; None on the lean scans.
        "hr_drift",
        # #804: the UTC instant the projection's own window filter binds to. Carried
        # so a wider fetch can be narrowed to a sub-window by exactly the predicate
        # the narrow query would have applied (see `facts_in_window`). Never used for
        # bucketing — that is always `local_date`.
        "start_date",
    )

    def __init__(self, activity: Activity):
        self.activity_id = activity.id
        # Bucket by the runner's local calendar day (#399), falling back to UTC.
        self.local_date: date = activity.local_start.date()
        self.start_date = activity.start_date
        self.activity_type = activity.type
        self.user_intent = activity.user_intent
        self.distance_m = activity.distance_m or 0
        self.moving_time_s = activity.moving_time_s or 0
        self.elapsed_time_s = activity.elapsed_time_s or 0
        self.elev_gain_m = activity.elev_gain_m or 0.0
        self.avg_hr = activity.avg_hr
        self.avg_cadence = activity.avg_cadence
        self.average_speed_mps = activity.average_speed_mps
        self.effort_score: Optional[float] = (
            activity.metrics.effort_score if activity.metrics else None
        )
        # Effort axis (ADR 0007): recovery|easy|moderate|tempo|hard. Used to
        # count "hard days" on the dashboard without an HR/name heuristic.
        self.effort: Optional[str] = (
            activity.metrics.effort if activity.metrics else None
        )
        self.time_in_zones: Optional[dict] = (
            activity.metrics.time_in_zones if activity.metrics else None
        )
        # #650: session shape (classifier structure + interval structure) for the
        # per-session marker; None when there is no DerivedMetric.
        self.structure: Optional[str] = (
            activity.metrics.structure if activity.metrics else None
        )
        self.interval_structure: Optional[dict] = (
            activity.metrics.interval_structure if activity.metrics else None
        )
        self.duration_class: Optional[str] = (
            activity.metrics.duration_class if activity.metrics else None
        )
        self.hr_drift: Optional[float] = (
            activity.metrics.hr_drift if activity.metrics else None
        )

    @classmethod
    def from_row(cls, row) -> "ActivityFact":
        """Build from a column-projection Row instead of a full Activity ORM
        object (#367). The queries only read the ~14 scalar fields below, so
        projecting them avoids materializing the Activity/DerivedMetric JSON blobs
        (raw_summary, interval_structure, discount_signals, ...) the charts never
        touch. The LEFT join makes the metric columns NULL for an activity without
        a DerivedMetric, reproducing the prior `activity.metrics ... else None`.
        """
        self = cls.__new__(cls)
        self.activity_id = row.id
        self.local_date = local_day(row.start_date, row.start_date_local)
        self.start_date = row.start_date
        self.activity_type = row.type
        self.user_intent = row.user_intent
        self.distance_m = row.distance_m or 0
        self.moving_time_s = row.moving_time_s or 0
        self.elapsed_time_s = row.elapsed_time_s or 0
        self.elev_gain_m = row.elev_gain_m or 0.0
        self.avg_hr = row.avg_hr
        self.avg_cadence = row.avg_cadence
        self.average_speed_mps = row.average_speed_mps
        self.effort_score = row.effort_score
        self.effort = row.effort
        self.time_in_zones = row.time_in_zones
        # #650: present only when the caller opted into the shape projection; absent
        # from the lean default projection, so getattr falls back to None.
        self.structure = getattr(row, "structure", None)
        self.interval_structure = getattr(row, "interval_structure", None)
        self.duration_class = getattr(row, "duration_class", None)
        self.hr_drift = getattr(row, "hr_drift", None)
        return self

    @property
    def effective_type(self) -> str:
        return self.user_intent if self.user_intent else self.activity_type

    @property
    def pace_sec_per_km(self) -> Optional[float]:
        """Pace in seconds/km. None if distance is zero."""
        if self.distance_m <= 0:
            return None
        return (self.moving_time_s / self.distance_m) * 1000


def window_bounds(
    start_date: Optional[date], end_date: Optional[date]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """The UTC instants a ``[start_date, end_date)`` day window binds to.

    The one place the half-open day-to-instant convention lives: start inclusive at
    midnight, end EXCLUSIVE at midnight. Stated once so the SQL filter and the
    in-memory narrowing in ``facts_in_window`` can never drift apart.
    """
    lo = datetime.combine(start_date, datetime.min.time()) if start_date else None
    hi = datetime.combine(end_date, datetime.min.time()) if end_date else None
    return lo, hi


def facts_in_window(
    facts: List[ActivityFact], start_date: Optional[date], end_date: Optional[date]
) -> List[ActivityFact]:
    """Narrow an already-fetched fact list to a sub-window, applying EXACTLY the
    predicate ``query_facts`` would have pushed to SQL (#804).

    This is what lets one wide fetch answer several overlapping window questions in
    a single pack build instead of re-issuing the projection per signal. It binds
    to the UTC ``start_date``, not ``local_date``, precisely because the SQL filter
    does; using the local day here would silently move a near-midnight activity.
    """
    lo, hi = window_bounds(start_date, end_date)
    return [
        f
        for f in facts
        if (lo is None or f.start_date >= lo) and (hi is None or f.start_date < hi)
    ]


def query_facts(
    db: Session,
    start_date: Optional[date],
    end_date: Optional[date],
    types: Optional[List[str]] = None,
    *,
    user_id=None,
    include_session_shape: bool = False,
) -> List[ActivityFact]:
    """THE projection: the owner's non-deleted activities in ``[start, end)``.

    Soft-delete, owner scoping and the date window are stated here once. ``user_id``
    scopes the query to a single owner; pass it at every call site so a multi-user
    read can never leak across tenants.

    Only the columns ``ActivityFact`` reads are projected (#367), with a LEFT outer
    join so activities without a ``DerivedMetric`` survive with NULL metric columns.
    ``DerivedMetric.activity_id`` is unique, so the join never duplicates a row.
    """
    stmt = (
        select(
            Activity.id,
            Activity.start_date,
            Activity.start_date_local,
            Activity.type,
            Activity.user_intent,
            Activity.distance_m,
            Activity.moving_time_s,
            Activity.elapsed_time_s,
            Activity.elev_gain_m,
            Activity.avg_hr,
            Activity.avg_cadence,
            Activity.average_speed_mps,
            DerivedMetric.effort_score,
            DerivedMetric.effort,
            DerivedMetric.time_in_zones,
            # #650: the session-shape columns ride the projection ONLY on opt-in, so the
            # lean scans (trends, the 10-year training-history) never load them.
            *(
                (
                    DerivedMetric.structure,
                    DerivedMetric.interval_structure,
                    DerivedMetric.duration_class,
                    DerivedMetric.hr_drift,
                )
                if include_session_shape
                else ()
            ),
        )
        .outerjoin(DerivedMetric, DerivedMetric.activity_id == Activity.id)
        .where(Activity.is_deleted == False)  # noqa: E712
        # Chronological, with id as a total-order tiebreaker (#804). Without the
        # tiebreaker two activities sharing a start_date order arbitrarily, which
        # would make a wide fetch narrowed by `facts_in_window` differ from the
        # equivalent narrow query. The mirror of the same rule in training_load.py.
        .order_by(Activity.start_date.asc(), Activity.id.asc())
    )
    if user_id is not None:
        stmt = stmt.where(Activity.user_id == user_id)
    lo, hi = window_bounds(start_date, end_date)
    if lo is not None:
        stmt = stmt.where(Activity.start_date >= lo)
    if hi is not None:
        stmt = stmt.where(Activity.start_date < hi)

    facts = [ActivityFact.from_row(r) for r in db.execute(stmt).all()]

    if types:
        type_set = {t.lower() for t in types}
        facts = [f for f in facts if f.activity_type.lower() in type_set]

    return facts


# ---------------------------------------------------------------------------
# 2. Windows
# ---------------------------------------------------------------------------

ALLOWED_RANGES = {"7D", "30D", "3M", "6M", "1Y", "ALL"}

RANGE_DAYS: Dict[str, Optional[int]] = {
    "7D": 7,
    "30D": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "ALL": None,
}

# Range key -> the rolling window length in days (the finite subset of RANGE_DAYS).
RANGE_WINDOW_DAYS = {"7D": 7, "30D": 30, "3M": 90, "6M": 180, "1Y": 365}

# Term-scaled norm baseline: a longer term reads "typical" from a longer history.
# Capped at ~1 year to stay within a typical runner's data; the actual span is
# further clamped to available history (so pre-history days never deflate the rate).
BASELINE_DAYS_BY_RANGE = {"7D": 84, "30D": 168, "3M": 365, "6M": 365, "1Y": 365}
BASELINE_LABEL_BY_RANGE = {
    "7D": "the last 12 weeks",
    "30D": "the last 6 months",
    "3M": "the last year",
    "6M": "the last year",
    "1Y": "the last year",
}


def resolve_since(range_key: str) -> Optional[date]:
    """The earliest local date a rolling range includes, or None for ALL.

    The window is inclusive on both ends (``since`` .. ``today``), so a range of N
    days subtracts N-1 to span exactly N calendar days. e.g. 7D with today = Jun 9
    covers Jun 3-Jun 9 inclusive, not Jun 2-Jun 9 (#179).
    """
    days = RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None
    return date.today() - timedelta(days=days - 1)


def period_window(range_key: str) -> Optional[Tuple[date, date]]:
    """``(current_start, previous_start)`` for a fixed rolling range, or None for ALL.

    The current window is ``[current_start, today]`` (inclusive) and the previous
    window is ``[previous_start, current_start)``. Both span exactly
    ``RANGE_DAYS[range]`` calendar days with no gap or overlap at the boundary, so
    period-over-period deltas line up with the charts (#179).
    """
    days = RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None
    current_start = resolve_since(range_key)
    assert current_start is not None  # days is not None here
    previous_start = current_start - timedelta(days=days)
    return current_start, previous_start


def calendar_period(
    range_key: str, as_of: date, week_starts_on: int = MONDAY
) -> Tuple[date, date, int, str]:
    """The current calendar period for a range: ``(start, last_day, length_days, label)``.

    The period runs from ``start`` to ``last_day``; the caller compares to-date
    (``start``..``as_of``). Only the weekly (7D) period depends on
    ``week_starts_on`` (#676).
    """
    if range_key == "7D":
        start = week_start(as_of, week_starts_on)
        last = start + timedelta(days=6)
        label = "This week"
    elif range_key == "30D":
        start = as_of.replace(day=1)
        last = as_of.replace(day=_cal.monthrange(as_of.year, as_of.month)[1])
        label = "This month"
    elif range_key == "3M":
        q_first_month = ((as_of.month - 1) // 3) * 3 + 1
        start = date(as_of.year, q_first_month, 1)
        end_month = q_first_month + 2
        last = date(as_of.year, end_month, _cal.monthrange(as_of.year, end_month)[1])
        label = "This quarter"
    elif range_key == "6M":
        if as_of.month <= 6:
            start, last = date(as_of.year, 1, 1), date(as_of.year, 6, 30)
        else:
            start, last = date(as_of.year, 7, 1), date(as_of.year, 12, 31)
        label = "This half-year"
    else:  # "1Y"
        start, last = date(as_of.year, 1, 1), date(as_of.year, 12, 31)
        label = "This year"
    return start, last, (last - start).days + 1, label


def resolve_window(
    range_key: str, mode: str = "rolling", today: Optional[date] = None
) -> Tuple[Optional[date], Optional[date], Optional[date]]:
    """``(since, prev_start, prev_end)`` for the (range, mode) — the #400 global toggle.

    The current window is ``[since, today]`` inclusive; the previous comparison
    window is ``[prev_start, prev_end)``. Returns ``(None, None, None)`` for ALL
    (whole history, no previous).

    - ``rolling``: ``since`` is ``today - (N-1)`` and the previous window is the
      equal-length block immediately before it.
    - ``calendar``: ``since`` is the start of the current calendar period (week/
      month/quarter/half/year) and the previous window is the ENTIRE previous
      calendar period (e.g. Jun 1-20 to-date vs ALL of May), so "vs last month"
      reads against last month's full total — it runs behind for most of the
      period and catches up by period-end (#413).
    """
    today = today or date.today()
    days = RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None, None, None
    if mode == "calendar":
        p_start, _, _, _ = calendar_period(range_key.upper(), today)
        prev_p_start, _, _, _ = calendar_period(
            range_key.upper(), p_start - timedelta(days=1)
        )
        # Previous = the whole prior calendar period: [prev_p_start, p_start).
        return p_start, prev_p_start, p_start
    since = today - timedelta(days=days - 1)
    return since, since - timedelta(days=days), since


# ---------------------------------------------------------------------------
# 3. Buckets
# ---------------------------------------------------------------------------

# A fixed Monday reference for deterministic fortnight alignment (1970-01-05 is a
# Monday). Anchoring 2-week bins to it keeps boundaries stable regardless of the
# window start, so the same fortnights line up across ranges and the bars don't
# shift when the runner changes the selected range.
EPOCH_MONDAY = date(1970, 1, 5)

# In rolling mode a day's bucket is chosen by its offset back from the anchor
# (today): the newest bin ends today and each older bin is the preceding fixed-width
# block. "month" has no fixed length, so a rolling month is a 30-day block.
ROLLING_BIN_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30}


class DailyFact:
    """One row per local date — sums distance / time across all activities."""

    __slots__ = (
        "local_date", "total_distance_m", "total_moving_time_s",
        "total_elapsed_time_s", "total_elev_gain_m", "total_effort_score",
        "activity_count",
    )

    def __init__(self, local_date: date):
        self.local_date = local_date
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.total_elapsed_time_s = 0
        self.total_elev_gain_m = 0.0
        self.total_effort_score = 0.0
        self.activity_count = 0

    def add(self, fact: ActivityFact):
        self.total_distance_m += fact.distance_m
        self.total_moving_time_s += fact.moving_time_s
        self.total_elapsed_time_s += fact.elapsed_time_s
        self.total_elev_gain_m += fact.elev_gain_m
        if fact.effort_score:
            self.total_effort_score += fact.effort_score
        self.activity_count += 1


class Bucket:
    """One aggregation bucket, at ANY granularity (#804).

    Replaces the two classes that differed by the name of their start field and
    shared a byte-identical accumulate step. ``week_start`` and ``period_start``
    remain as read-only aliases of ``start`` so the chart-point constructors and
    the tests that name a granularity still read naturally.

    Edge-bucket coverage (#566): a bucket rarely aligns to the selected window, so
    ``in_period_days``/``out_of_period_days`` record the split and the
    ``out_of_period_*`` totals carry the value of the days OUTSIDE the window.
    ``total_*`` always stays the in-period sum; the chart stacks the faded segment
    on top so the bar shows the whole week/fortnight/month.
    """

    __slots__ = (
        "start", "total_distance_m", "total_moving_time_s",
        "total_effort_score", "activity_count",
        "in_period_days", "out_of_period_days",
        "out_of_period_distance_m", "out_of_period_moving_time_s",
        "out_of_period_effort_score",
    )

    def __init__(self, start: date):
        self.start = start
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.total_effort_score = 0.0
        self.activity_count = 0
        self.in_period_days = 0
        self.out_of_period_days = 0
        self.out_of_period_distance_m = 0
        self.out_of_period_moving_time_s = 0
        self.out_of_period_effort_score = 0.0

    @property
    def week_start(self) -> date:
        return self.start

    @property
    def period_start(self) -> date:
        return self.start

    def add(self, daily: DailyFact):
        self.total_distance_m += daily.total_distance_m
        self.total_moving_time_s += daily.total_moving_time_s
        self.total_effort_score += daily.total_effort_score
        self.activity_count += daily.activity_count


def rolling_bin_start(d: date, anchor: date, bin_days: int) -> date:
    """First local day of the anchor-aligned rolling bin of width ``bin_days`` that
    ``d`` falls in. The newest bin is ``[anchor - bin_days + 1, anchor]``; older bins
    step back by ``bin_days``. ``d`` is assumed on or before ``anchor``."""
    k = (anchor - d).days // bin_days
    return anchor - timedelta(days=(k + 1) * bin_days - 1)


def period_start(d: date, period: str, week_starts_on: int = MONDAY) -> date:
    """First local day of the calendar bucket that ``d`` falls in.

    - ``weekly``: the runner's week boundary.
    - ``biweekly``: 14-day bins aligned to the week-start grid (fortnight parity).
    - ``monthly``: the first of the calendar month.

    ``week_starts_on`` (0=Monday default, 6=Sunday) shifts the weekly and biweekly
    grids (#676); monthly is unaffected.
    """
    if period == "monthly":
        return d.replace(day=1)
    if period == "weekly":
        return week_start(d, week_starts_on)
    # biweekly: snap to the bin's starting week boundary by fortnight parity. The
    # parity epoch is the week-start grid's own epoch so weekly and biweekly agree.
    epoch = week_start(EPOCH_MONDAY, week_starts_on)
    start = week_start(d, week_starts_on)
    weeks = (start - epoch).days // 7
    if weeks % 2 == 1:
        start -= timedelta(days=7)
    return start


def next_period_start(start: date, period: str) -> date:
    """The start of the calendar bucket immediately after ``start`` (continuous fill)."""
    if period == "monthly":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    if period == "weekly":
        return start + timedelta(days=7)
    return start + timedelta(days=14)


def coverage_days(
    span_start: date,
    span_end: date,
    period_start_: Optional[date],
    period_end: date,
) -> Tuple[int, int]:
    """In-period vs out-of-period day counts for a bucket spanning ``[span_start,
    span_end]`` inclusive against the selected window (#566).

    Returns ``(in_period_days, out_of_period_days)``; when ``period_start_`` is
    ``None`` (the ALL range, no window) the bucket is fully in-period.
    """
    total_days = (span_end - span_start).days + 1
    if period_start_ is None:
        return total_days, 0
    lo = max(span_start, period_start_)
    hi = min(span_end, period_end)
    in_days = (hi - lo).days + 1 if hi >= lo else 0
    return in_days, total_days - in_days


def build_daily_facts(activity_facts: List[ActivityFact]) -> List[DailyFact]:
    """Collapse activity facts into one row per local date."""
    buckets: Dict[date, DailyFact] = {}
    for af in activity_facts:
        if af.local_date not in buckets:
            buckets[af.local_date] = DailyFact(af.local_date)
        buckets[af.local_date].add(af)
    return sorted(buckets.values(), key=lambda d: d.local_date)


def bucket_key_fn(
    period: str, rolling_anchor: Optional[date], week_starts_on: int = MONDAY
) -> Callable[[date], date]:
    """The one bucket-keying rule, for any granularity and either framing (#630).

    ``rolling_anchor`` set means today-anchored fixed-width blocks (7/14/30 days);
    ``None`` means the calendar grid (the runner's week boundary, the fortnight
    parity grid, or the 1st of the month).
    """
    if rolling_anchor is not None:
        bin_days = ROLLING_BIN_DAYS[period]
        return lambda d: rolling_bin_start(d, rolling_anchor, bin_days)
    return lambda d: period_start(d, period, week_starts_on)


def bucket_daily_facts(
    daily_facts: List[DailyFact],
    period: str,
    *,
    since: Optional[date],
    end: date,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
    pre_window_daily: Optional[List[DailyFact]] = None,
) -> List[Bucket]:
    """THE bucketer: roll daily facts into ``period`` buckets over ``[since, end]``.

    Aggregates from real data, fills every empty bucket across the window so charts
    have continuous x-axes, then applies the edge-coverage rule (#566/#630): a
    bucket that only partly overlaps the window records the split, and
    ``pre_window_daily`` (days just before ``since``) folds the out-of-window value
    into the matching displayed bucket so the bar can show the whole period with the
    excluded part faded.

    Every bucket's coverage is set from its own span below, so the two prior bucket
    classes' differing ``in_period_days`` construction defaults (7 for weeks, 0 for
    periods) were both dead and are not reproduced.
    """
    key = bucket_key_fn(period, rolling_anchor, week_starts_on)

    def advance(cur: date) -> date:
        if rolling_anchor is not None:
            return cur + timedelta(days=ROLLING_BIN_DAYS[period])
        return next_period_start(cur, period)

    def span_end(start: date) -> date:
        if rolling_anchor is not None:
            return start + timedelta(days=ROLLING_BIN_DAYS[period] - 1)
        return next_period_start(start, period) - timedelta(days=1)

    buckets: Dict[date, Bucket] = {}
    for df in daily_facts:
        k = key(df.local_date)
        if k not in buckets:
            buckets[k] = Bucket(k)
        buckets[k].add(df)

    end_key = key(end)
    if since is not None:
        start_key = key(since)
    elif daily_facts:
        start_key = key(daily_facts[0].local_date)
    else:
        start_key = end_key

    cursor = start_key
    while cursor <= end_key:
        if cursor not in buckets:
            buckets[cursor] = Bucket(cursor)
        cursor = advance(cursor)

    for b in buckets.values():
        b.in_period_days, b.out_of_period_days = coverage_days(
            b.start, span_end(b.start), since, end
        )

    # Carry the out-of-window value of a leading edge bucket's earlier days, keyed the
    # same way the buckets are. Pre-window days whose bucket is not displayed (older
    # than the leading bucket) are ignored.
    for df in pre_window_daily or []:
        bucket = buckets.get(key(df.local_date))
        if bucket is not None:
            bucket.out_of_period_distance_m += df.total_distance_m
            bucket.out_of_period_moving_time_s += df.total_moving_time_s
            bucket.out_of_period_effort_score += df.total_effort_score

    return sorted(buckets.values(), key=lambda b: b.start)


def fill_days(
    daily_facts: List[DailyFact], start: date, end: date
) -> List[DailyFact]:
    """Every day in ``[start, end]``, existing facts kept and gaps filled with zeros,
    so a chart has a continuous x-axis."""
    existing = {df.local_date: df for df in daily_facts}
    result: List[DailyFact] = []
    cursor = start
    while cursor <= end:
        result.append(existing.get(cursor, DailyFact(cursor)))
        cursor += timedelta(days=1)
    return result


# ---------------------------------------------------------------------------
# 4. Zones
# ---------------------------------------------------------------------------


def collapse_to_3_zones(time_in_zones: dict) -> Tuple[int, int, int]:
    """Collapse a 5-zone dict into 3-zone seconds: ``(easy, moderate, hard)``.

    Easy     = Z1 + Z2  (< 70% max HR)
    Moderate = Z3       (70-80% max HR)
    Hard     = Z4 + Z5  (> 80% max HR)
    """
    z1 = time_in_zones.get("Z1", 0) or 0
    z2 = time_in_zones.get("Z2", 0) or 0
    z3 = time_in_zones.get("Z3", 0) or 0
    z4 = time_in_zones.get("Z4", 0) or 0
    z5 = time_in_zones.get("Z5", 0) or 0
    return (z1 + z2, z3, z4 + z5)


def sum_zone_seconds(facts: List[ActivityFact]) -> Tuple[int, int, int]:
    """Total ``(easy, moderate, hard)`` seconds over a fact list. Facts with no zone
    data contribute nothing."""
    easy = mod = hard = 0
    for af in facts:
        if not af.time_in_zones:
            continue
        e, m, h = collapse_to_3_zones(af.time_in_zones)
        easy += e
        mod += m
        hard += h
    return easy, mod, hard


def bucket_zone_seconds(
    facts: List[ActivityFact], key: Callable[[date], date]
) -> Dict[date, Tuple[int, int, int]]:
    """``(easy, moderate, hard)`` seconds accumulated per bucket key.

    The one zone-accumulation loop. ``key`` must be the SAME keying the value
    buckets used, or the zone bars would not line up with them.
    """
    out: Dict[date, Tuple[int, int, int]] = {}
    for af in facts:
        if not af.time_in_zones:
            continue
        k = key(af.local_date)
        e, m, h = collapse_to_3_zones(af.time_in_zones)
        prev = out.get(k, (0, 0, 0))
        out[k] = (prev[0] + e, prev[1] + m, prev[2] + h)
    return out


def zone_minutes(facts: List[ActivityFact]) -> Tuple[float, float, float]:
    """Minutes in each HR band (easy, moderate, hard) over a window (#385)."""
    easy_s, mod_s, hard_s = sum_zone_seconds(facts)
    return round(easy_s / 60, 1), round(mod_s / 60, 1), round(hard_s / 60, 1)


# ---------------------------------------------------------------------------
# 5. Rollups and norms
# ---------------------------------------------------------------------------

# The metrics compared, in a fixed order (byte-stable pack).
METRICS = ("sessions", "distance_m", "moving_time_s", "effort_score")

BASELINE_WEEKS = 12        # the stable norm window (history before the current 7d)
RECENT_WEEKS = 4           # the recent-context norm window
DEADBAND_PCT = 15.0        # within +/- this of norm reads as in_line
# Below this many activities in the baseline, history is too thin to call a norm —
# every direction abstains as no_norm (mirrors the M9/baseline abstain tiers).
MIN_BASELINE_ACTIVITIES = 4


def is_run(fact: Any) -> bool:
    """Runs-only vs all-activity is a real distinction, never a merge: the holistic
    total is the cardio view the runner intends, the runs-only figure is alongside so
    a walk is never read as a run."""
    return (getattr(fact, "activity_type", "") or "").lower() == "run"


def metric_value(fact: Any, metric: str) -> float:
    if metric == "sessions":
        return 1
    if metric == "distance_m":
        return fact.distance_m or 0
    if metric == "moving_time_s":
        return fact.moving_time_s or 0
    if metric == "effort_score":
        return fact.effort_score or 0
    return 0


def sum_metric(facts: List[Any], metric: str, *, runs_only: bool = False) -> float:
    return sum(
        metric_value(f, metric) for f in facts if (not runs_only or is_run(f))
    )


def direction(current: float, norm: Optional[float], factor: float) -> str:
    """Label ``current`` against a norm pro-rated by ``factor`` (1.0 for a full
    window, days_elapsed/7 for a partial calendar week). A deadband keeps small
    fluctuations reading as ``in_line``."""
    if norm is None:
        return "no_norm"
    comparable = norm * factor
    if comparable <= 0:
        return "no_norm"
    pct = (current - comparable) / comparable * 100.0
    if pct > DEADBAND_PCT:
        return "up"
    if pct < -DEADBAND_PCT:
        return "down"
    return "in_line"


def baseline_window(
    facts: List[Any], baseline_end: date, nominal_days: int
) -> Optional[Tuple[date, date, int]]:
    """The norm baseline date range ending on ``baseline_end``, CLAMPED so it never
    starts before the runner's first activity (#451).

    The clamp is the load-bearing part: without it, pre-history zeros deflate the
    per-day rate, so a newer or returning runner's "typical" is understated and
    every current window reads as an increase. Returns ``(start, end, day_count)``
    or ``None`` when the clamped window is empty.
    """
    nominal_start = baseline_end - timedelta(days=nominal_days - 1)
    earliest = min((f.local_date for f in facts), default=None)
    start = nominal_start if earliest is None else max(nominal_start, earliest)
    if start > baseline_end:
        return None
    return start, baseline_end, (baseline_end - start).days + 1


def norm_per_day(
    facts: List[Any], start: date, end: date, day_count: int
) -> Dict[str, Optional[float]]:
    """Per-metric average daily output over ``[start, end]`` (``day_count`` calendar
    days), or None per metric when too few activities fall in it.

    CALENDAR days (not active days) are the denominator, so rest days count as zero —
    a true average daily rate that scales to any window length. This is the single
    definition of "typical" the coach pack, the Trends page and recent_training all
    read, so the product cannot disagree with itself about it.
    """
    window = [f for f in facts if start <= f.local_date <= end]
    if len(window) < MIN_BASELINE_ACTIVITIES:
        return {m: None for m in METRICS}
    return {m: sum_metric(window, m) / day_count for m in METRICS}
