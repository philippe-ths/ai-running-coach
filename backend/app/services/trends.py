"""
Trends pipeline — turns raw Activity rows into daily/weekly aggregated facts.

All grouping uses the activity's local start_date (timezone-aware).
If multiple activities occur on the same local date, they are summed.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import Activity, DerivedMetric, UserProfile
from app.services.weeks import MONDAY, resolve_week_start, week_start
from app.schemas.trends import (
    TrendsResponse,
    TrendsSummary,
    WeeklyDistancePoint,
    WeeklyTimePoint,
    WeeklySufferScorePoint,
    DailyDistancePoint,
    DailyTimePoint,
    SufferScorePoint,
    DailySufferScorePoint,
    EfficiencyPoint,
    ZoneLoadWeekPoint,
    DailyZoneLoadPoint,
    PeriodDistancePoint,
    PeriodTimePoint,
    PeriodSufferScorePoint,
    PeriodZoneLoadPoint,
    WeeklyStatsSummary,
    WeeklyStatsResponse,
)

# Effort-axis labels that count a day as "hard" for the dashboard summary.
_HARD_EFFORTS = {"tempo", "hard"}

ALLOWED_RANGES = {"7D", "30D", "3M", "6M", "1Y", "ALL"}


# ---------------------------------------------------------------------------
# 1. Activity-level facts
# ---------------------------------------------------------------------------

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
    )

    def __init__(self, activity: Activity):
        self.activity_id = activity.id
        # Bucket by the runner's local calendar day (#399), falling back to UTC.
        self.local_date: date = activity.local_start.date()
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
        object (#367). The trends queries only read the ~14 scalar fields below,
        so projecting them (mirroring readiness.py) avoids materializing the
        Activity/DerivedMetric JSON blobs (raw_summary, interval_structure,
        discount_signals, ...) the trend charts never touch. The LEFT join makes
        the three metric columns NULL for an activity without a DerivedMetric,
        reproducing the prior `activity.metrics ... else None`.
        """
        self = cls.__new__(cls)
        self.activity_id = row.id
        # Bucket by the runner's local calendar day (#399), falling back to UTC.
        self.local_date = (row.start_date_local or row.start_date).date()
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


# ---------------------------------------------------------------------------
# 2. Daily facts (summed when multiple activities in a day)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 3. Weekly bucket (used by distance/time per-week charts)
# ---------------------------------------------------------------------------

class WeekBucket:
    """Aggregation bucket for one ISO week."""

    __slots__ = (
        "week_start", "total_distance_m", "total_moving_time_s",
        "total_effort_score", "activity_count",
        "easy_seconds", "moderate_seconds", "hard_seconds",
        "in_period_days", "out_of_period_days",
        "out_of_period_distance_m", "out_of_period_moving_time_s",
        "out_of_period_effort_score",
    )

    def __init__(self, week_start: date):
        self.week_start = week_start  # Monday of the ISO week
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.total_effort_score = 0.0
        self.activity_count = 0
        self.easy_seconds = 0
        self.moderate_seconds = 0
        self.hard_seconds = 0
        # Edge-bucket coverage (#566): how many of the 7 days fall inside the
        # selected period. Defaults to a full week; the builder overrides for
        # buckets that straddle the window boundary.
        self.in_period_days = 7
        self.out_of_period_days = 0
        # Distance/time/load from the bucket's days OUTSIDE the selected window
        # (the older days an edge week spans). total_* stays the in-period sum;
        # the chart stacks this faded segment on top so the bar shows the whole
        # week.
        self.out_of_period_distance_m = 0
        self.out_of_period_moving_time_s = 0
        self.out_of_period_effort_score = 0.0

    def add(self, daily: DailyFact):
        self.total_distance_m += daily.total_distance_m
        self.total_moving_time_s += daily.total_moving_time_s
        self.total_effort_score += daily.total_effort_score
        self.activity_count += daily.activity_count


# ---------------------------------------------------------------------------
# 3b. Coarse-granularity bucket (#432 — 2-week / month bars)
# ---------------------------------------------------------------------------

# A fixed Monday reference for deterministic fortnight alignment (1970-01-05 is
# a Monday). Anchoring 2-week bins to it keeps boundaries stable regardless of
# the window start, so the same fortnights line up across ranges and the bars
# don't shift when the runner changes the selected range.
_EPOCH_MONDAY = date(1970, 1, 5)


def _period_start(d: date, period: str, week_starts_on: int = MONDAY) -> date:
    """First local day of the coarse bucket that ``d`` falls in.

    - ``biweekly``: 14-day bins aligned to the week-start grid (fortnight parity).
    - ``monthly``: the first of the calendar month.

    ``week_starts_on`` (0=Monday default, 6=Sunday) shifts the biweekly grid so
    it aligns with the weekly bars (#676); monthly is unaffected.
    """
    if period == "monthly":
        return d.replace(day=1)
    # biweekly: snap to the bin's starting week boundary by fortnight parity. The
    # parity epoch is the week-start grid's own epoch so weekly and biweekly agree.
    epoch = week_start(_EPOCH_MONDAY, week_starts_on)
    start = week_start(d, week_starts_on)
    weeks = (start - epoch).days // 7
    if weeks % 2 == 1:
        start -= timedelta(days=7)
    return start


def _next_period_start(start: date, period: str) -> date:
    """The start of the bucket immediately after ``start`` (for continuous fill)."""
    if period == "monthly":
        if start.month == 12:
            return date(start.year + 1, 1, 1)
        return date(start.year, start.month + 1, 1)
    return start + timedelta(days=14)


# ---------------------------------------------------------------------------
# 3c. Rolling-mode bins (#630)
# ---------------------------------------------------------------------------
#
# In rolling mode the bars must roll back from the current date, not snap to
# calendar boundaries (ISO-Monday weeks / calendar months / the epoch-anchored
# fortnight grid above). So a day's bucket is chosen by its offset back from the
# anchor (today): the newest bin ends today and each older bin is the preceding
# fixed-width block. Calendar mode is unchanged and keeps the calendar-anchored
# keys above. "month" has no fixed length, so a rolling month is a 30-day block.
_ROLLING_BIN_DAYS = {"biweekly": 14, "monthly": 30}


