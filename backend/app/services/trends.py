"""
Trends pipeline — turns raw Activity rows into daily/weekly aggregated facts.

All grouping uses the activity's local start_date (timezone-aware).
If multiple activities occur on the same local date, they are summed.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import Activity, DerivedMetric
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
) -> List[ActivityFact]:
    """
    Query activities within the given range and project them into ActivityFact rows.
    Optionally filter by activity type (case-insensitive).
    Pass ``user_id`` to restrict results to a single owner.
    """
    since = _resolve_since(range_key)
    return _query_activity_facts(db, since, None, types, user_id=user_id)


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
) -> List[DailyFact]:
    """
    Fill every day in the range so charts have continuous x-axes.
    """
    today = date.today()
    since = _resolve_since(range_key)

    if since is not None:
        start = since
    elif daily_facts:
        start = daily_facts[0].local_date
    else:
        start = today

    existing = {df.local_date: df for df in daily_facts}
    result: List[DailyFact] = []
    cursor = start
    while cursor <= today:
        result.append(existing.get(cursor, DailyFact(cursor)))
        cursor += timedelta(days=1)

    return result


def build_weekly_buckets(
    daily_facts: List[DailyFact],
    range_key: str = "30D",
) -> List[WeekBucket]:
    """
    Roll daily facts into ISO-week buckets (Monday start).
    Fills every week in the range so charts have continuous x-axes.
    """
    # Build buckets from actual data first
    buckets: dict[date, WeekBucket] = {}
    for df in daily_facts:
        monday = df.local_date - timedelta(days=df.local_date.weekday())
        if monday not in buckets:
            buckets[monday] = WeekBucket(monday)
        buckets[monday].add(df)

    # Determine the full span of weeks to show
    today = date.today()
    end_monday = today - timedelta(days=today.weekday())  # current week

    since = _resolve_since(range_key)
    if since is not None:
        start_monday = since - timedelta(days=since.weekday())
    elif daily_facts:
        earliest = daily_facts[0].local_date
        start_monday = earliest - timedelta(days=earliest.weekday())
    else:
        start_monday = end_monday

    # Walk from start_monday to end_monday, inserting empty buckets
    cursor = start_monday
    while cursor <= end_monday:
        if cursor not in buckets:
            buckets[cursor] = WeekBucket(cursor)
        cursor += timedelta(weeks=1)

    return sorted(buckets.values(), key=lambda w: w.week_start)




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
) -> List[dict]:
    """
    Return one {date, effort_score} per day in the range.

    Days without activities get effort_score = 0.
    Days with multiple activities sum their effort scores.
    """
    today = date.today()
    since = _resolve_since(range_key)

    if since is not None:
        start = since
    elif activity_facts:
        start = activity_facts[0].local_date
    else:
        start = today

    # Sum effort scores per day
    daily: dict[date, float] = {}
    for af in activity_facts:
        if af.effort_score is None:
            continue
        daily[af.local_date] = daily.get(af.local_date, 0) + af.effort_score

    result: List[dict] = []
    cursor = start
    while cursor <= today:
        result.append({
            "date": cursor.isoformat(),
            "effort_score": round(daily.get(cursor, 0), 1),
        })
        cursor += timedelta(days=1)

    return result

def build_efficiency_trend(facts: List[ActivityFact]) -> List[dict]:
    """
    Build data points for Efficiency = Speed (m/s) / HR (bpm).
    Only includes activities with distance > 1km and valid HR.
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
        
        points.append({
            "date": f.local_date.isoformat(),
            "efficiency_mps_per_bpm": round(efficiency, 4),
            "type": f.activity_type,
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
) -> List[dict]:
    """
    Aggregate per-activity time_in_zones into weekly 3-zone buckets.

    Returns one dict per week: {week_start, easy_min, moderate_min, hard_min}.
    Weeks with no zone data get zeros.
    """
    # Sum zone seconds per ISO-week Monday
    zone_by_week: dict[date, tuple[int, int, int]] = {}
    for af in activity_facts:
        if not af.time_in_zones:
            continue
        monday = af.local_date - timedelta(days=af.local_date.weekday())
        easy_s, mod_s, hard_s = _collapse_to_3_zones(af.time_in_zones)
        prev = zone_by_week.get(monday, (0, 0, 0))
        zone_by_week[monday] = (
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
) -> TrendsResponse:
    """
    Main entry point for generating the complete trends report.
    Orchestrates data fetching and aggregation.
    Pass ``user_id`` to restrict results to a single owner.
    """
    range_upper = range_key.upper()
    if range_upper not in ALLOWED_RANGES:
        range_upper = "30D"

    # 1. Activity-level facts (filtered by types if provided)
    activity_facts = build_activity_facts(db, range_upper, types=types, user_id=user_id)

    # 2. Daily facts (sum per local date)
    daily_facts = build_daily_facts(activity_facts)

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

    # Previous period summary
    previous_summary = None
    window = _period_window(range_upper)
    if window is not None:
        current_start, prev_start = window
        prev_facts = _query_activity_facts(db, prev_start, current_start, types=types, user_id=user_id)
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
    continuous_daily = build_continuous_daily_facts(daily_facts, range_key=range_upper)

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
    weekly = build_weekly_buckets(daily_facts, range_key=range_upper)

    weekly_distance = [
        WeeklyDistancePoint(
            week_start=w.week_start,
            total_distance_m=w.total_distance_m,
            activity_count=w.activity_count,
        )
        for w in weekly
    ]

    weekly_time = [
        WeeklyTimePoint(
            week_start=w.week_start,
            total_moving_time_s=w.total_moving_time_s,
            activity_count=w.activity_count,
        )
        for w in weekly
    ]

    weekly_suffer_score = [
        WeeklySufferScorePoint(
            week_start=w.week_start,
            effort_score=round(w.total_effort_score, 1),
        )
        for w in weekly
    ]

    # 6. Suffer score (per-activity)
    suffer_score = [
        SufferScorePoint(**p) for p in build_suffer_score_trend(activity_facts)
    ]

    # 7. Daily suffer score (continuous — every day filled)
    daily_suffer_score = [
        DailySufferScorePoint(**p)
        for p in build_continuous_suffer_scores(activity_facts, range_key=range_upper)
    ]

    # 8. Efficiency trend
    efficiency_trend = [
        EfficiencyPoint(**p)
        for p in build_efficiency_trend(activity_facts)
    ]

    # 9. Zone load (3-zone stacked bar)
    weekly_zone_load = [
        ZoneLoadWeekPoint(**p)
        for p in build_zone_load_weekly(activity_facts, weekly)
    ]
    daily_zone_load = [
        DailyZoneLoadPoint(**p)
        for p in build_zone_load_daily(activity_facts, continuous_daily)
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
    )
