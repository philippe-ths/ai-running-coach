"""
Trends pipeline — turns raw Activity rows into daily/weekly aggregated facts.

All grouping uses the activity's local start_date (timezone-aware).
If multiple activities occur on the same local date, they are summed.
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import Activity
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
)

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
        "effort_score", "time_in_zones",
    )

    def __init__(self, activity: Activity):
        self.activity_id = activity.id
        # Use the timezone-aware start_date, convert to local date
        self.local_date: date = activity.start_date.date()
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
        self.time_in_zones: Optional[dict] = (
            activity.metrics.time_in_zones if activity.metrics else None
        )

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
    """Return the earliest local date to include, or None for ALL."""
    days = _RANGE_DAYS.get(range_key.upper())
    if days is None:
        return None
    return date.today() - timedelta(days=days)


def get_available_types(db: Session) -> List[str]:
    """
    Return the distinct activity types present in the database,
    sorted alphabetically.
    """
    from sqlalchemy import distinct

    stmt = (
        select(distinct(Activity.type))
        .where(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.type)
    )
    return [row for row in db.execute(stmt).scalars().all()]


def _query_activity_facts(
    db: Session,
    start_date: Optional[date],
    end_date: Optional[date],
    types: Optional[List[str]] = None,
) -> List[ActivityFact]:
    """
    Internal helper to query activities by exact date range (start inclusive, end exclusive).
    """
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Activity)
        .options(selectinload(Activity.metrics))
        .where(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.start_date.asc())
    )
    if start_date:
        stmt = stmt.where(Activity.start_date >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        stmt = stmt.where(Activity.start_date < datetime.combine(end_date, datetime.min.time()))

    activities = db.execute(stmt).scalars().all()
    facts = [ActivityFact(a) for a in activities]

    if types:
        type_set = {t.lower() for t in types}
        facts = [f for f in facts if f.activity_type.lower() in type_set]

    return facts


def build_activity_facts(
    db: Session,
    range_key: str = "30D",
    types: Optional[List[str]] = None,
) -> List[ActivityFact]:
    """
    Query activities within the given range and project them into ActivityFact rows.
    Optionally filter by activity type (case-insensitive).
    """
    since = _resolve_since(range_key)
    return _query_activity_facts(db, since, None, types)


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


def get_trends_report(
    db: Session,
    range_key: str = "30D",
    types: Optional[List[str]] = None,
) -> TrendsResponse:
    """
    Main entry point for generating the complete trends report.
    Orchestrates data fetching and aggregation.
    """
    range_upper = range_key.upper()
    if range_upper not in ALLOWED_RANGES:
        range_upper = "30D"

    # 1. Activity-level facts (filtered by types if provided)
    activity_facts = build_activity_facts(db, range_upper, types=types)

    # 2. Daily facts (sum per local date)
    daily_facts = build_daily_facts(activity_facts)

    # Summary totals across the entire range
    summary = TrendsSummary(
        total_distance_m=sum(d.total_distance_m for d in daily_facts),
        total_moving_time_s=sum(d.total_moving_time_s for d in daily_facts),
        activity_count=sum(d.activity_count for d in daily_facts),
        total_suffer_score=sum(d.total_effort_score for d in daily_facts),
    )

    # Previous period summary
    previous_summary = None
    days = _RANGE_DAYS.get(range_upper)
    if days is not None:
        today = date.today()
        current_start = today - timedelta(days=days)
        prev_start = current_start - timedelta(days=days)

        prev_facts = _query_activity_facts(db, prev_start, current_start, types=types)
        previous_summary = TrendsSummary(
            total_distance_m=sum(f.distance_m for f in prev_facts),
            total_moving_time_s=sum(f.moving_time_s for f in prev_facts),
            activity_count=len(prev_facts),
            total_suffer_score=sum(f.effort_score or 0.0 for f in prev_facts),
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