def _rolling_bin_start(d: date, anchor: date, bin_days: int) -> date:
    """First local day of the today-anchored rolling bin of width ``bin_days``
    that ``d`` falls in. The newest bin is ``[anchor - bin_days + 1, anchor]``;
    older bins step back by ``bin_days``. ``d`` is assumed on or before ``anchor``."""
    k = (anchor - d).days // bin_days
    return anchor - timedelta(days=(k + 1) * bin_days - 1)


def _coverage_days(
    span_start: date,
    span_end: date,
    period_start: Optional[date],
    period_end: date,
) -> tuple[int, int]:
    """In-period vs out-of-period day counts for a bucket spanning ``[span_start,
    span_end]`` inclusive against the selected window ``[period_start, period_end]`` (#566).

    A bucket (week / fortnight / month) rarely aligns to the period boundary, so
    an edge bucket only partially overlaps the window. Returns
    ``(in_period_days, out_of_period_days)``; when ``period_start`` is ``None``
    (the ALL range, no window) the bucket is treated as fully in-period.
    """
    total_days = (span_end - span_start).days + 1
    if period_start is None:
        return total_days, 0
    lo = max(span_start, period_start)
    hi = min(span_end, period_end)
    in_days = (hi - lo).days + 1 if hi >= lo else 0
    return in_days, total_days - in_days


def _add_out_of_period_values(buckets, pre_window_daily, key_fn) -> None:
    """Fold pre-window (out-of-period) days into the matching displayed bucket's
    ``out_of_period_*`` totals (#566), so the chart can stack a faded segment for
    the part of an edge bucket that falls before the window start. Pre-window
    days whose bucket is not displayed (older than the leading bucket) are
    ignored. ``key_fn`` maps a date to its bucket's start key.
    """
    if not pre_window_daily:
        return
    for df in pre_window_daily:
        bucket = buckets.get(key_fn(df.local_date))
        if bucket is not None:
            bucket.out_of_period_distance_m += df.total_distance_m
            bucket.out_of_period_moving_time_s += df.total_moving_time_s
            bucket.out_of_period_effort_score += df.total_effort_score


class PeriodBucket:
    """Aggregation bucket for one coarse granularity period (#432)."""

    __slots__ = (
        "period_start", "total_distance_m", "total_moving_time_s",
        "total_effort_score", "activity_count",
        "in_period_days", "out_of_period_days",
        "out_of_period_distance_m", "out_of_period_moving_time_s",
        "out_of_period_effort_score",
    )

    def __init__(self, period_start: date):
        self.period_start = period_start
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.total_effort_score = 0.0
        self.activity_count = 0
        # Edge-bucket coverage (#566): in/out-of-period day counts, set by the
        # builder from the bucket's full span against the selected window.
        self.in_period_days = 0
        self.out_of_period_days = 0
        # Distance/time/load from the bucket's days OUTSIDE the selected window;
        # see WeekBucket. total_* stays the in-period sum.
        self.out_of_period_distance_m = 0
        self.out_of_period_moving_time_s = 0
        self.out_of_period_effort_score = 0.0

    def add(self, daily: DailyFact):
        self.total_distance_m += daily.total_distance_m
        self.total_moving_time_s += daily.total_moving_time_s
        self.total_effort_score += daily.total_effort_score
        self.activity_count += daily.activity_count


# ---------------------------------------------------------------------------
# 4. Pipeline functions
# ---------------------------------------------------------------------------

_RANGE_DAYS = {
    "7D": 7,
    "30D": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "ALL": None,
}


def _resolve_since(range_key: str) -> Optional[date]:
    """Return the earliest local date to include, or None for ALL.

    The window is inclusive on both ends (``since`` .. ``today``), so a range of
    N days subtracts N-1 to span exactly N calendar days. e.g. 7D with today =
    Jun 9 covers Jun 3–Jun 9 inclusive, not Jun 2–Jun 9 (#179).
    """
    days = _RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None
    return date.today() - timedelta(days=days - 1)


def _period_window(range_key: str) -> Optional[tuple[date, date]]:
    """Return (current_start, previous_start) for a fixed range, or None for ALL.

    The current window is ``[current_start, today]`` (inclusive) and the previous
    window is ``[previous_start, current_start)``. Both span exactly
    ``_RANGE_DAYS[range]`` calendar days with no gap or overlap at the boundary,
    so period-over-period deltas line up with the charts (#179).
    """
    days = _RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None
    current_start = _resolve_since(range_key)
    assert current_start is not None  # days is not None here
    previous_start = current_start - timedelta(days=days)
    return current_start, previous_start


def _resolve_window(
    range_key: str, mode: str = "rolling", today: Optional[date] = None
) -> tuple[Optional[date], Optional[date], Optional[date]]:
    """(since, prev_start, prev_end) for the (range, mode) — the #400 global toggle.

    The current window is ``[since, today]`` inclusive; the previous comparison
    window is ``[prev_start, prev_end)``. Returns ``(None, None, None)`` for ALL
    (whole history, no previous).

    - ``rolling``: ``since`` is ``today - (N-1)`` and the previous window is the
      equal-length block immediately before it (unchanged behaviour).
    - ``calendar``: ``since`` is the start of the current calendar period (week/
      month/quarter/half/year) and the previous window is the ENTIRE previous
      calendar period (e.g. Jun 1-20 to-date vs ALL of May), so "vs last month"
      reads against last month's full total — it runs behind for most of the
      period and catches up by period-end (#413).
    """
    today = today or date.today()
    days = _RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None, None, None
    if mode == "calendar":
        from app.services.coach.volume import _calendar_period

        p_start, _, _, _ = _calendar_period(range_key.upper(), today)
        prev_p_start, _, _, _ = _calendar_period(
            range_key.upper(), p_start - timedelta(days=1)
        )
        # Previous = the whole prior calendar period: [prev_p_start, p_start).
        return p_start, prev_p_start, p_start
    since = today - timedelta(days=days - 1)
    return since, since - timedelta(days=days), since


