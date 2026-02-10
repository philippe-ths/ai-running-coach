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


# ---------------------------------------------------------------------------
# 1. Activity-level facts
# ---------------------------------------------------------------------------

class ActivityFact:
    """One row per activity — the minimal projection needed for trend charts."""

    __slots__ = (
        "activity_id", "local_date", "activity_type", "user_intent",
        "distance_m", "moving_time_s", "elapsed_time_s",
        "elev_gain_m", "avg_hr", "avg_cadence", "average_speed_mps",
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
        "total_elapsed_time_s", "total_elev_gain_m", "activity_count",
    )

    def __init__(self, local_date: date):
        self.local_date = local_date
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.total_elapsed_time_s = 0
        self.total_elev_gain_m = 0.0
        self.activity_count = 0

    def add(self, fact: ActivityFact):
        self.total_distance_m += fact.distance_m
        self.total_moving_time_s += fact.moving_time_s
        self.total_elapsed_time_s += fact.elapsed_time_s
        self.total_elev_gain_m += fact.elev_gain_m
        self.activity_count += 1


# ---------------------------------------------------------------------------
# 3. Weekly bucket (used by distance/time per-week charts)
# ---------------------------------------------------------------------------

class WeekBucket:
    """Aggregation bucket for one ISO week."""

    __slots__ = (
        "week_start", "total_distance_m", "total_moving_time_s",
        "activity_count",
    )

    def __init__(self, week_start: date):
        self.week_start = week_start  # Monday of the ISO week
        self.total_distance_m = 0
        self.total_moving_time_s = 0
        self.activity_count = 0

    def add(self, daily: DailyFact):
        self.total_distance_m += daily.total_distance_m
        self.total_moving_time_s += daily.total_moving_time_s
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

    stmt = (
        select(Activity)
        .where(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.start_date.asc())
    )
    if since is not None:
        stmt = stmt.where(Activity.start_date >= datetime.combine(since, datetime.min.time()))

    activities = db.execute(stmt).scalars().all()
    facts = [ActivityFact(a) for a in activities]

    # Post-filter by effective_type so user_intent overrides are respected
    if types:
        type_set = {t.lower() for t in types}
        facts = [f for f in facts if f.effective_type.lower() in type_set]

    return facts


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


def build_pace_trend(
    activity_facts: List[ActivityFact],
    types: Optional[List[str]] = None,
) -> List[dict]:
    """
    Return a list of {date, pace_sec_per_km, type} for pace-trend charting.

    Filters to run/walk by default (case-insensitive effective_type match).
    If an activity has zero distance, it is skipped.

    When multiple activities of the same type fall on the same day,
    their paces are distance-weighted averaged into a single point.
    """
    if types is None:
        types = ["run", "walk"]

    type_set = {t.lower() for t in types}

    # Accumulate (total_distance, total_time) per (date, type) for weighted avg
    buckets: dict[tuple[str, str], tuple[int, int]] = {}

    for af in activity_facts:
        etype = af.effective_type
        if etype.lower() not in type_set:
            continue
        if af.distance_m <= 0:
            continue

        key = (af.local_date.isoformat(), etype)
        dist, time = buckets.get(key, (0, 0))
        buckets[key] = (dist + af.distance_m, time + af.moving_time_s)

    points: List[dict] = []
    for (iso_date, etype), (total_dist, total_time) in sorted(buckets.items()):
        pace = (total_time / total_dist) * 1000
        points.append({
            "date": iso_date,
            "pace_sec_per_km": round(pace, 1),
            "type": etype,
        })

    return points