def get_available_types(db: Session, *, user_id=None) -> List[str]:
    """
    Return the distinct activity types present in the database,
    sorted alphabetically.
    Pass ``user_id`` to restrict results to a single owner.
    """
    from sqlalchemy import distinct

    stmt = (
        select(distinct(Activity.type))
        .where(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.type)
    )
    if user_id is not None:
        stmt = stmt.where(Activity.user_id == user_id)
    return [row for row in db.execute(stmt).scalars().all()]


def _query_activity_facts(
    db: Session,
    start_date: Optional[date],
    end_date: Optional[date],
    types: Optional[List[str]] = None,
    *,
    user_id=None,
    include_session_shape: bool = False,
) -> List[ActivityFact]:
    """
    Internal helper to query activities by exact date range (start inclusive, end exclusive).

    ``user_id`` scopes the query to a single owner.  Pass it at every call site
    so that when multi-user lands (Phase 2) there is no accidental cross-user leak.
    Currently optional so callers that do not yet have a user_id available can
    continue working; the intent is to make it required once the API auth layer
    provides a resolved user at every endpoint (ADR 0005 / Phase 2).
    """
    # Project only the columns ActivityFact reads instead of materializing full
    # Activity ORM objects + a selectinload of DerivedMetric (#367). The LEFT
    # outer join keeps activities without a DerivedMetric (their metric columns
    # come back NULL); DerivedMetric.activity_id is unique, so the join never
    # duplicates a row. This avoids loading the Activity/DerivedMetric JSON
    # blobs the trend charts never touch — the worst over-fetch on the ALL range.
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
        .order_by(Activity.start_date.asc())
    )
    if user_id is not None:
        stmt = stmt.where(Activity.user_id == user_id)
    if start_date:
        stmt = stmt.where(Activity.start_date >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(Activity.start_date < datetime.combine(end_date, datetime.min.time()))

    rows = db.execute(stmt).all()
    facts = [ActivityFact.from_row(r) for r in rows]

    if types:
        type_set = {t.lower() for t in types}
        facts = [f for f in facts if f.activity_type.lower() in type_set]

    return facts


def build_activity_facts(
    db: Session,
    range_key: str = "30D",
    types: Optional[List[str]] = None,
    *,
    user_id=None,
    since: Optional[date] = None,
) -> List[ActivityFact]:
    """
    Query activities within the given range and project them into ActivityFact rows.
    Optionally filter by activity type (case-insensitive).
    Pass ``user_id`` to restrict results to a single owner. Pass ``since`` to override
    the window start (the #400 calendar mode); otherwise it derives from range_key.
    """
    resolved_since = since if since is not None else _resolve_since(range_key)
    return _query_activity_facts(db, resolved_since, None, types, user_id=user_id)


def build_daily_facts(activity_facts: List[ActivityFact]) -> List[DailyFact]:
    """
    Collapse activity facts into one row per local date.
    """
    buckets: dict[date, DailyFact] = {}
    for af in activity_facts:
        if af.local_date not in buckets:
            buckets[af.local_date] = DailyFact(af.local_date)
        buckets[af.local_date].add(af)

    return sorted(buckets.values(), key=lambda d: d.local_date)


def build_continuous_daily_facts(
    daily_facts: List[DailyFact],
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[DailyFact]:
    """
    Fill every day in the range so charts have continuous x-axes.

    Pass ``since`` to override the window start (the #400 calendar mode).
    Pass ``until`` to override the window end (#413): calendar mode passes the
    calendar period's last day so the chart frames the whole period (e.g. the
    full Mon–Sun week for 7D), with days after today rendered as empty bars.
    Defaults to today (rolling).
    """
    today = date.today()
    end = until if until is not None else today
    if since is None:
        since = _resolve_since(range_key)

    if since is not None:
        start = since
    elif daily_facts:
        start = daily_facts[0].local_date
    else:
        start = end

    existing = {df.local_date: df for df in daily_facts}
    result: List[DailyFact] = []
    cursor = start
    while cursor <= end:
        result.append(existing.get(cursor, DailyFact(cursor)))
        cursor += timedelta(days=1)

    return result


def build_weekly_buckets(
    daily_facts: List[DailyFact],
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
    pre_window_daily: Optional[List[DailyFact]] = None,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[WeekBucket]:
    """
    Roll daily facts into 7-day buckets.
    Fills every week in the range so charts have continuous x-axes.

    Pass ``since`` to override the window start (the #400 calendar mode).
    Pass ``until`` to override the window end (#413): calendar mode passes the
    calendar period's last day so the chart spans the whole period. Defaults to
    today (rolling).
    Pass ``pre_window_daily`` (days just before ``since``) so a leading edge
    week carries the value of its out-of-window days for the stacked partial
    bar (#566); ``total_*`` stays the in-period sum.
    Pass ``rolling_anchor`` (today) to bucket by 7-day blocks rolling back from
    that anchor instead of ISO-Monday weeks (#630): the bars then roll back from
    the current date. A leading block that only partly overlaps the window fades
    its out-of-window days via ``pre_window_daily``, the same as a calendar edge
    week — the bar shows the whole block, in-window solid and excluded part faded.
    """
    def _key(d: date) -> date:
        if rolling_anchor is not None:
            return _rolling_bin_start(d, rolling_anchor, 7)
        return week_start(d, week_starts_on)

    # Build buckets from actual data first
    buckets: dict[date, WeekBucket] = {}
    for df in daily_facts:
        k = _key(df.local_date)
        if k not in buckets:
            buckets[k] = WeekBucket(k)
        buckets[k].add(df)

    # Determine the full span of weeks to show
    end = until if until is not None else date.today()
    end_key = _key(end)  # last week to show

    if since is None:
        since = _resolve_since(range_key)
    if since is not None:
        start_key = _key(since)
    elif daily_facts:
        start_key = _key(daily_facts[0].local_date)
    else:
        start_key = end_key

    # Walk from start_key to end_key, inserting empty buckets (7-day step either way)
    cursor = start_key
    while cursor <= end_key:
        if cursor not in buckets:
            buckets[cursor] = WeekBucket(cursor)
        cursor += timedelta(weeks=1)

    # Edge-bucket coverage (#566): mark how much of each week falls inside the
    # selected window [since, end] so the chart can fade the partial leading week.
    # Rolling and calendar are handled the same here (#630): a leading rolling
    # block that only partly overlaps the window shows its out-of-window days as a
    # faded segment, exactly like a calendar edge week — the bar shows the whole
    # 7-day block, in-window solid and the excluded part faded.
    for w in buckets.values():
        w.in_period_days, w.out_of_period_days = _coverage_days(
            w.week_start, w.week_start + timedelta(days=6), since, end
        )
    # Carry the out-of-window value of a leading edge week's earlier days, keyed
    # the same way the buckets are (rolling block start or ISO Monday).
    _add_out_of_period_values(buckets, pre_window_daily, _key)

    return sorted(buckets.values(), key=lambda w: w.week_start)


def build_period_buckets(
    daily_facts: List[DailyFact],
    period: str,
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
    pre_window_daily: Optional[List[DailyFact]] = None,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[PeriodBucket]:
    """Roll daily facts into coarse buckets (#432) for ``biweekly`` or ``monthly``.

    Mirrors ``build_weekly_buckets``: aggregates from real data, then fills every
    empty bucket across the window so charts have continuous x-axes. ``since`` /
    ``until`` override the window start/end (the #400/#413 calendar framing);
    they default to the range start and today.
    Pass ``rolling_anchor`` (today) to bucket by fixed-width blocks rolling back
    from that anchor instead of the calendar grid (#630): 14-day blocks for
    ``biweekly``, 30-day blocks for ``monthly``. A leading block that only partly
    overlaps the window fades its out-of-window days via ``pre_window_daily``, the
    same as a calendar edge bucket.
    """
    bin_days = _ROLLING_BIN_DAYS[period]  # rolling-mode block width

    def _key(d: date) -> date:
        if rolling_anchor is not None:
            return _rolling_bin_start(d, rolling_anchor, bin_days)
        return _period_start(d, period, week_starts_on)

    def _advance(cur: date) -> date:
        if rolling_anchor is not None:
            return cur + timedelta(days=bin_days)
        return _next_period_start(cur, period)

    def _span_end(start: date) -> date:
        if rolling_anchor is not None:
            return start + timedelta(days=bin_days - 1)
        return _next_period_start(start, period) - timedelta(days=1)

    buckets: dict[date, PeriodBucket] = {}
    for df in daily_facts:
        ps = _key(df.local_date)
        if ps not in buckets:
            buckets[ps] = PeriodBucket(ps)
        buckets[ps].add(df)

    end = until if until is not None else date.today()
    end_start = _key(end)

    if since is None:
        since = _resolve_since(range_key)
    if since is not None:
        start = _key(since)
    elif daily_facts:
        start = _key(daily_facts[0].local_date)
    else:
        start = end_start

    cursor = start
    while cursor <= end_start:
        if cursor not in buckets:
            buckets[cursor] = PeriodBucket(cursor)
        cursor = _advance(cursor)

    # Edge-bucket coverage (#566): the bucket's full span is [period_start,
    # span_end] (the calendar month / fortnight in calendar mode, a fixed
    # 14-/30-day block in rolling mode); mark how much falls inside [since, end]
    # so a leading edge bucket fades its out-of-window part in either mode (#630).
    for b in buckets.values():
        span_end = _span_end(b.period_start)
        b.in_period_days, b.out_of_period_days = _coverage_days(
            b.period_start, span_end, since, end
        )
    # Carry the out-of-window value of a leading edge bucket's earlier days, keyed
    # the same way the buckets are (rolling block start or calendar period start).
    _add_out_of_period_values(buckets, pre_window_daily, _key)

    return sorted(buckets.values(), key=lambda b: b.period_start)


def build_suffer_score_trend(
    activity_facts: List[ActivityFact],
) -> List[dict]:
    """
    Return a list of {date, effort_score, type} for suffer-score charting.

    One entry per activity that has an effort_score.
    """
    points: List[dict] = []
    for af in activity_facts:
        if af.effort_score is None:
            continue
        points.append({
            "date": af.local_date.isoformat(),
            "effort_score": round(af.effort_score, 1),
            "type": af.activity_type,
        })
    return points


def build_continuous_suffer_scores(
    activity_facts: List[ActivityFact],
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[dict]:
    """
    Return one {date, effort_score} per day in the range.

    Days without activities get effort_score = 0.
    Days with multiple activities sum their effort scores.
    Pass ``since`` to override the window start (the #400 calendar mode).
    Pass ``until`` to override the window end (#413, calendar period frame).
    Defaults to today (rolling).
    """
    today = date.today()
    end = until if until is not None else today
    if since is None:
        since = _resolve_since(range_key)

    if since is not None:
        start = since
    elif activity_facts:
        start = activity_facts[0].local_date
    else:
        start = end

    # Sum effort scores per day
    daily: dict[date, float] = {}
    for af in activity_facts:
        if af.effort_score is None:
            continue
        daily[af.local_date] = daily.get(af.local_date, 0) + af.effort_score

    result: List[dict] = []
    cursor = start
    while cursor <= end:
        result.append({
            "date": cursor.isoformat(),
            "effort_score": round(daily.get(cursor, 0), 1),
        })
        cursor += timedelta(days=1)

    return result

# Efficiency (m/beat) is affected by conditions we can flag from fields already on
# the projection, so a hilly or stop-heavy activity is not silently read as less
# fit (#746). Thresholds mirror the analysis layer: HILLY matches the classifier's
# hilly gate; STOPPY marks a meaningful share of elapsed time stopped (the speed
# term already uses moving time, so stops act mainly on avg_hr).
_EFFICIENCY_HILLY_GAIN_PER_KM = 15.0  # mirrors analysis.classifier._HILLY_GAIN_PER_KM
_EFFICIENCY_STOPPY_FRACTION = 0.10


def build_efficiency_trend(facts: List[ActivityFact]) -> List[dict]:
    """
    Build data points for Efficiency = Speed (m/s) / HR (bpm).
    Only includes activities with distance > 1km and valid HR.

    Each point also carries a stable activity_id (#745, so same-day activities are
    individually addressable) and condition flags (hills, stops) derived from
    fields already on the projection so the chart can surface confounders (#746)
    rather than present an unadjusted number as pure fitness. Heat is a further
    known confounder but lives in the deferred raw_summary (not projected here for
    perf, #359/#367) and is left as a follow-up.
    """
    points = []
    for f in facts:
        # Filter for meaningful runs/walks
        if f.distance_m < 1000:
            continue
        if not f.avg_hr or f.avg_hr < 1:
            continue
        
        # Use DB speed, or calc from distance/time if missing
        speed = f.average_speed_mps
        if (speed is None or speed <= 0) and f.moving_time_s > 0:
            speed = f.distance_m / f.moving_time_s
            
        if not speed or speed <= 0:
            continue

        efficiency = speed / f.avg_hr
        
        # --- condition confounders (#746), from already-projected fields ---
        gain_per_km = (
            f.elev_gain_m / (f.distance_m / 1000.0) if f.distance_m > 0 else 0.0
        )
        stopped_frac = (
            (f.elapsed_time_s - f.moving_time_s) / f.elapsed_time_s
            if f.elapsed_time_s and f.elapsed_time_s > 0
            else 0.0
        )
        stopped_frac = max(0.0, min(1.0, stopped_frac))

        points.append({
            "date": f.local_date.isoformat(),
            "activity_id": str(f.activity_id),
            "efficiency_mps_per_bpm": round(efficiency, 4),
            "type": f.activity_type,
            "elev_gain_m": round(f.elev_gain_m or 0.0, 1),
            "gain_per_km": round(gain_per_km, 1),
            "hilly": gain_per_km >= _EFFICIENCY_HILLY_GAIN_PER_KM,
            "stopped_frac": round(stopped_frac, 3),
            "stoppy": stopped_frac >= _EFFICIENCY_STOPPY_FRACTION,
        })

    return sorted(points, key=lambda p: p["date"])


def _collapse_to_3_zones(time_in_zones: dict) -> tuple[int, int, int]:
    """Collapse 5-zone dict into 3-zone seconds: (easy, moderate, hard).

    Easy    = Z1 + Z2  (< 70% max HR)
    Moderate = Z3      (70-80% max HR)
    Hard    = Z4 + Z5  (> 80% max HR)
    """
    z1 = time_in_zones.get("Z1", 0) or 0
    z2 = time_in_zones.get("Z2", 0) or 0
    z3 = time_in_zones.get("Z3", 0) or 0
    z4 = time_in_zones.get("Z4", 0) or 0
    z5 = time_in_zones.get("Z5", 0) or 0
    return (z1 + z2, z3, z4 + z5)


def build_zone_load_weekly(
    activity_facts: List[ActivityFact],
    weekly_buckets: List["WeekBucket"],
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[dict]:
    """
    Aggregate per-activity time_in_zones into weekly 3-zone buckets.

    Returns one dict per week: {week_start, easy_min, moderate_min, hard_min}.
    Weeks with no zone data get zeros. ``rolling_anchor`` must match the value
    passed to ``build_weekly_buckets`` so the zone keys line up with the bars
    (#630): today-anchored 7-day bins when set, ISO-Monday weeks otherwise.
    """
    # Sum zone seconds per bucket key (must match the weekly_buckets keys)
    zone_by_week: dict[date, tuple[int, int, int]] = {}
    for af in activity_facts:
        if not af.time_in_zones:
            continue
        if rolling_anchor is not None:
            key = _rolling_bin_start(af.local_date, rolling_anchor, 7)
        else:
            key = week_start(af.local_date, week_starts_on)
        easy_s, mod_s, hard_s = _collapse_to_3_zones(af.time_in_zones)
        prev = zone_by_week.get(key, (0, 0, 0))
        zone_by_week[key] = (
            prev[0] + easy_s,
            prev[1] + mod_s,
            prev[2] + hard_s,
        )

    # Emit one point per weekly bucket (continuous)
    result: List[dict] = []
    for wb in weekly_buckets:
        easy_s, mod_s, hard_s = zone_by_week.get(wb.week_start, (0, 0, 0))
        result.append({
            "week_start": wb.week_start.isoformat(),
            "easy_min": round(easy_s / 60, 1),
            "moderate_min": round(mod_s / 60, 1),
            "hard_min": round(hard_s / 60, 1),
        })
    return result


def build_zone_load_daily(
    activity_facts: List[ActivityFact],
    continuous_daily: List["DailyFact"],
) -> List[dict]:
    """
    Per-day 3-zone minutes, continuous (every day in the range gets a row).
    """
    # Sum zone seconds per local date
    zone_by_date: dict[date, tuple[int, int, int]] = {}
    for af in activity_facts:
        if not af.time_in_zones:
            continue
        easy_s, mod_s, hard_s = _collapse_to_3_zones(af.time_in_zones)
        prev = zone_by_date.get(af.local_date, (0, 0, 0))
        zone_by_date[af.local_date] = (
            prev[0] + easy_s,
            prev[1] + mod_s,
            prev[2] + hard_s,
        )

    result: List[dict] = []
    for df in continuous_daily:
        easy_s, mod_s, hard_s = zone_by_date.get(df.local_date, (0, 0, 0))
        result.append({
            "date": df.local_date.isoformat(),
            "easy_min": round(easy_s / 60, 1),
            "moderate_min": round(mod_s / 60, 1),
            "hard_min": round(hard_s / 60, 1),
        })
    return result


def build_zone_load_period(
    activity_facts: List[ActivityFact],
    period_buckets: List["PeriodBucket"],
    period: str,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[dict]:
    """Per-coarse-bucket 3-zone minutes (#432), continuous over ``period_buckets``.

    ``rolling_anchor`` must match ``build_period_buckets`` so the zone keys line
    up with the bars (#630): today-anchored 14-/30-day bins when set, the
    calendar grid otherwise.
    """
    zone_by_period: dict[date, tuple[int, int, int]] = {}
    for af in activity_facts:
        if not af.time_in_zones:
            continue
        if rolling_anchor is not None:
            ps = _rolling_bin_start(af.local_date, rolling_anchor, _ROLLING_BIN_DAYS[period])
        else:
            ps = _period_start(af.local_date, period, week_starts_on)
        easy_s, mod_s, hard_s = _collapse_to_3_zones(af.time_in_zones)
        prev = zone_by_period.get(ps, (0, 0, 0))
        zone_by_period[ps] = (
            prev[0] + easy_s,
            prev[1] + mod_s,
            prev[2] + hard_s,
        )

    result: List[dict] = []
    for pb in period_buckets:
        easy_s, mod_s, hard_s = zone_by_period.get(pb.period_start, (0, 0, 0))
        result.append({
            "period_start": pb.period_start.isoformat(),
            "easy_min": round(easy_s / 60, 1),
            "moderate_min": round(mod_s / 60, 1),
            "hard_min": round(hard_s / 60, 1),
        })
    return result


def _avg_efficiency(facts: List[ActivityFact]) -> Optional[float]:
    """Mean HR-efficiency (m/s per bpm) over the window, or None when no
    activity qualifies. Reuses build_efficiency_trend so the average is taken
    over exactly the activities the efficiency chart plots (#385)."""
    points = build_efficiency_trend(facts)
    if not points:
        return None
    return round(sum(p["efficiency_mps_per_bpm"] for p in points) / len(points), 4)


def _zone_minutes(facts: List[ActivityFact]) -> tuple[float, float, float]:
    """Minutes in each HR band (easy, moderate, hard) over the window (#385)."""
    easy_s = mod_s = hard_s = 0
    for af in facts:
        if not af.time_in_zones:
            continue
        e, m, h = _collapse_to_3_zones(af.time_in_zones)
        easy_s += e
        mod_s += m
        hard_s += h
    return round(easy_s / 60, 1), round(mod_s / 60, 1), round(hard_s / 60, 1)


def _summarise_window(facts: List[ActivityFact]) -> WeeklyStatsSummary:
    """Collapse activity facts for one window into the dashboard summary card totals."""
    hard_days = len({f.local_date for f in facts if f.effort in _HARD_EFFORTS})
    return WeeklyStatsSummary(
        total_distance_m=sum(f.distance_m for f in facts),
        total_moving_time_s=sum(f.moving_time_s for f in facts),
        activity_count=len(facts),
        total_load=round(sum(f.effort_score or 0.0 for f in facts), 1),
        hard_days=hard_days,
    )


def get_weekly_stats(db: Session, *, user_id=None) -> WeeklyStatsResponse:
    """
    Rolling 7-day summary for the dashboard, plus the prior 7 days for comparison.

    Uses exactly the same 7-day window as the Trends 7D view (via
    ``_period_window``) so the dashboard cards and Trends 7D can never drift
    (#246, #179). "Hard days" is derived from the effort axis rather than an
    HR/name heuristic.
    Pass ``user_id`` to restrict results to a single owner.
    """
    current_start, prev_start = _period_window("7D")  # type: ignore[misc]

    current = _query_activity_facts(db, current_start, None, user_id=user_id)
    previous = _query_activity_facts(db, prev_start, current_start, user_id=user_id)

    return WeeklyStatsResponse(
        summary=_summarise_window(current),
        previous_summary=_summarise_window(previous),
    )


def get_trends_report(
    db: Session,
    range_key: str = "30D",
    types: Optional[List[str]] = None,
    *,
    user_id=None,
    mode: str = "rolling",
) -> TrendsResponse:
    """
    Main entry point for generating the complete trends report.
    Orchestrates data fetching and aggregation.
    Pass ``user_id`` to restrict results to a single owner. ``mode`` is the #400
    global window framing: ``rolling`` (trailing N days, the default) or
    ``calendar`` (the current calendar period — week/month/quarter/half/year — for
    the range), which shifts the window start and the previous-period comparison
    for the whole report (summary, deltas, and every chart).
    """
    range_upper = range_key.upper()
    if range_upper not in ALLOWED_RANGES:
        range_upper = "30D"
    if mode not in ("rolling", "calendar"):
        mode = "rolling"

    # The (range, mode) window: `since` starts the current window; the previous
    # comparison spans [prev_start, prev_end). Calendar mode shifts both.
    since, prev_start, prev_end = _resolve_window(range_upper, mode)

    # The runner's chosen week start (0=Monday default, 6=Sunday), which the
    # calendar-mode weekly/biweekly bars align to (#676). Rolling mode buckets by
    # today-anchored blocks, so it is week-start-independent there.
    week_starts_on = resolve_week_start(
        db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if user_id is not None
        else None
    )

    # Rolling mode buckets the bars in fixed-width blocks rolling back from today
    # rather than snapping to the calendar grid (#630). None keeps the calendar
    # keying (ISO-Monday weeks / calendar months / the fortnight grid).
    rolling_anchor: Optional[date] = date.today() if mode == "rolling" else None

    # Chart x-axis frame end (#413): rolling stops at today (until=None); calendar
    # spans the whole current period (e.g. Mon–Sun for 7D), so the period's last
    # day frames the chart and days after today render as empty bars.
    until: Optional[date] = None
    if mode == "calendar" and range_upper in _RANGE_DAYS and _RANGE_DAYS[range_upper]:
        from app.services.coach.volume import _calendar_period

        _, until, _, _ = _calendar_period(range_upper, date.today(), week_starts_on)

    # 1. Activity-level facts (filtered by types if provided)
    activity_facts = build_activity_facts(
        db, range_upper, types=types, user_id=user_id, since=since
    )

    # 2. Daily facts (sum per local date)
    daily_facts = build_daily_facts(activity_facts)

    # #566/#630: the days just before the window, back to the earliest leading
    # edge-bucket start across the week / 2-week / month granularities, so a
    # leading edge bucket can show the value of its out-of-window days as a faded
    # stacked segment (the bar then shows the whole week/period, not just the
    # in-window slice). Calendar buckets snap to the 1st of `since`'s month;
    # rolling blocks are anchored to today, and the three widths don't nest, so
    # take the earliest of the three leading-block starts. Kept separate from
    # daily_facts so the summary/header totals stay strictly in-period.
    pre_window_daily: List[DailyFact] = []
    if since is not None:
        if rolling_anchor is not None:
            pre_start = min(
                _rolling_bin_start(since, rolling_anchor, d) for d in (7, 14, 30)
            )
        else:
            pre_start = _period_start(since, "monthly")
        if pre_start < since:
            pre_facts = _query_activity_facts(
                db, pre_start, since, types=types, user_id=user_id
            )
            pre_window_daily = build_daily_facts(pre_facts)

    # Summary totals across the entire range
    cur_easy, cur_mod, cur_hard = _zone_minutes(activity_facts)
    summary = TrendsSummary(
        total_distance_m=sum(d.total_distance_m for d in daily_facts),
        total_moving_time_s=sum(d.total_moving_time_s for d in daily_facts),
        activity_count=sum(d.activity_count for d in daily_facts),
        total_suffer_score=sum(d.total_effort_score for d in daily_facts),
        avg_efficiency_mps_per_bpm=_avg_efficiency(activity_facts),
        zone_easy_minutes=cur_easy,
        zone_moderate_minutes=cur_mod,
        zone_hard_minutes=cur_hard,
    )

    # Previous period summary (vs the equivalent prior window for this mode)
    previous_summary = None
    if prev_start is not None and prev_end is not None:
        prev_facts = _query_activity_facts(db, prev_start, prev_end, types=types, user_id=user_id)
        prev_easy, prev_mod, prev_hard = _zone_minutes(prev_facts)
        previous_summary = TrendsSummary(
            total_distance_m=sum(f.distance_m for f in prev_facts),
            total_moving_time_s=sum(f.moving_time_s for f in prev_facts),
            activity_count=len(prev_facts),
            total_suffer_score=sum(f.effort_score or 0.0 for f in prev_facts),
            avg_efficiency_mps_per_bpm=_avg_efficiency(prev_facts),
            zone_easy_minutes=prev_easy,
            zone_moderate_minutes=prev_mod,
            zone_hard_minutes=prev_hard,
        )

    # 3. Continuous daily facts (every day filled)
    continuous_daily = build_continuous_daily_facts(
        daily_facts, range_key=range_upper, since=since, until=until
    )

    daily_distance = [
        DailyDistancePoint(
            date=d.local_date,
            total_distance_m=d.total_distance_m,
            activity_count=d.activity_count,
        )
        for d in continuous_daily
    ]

    daily_time = [
        DailyTimePoint(
            date=d.local_date,
            total_moving_time_s=d.total_moving_time_s,
            activity_count=d.activity_count,
        )
        for d in continuous_daily
    ]

    # 4. Weekly buckets (continuous — includes empty weeks)
    weekly = build_weekly_buckets(
        daily_facts, range_key=range_upper, since=since, until=until,
        pre_window_daily=pre_window_daily, rolling_anchor=rolling_anchor,
        week_starts_on=week_starts_on,
    )

    weekly_distance = [
        WeeklyDistancePoint(
            week_start=w.week_start,
            total_distance_m=w.total_distance_m,
            activity_count=w.activity_count,
            in_period_days=w.in_period_days,
            out_of_period_days=w.out_of_period_days,
            out_of_period_distance_m=w.out_of_period_distance_m,
        )
        for w in weekly
    ]

    weekly_time = [
        WeeklyTimePoint(
            week_start=w.week_start,
            total_moving_time_s=w.total_moving_time_s,
            activity_count=w.activity_count,
            in_period_days=w.in_period_days,
            out_of_period_days=w.out_of_period_days,
            out_of_period_moving_time_s=w.out_of_period_moving_time_s,
        )
        for w in weekly
    ]

    weekly_suffer_score = [
        WeeklySufferScorePoint(
            week_start=w.week_start,
            effort_score=round(w.total_effort_score, 1),
            in_period_days=w.in_period_days,
            out_of_period_days=w.out_of_period_days,
            out_of_period_effort_score=round(w.out_of_period_effort_score, 1),
        )
        for w in weekly
    ]

    # 4b. Coarse buckets (#432): 2-week and month rollups of the same daily facts.
    biweekly = build_period_buckets(
        daily_facts, "biweekly", range_key=range_upper, since=since, until=until,
        pre_window_daily=pre_window_daily, rolling_anchor=rolling_anchor,
        week_starts_on=week_starts_on,
    )
    monthly = build_period_buckets(
        daily_facts, "monthly", range_key=range_upper, since=since, until=until,
        pre_window_daily=pre_window_daily, rolling_anchor=rolling_anchor,
        week_starts_on=week_starts_on,
    )

    def _period_distance(buckets: List[PeriodBucket]) -> List[PeriodDistancePoint]:
        return [
            PeriodDistancePoint(
                period_start=b.period_start,
                total_distance_m=b.total_distance_m,
                activity_count=b.activity_count,
                in_period_days=b.in_period_days,
                out_of_period_days=b.out_of_period_days,
                out_of_period_distance_m=b.out_of_period_distance_m,
            )
            for b in buckets
        ]

    def _period_time(buckets: List[PeriodBucket]) -> List[PeriodTimePoint]:
        return [
            PeriodTimePoint(
                period_start=b.period_start,
                total_moving_time_s=b.total_moving_time_s,
                activity_count=b.activity_count,
                in_period_days=b.in_period_days,
                out_of_period_days=b.out_of_period_days,
                out_of_period_moving_time_s=b.out_of_period_moving_time_s,
            )
            for b in buckets
        ]

    def _period_suffer(buckets: List[PeriodBucket]) -> List[PeriodSufferScorePoint]:
        return [
            PeriodSufferScorePoint(
                period_start=b.period_start,
                effort_score=round(b.total_effort_score, 1),
                in_period_days=b.in_period_days,
                out_of_period_days=b.out_of_period_days,
                out_of_period_effort_score=round(b.out_of_period_effort_score, 1),
            )
            for b in buckets
        ]

    biweekly_distance = _period_distance(biweekly)
    monthly_distance = _period_distance(monthly)
    biweekly_time = _period_time(biweekly)
    monthly_time = _period_time(monthly)
    biweekly_suffer_score = _period_suffer(biweekly)
    monthly_suffer_score = _period_suffer(monthly)

    # 6. Suffer score (per-activity)
    suffer_score = [
        SufferScorePoint(**p) for p in build_suffer_score_trend(activity_facts)
    ]

    # 7. Daily suffer score (continuous — every day filled)
    daily_suffer_score = [
        DailySufferScorePoint(**p)
        for p in build_continuous_suffer_scores(
            activity_facts, range_key=range_upper, since=since, until=until
        )
    ]

    # 8. Efficiency trend
    efficiency_trend = [
        EfficiencyPoint(**p)
        for p in build_efficiency_trend(activity_facts)
    ]

    # 9. Zone load (3-zone stacked bar)
    weekly_zone_load = [
        ZoneLoadWeekPoint(**p)
        for p in build_zone_load_weekly(
            activity_facts, weekly, rolling_anchor=rolling_anchor, week_starts_on=week_starts_on
        )
    ]
    daily_zone_load = [
        DailyZoneLoadPoint(**p)
        for p in build_zone_load_daily(activity_facts, continuous_daily)
    ]
    biweekly_zone_load = [
        PeriodZoneLoadPoint(**p)
        for p in build_zone_load_period(
            activity_facts, biweekly, "biweekly", rolling_anchor=rolling_anchor,
            week_starts_on=week_starts_on,
        )
    ]
    monthly_zone_load = [
        PeriodZoneLoadPoint(**p)
        for p in build_zone_load_period(
            activity_facts, monthly, "monthly", rolling_anchor=rolling_anchor,
            week_starts_on=week_starts_on,
        )
    ]

    return TrendsResponse(
        range=range_upper,
        summary=summary,
        previous_summary=previous_summary,
        weekly_distance=weekly_distance,
        weekly_time=weekly_time,
        weekly_suffer_score=weekly_suffer_score,
        daily_distance=daily_distance,
        daily_time=daily_time,
        suffer_score=suffer_score,
        daily_suffer_score=daily_suffer_score,
        efficiency_trend=efficiency_trend,
        weekly_zone_load=weekly_zone_load,
        daily_zone_load=daily_zone_load,
        biweekly_distance=biweekly_distance,
        monthly_distance=monthly_distance,
        biweekly_time=biweekly_time,
        monthly_time=monthly_time,
        biweekly_suffer_score=biweekly_suffer_score,
        monthly_suffer_score=monthly_suffer_score,
        biweekly_zone_load=biweekly_zone_load,
        monthly_zone_load=monthly_zone_load,
    )


def get_volume_report(
    db: Session,
    user_id,
    range_key: str = "7D",
    as_of: Optional[date] = None,
    types: Optional[List[str]] = None,
) -> "VolumeReport":
    """The #400 frequency-/volume-vs-norm report for the Trends page, for the selected
    range (7D/30D/3M/6M/1Y) as of `as_of` (defaults to today). Reuses the same pure
    core as the coach pack, so the two never drift. Fetches a span covering the larger
    of the rolling window and the calendar period, plus the norm baseline, and
    partitions by local day (#399).

    Pass ``types`` to scope BOTH the window and the norm baseline to the selected
    activity types (#413), so the typical line / "vs typical" caption compares
    like-for-like with the type-filtered Trends charts instead of measuring a
    filtered window against an all-activity norm."""
    from app.services.coach.volume import (
        _RANGE_WINDOW_DAYS,
        _BASELINE_DAYS_BY_RANGE,
        _calendar_period,
        build_volume_report,
    )

    resolved = as_of or date.today()
    week_starts_on = resolve_week_start(
        db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if user_id is not None
        else None
    )
    key = range_key if range_key in _RANGE_WINDOW_DAYS else "7D"
    n = _RANGE_WINDOW_DAYS[key]
    roll_start = resolved - timedelta(days=n - 1)
    period_start, _, _, _ = _calendar_period(key, resolved, week_starts_on)
    # Fetch back to the earliest window start plus the term-scaled norm baseline.
    earliest = min(roll_start, period_start) - timedelta(days=_BASELINE_DAYS_BY_RANGE[key] + 1)
    facts = _query_activity_facts(
        db, earliest, resolved + timedelta(days=1), types, user_id=user_id
    )
    return build_volume_report(facts, resolved, key, week_starts_on)
