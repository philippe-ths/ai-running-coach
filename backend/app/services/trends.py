"""
Trends pipeline — turns raw Activity rows into daily/weekly aggregated facts.

All grouping uses the activity's local start_date (timezone-aware).
If multiple activities occur on the same local date, they are summed.
"""

from datetime import date, timedelta
from typing import List, NamedTuple, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, UserProfile
from app.services.activity_facts import (
    ALLOWED_RANGES,
    BASELINE_DAYS_BY_RANGE,
    RANGE_DAYS,
    RANGE_WINDOW_DAYS,
    ActivityFact,
    Bucket,
    DailyFact,
    build_daily_facts,
    bucket_daily_facts,
    bucket_key_fn,
    bucket_zone_seconds,
    calendar_period,
    collapse_to_3_zones,
    fill_days,
    period_start,
    period_window,
    query_facts,
    resolve_since,
    resolve_window,
    rolling_bin_start,
    zone_minutes,
)
from app.services.weeks import MONDAY, resolve_week_start
# #746: the efficiency chart's condition flags reuse the analysis layer's own
# thresholds rather than restating them; see the constants below.
from app.services.analysis.classifier import _HILLY_GAIN_PER_KM
from app.services.analysis.discount_signals import HEAT_TEMP_C
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

# #804: the fact stream, its windows, its buckets, its zones and its norms all live
# in `app.services.activity_facts` now. This module keeps the Trends REPORT: which
# questions to ask of that stream for a given range and framing, and how to shape the
# answers into the chart payloads. The names below are re-exported deliberately —
# they are the vocabulary the Trends tests and the coach signals already speak, and
# re-pointing them at the shared module is what makes one definition serve both.
WeekBucket = Bucket
PeriodBucket = Bucket
_RANGE_DAYS = RANGE_DAYS
_collapse_to_3_zones = collapse_to_3_zones
_resolve_since = resolve_since
_period_window = period_window
_resolve_window = resolve_window
_zone_minutes = zone_minutes


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
    db,
    start_date,
    end_date,
    types=None,
    *,
    user_id=None,
    include_session_shape: bool = False,
):
    """Deprecated alias for the shared projection (#804).

    Kept so the Trends tests that name it keep working; `activity_facts.query_facts`
    is the one definition, and the coach signals import THAT rather than reaching
    into this module for a private name.
    """
    return query_facts(
        db, start_date, end_date, types,
        user_id=user_id, include_session_shape=include_session_shape,
    )


def build_activity_facts(
    db: Session,
    range_key: str = "30D",
    types: Optional[List[str]] = None,
    *,
    user_id=None,
    since: Optional[date] = None,
    until: Optional[date] = None,
) -> List[ActivityFact]:
    """
    Query activities within the given range and project them into ActivityFact rows.
    Optionally filter by activity type (case-insensitive).
    Pass ``user_id`` to restrict results to a single owner. Pass ``since`` to override
    the window start (the #400 calendar mode); otherwise it derives from range_key.
    ``until`` is an exclusive upper bound when a report is anchored before today.
    """
    resolved_since = since if since is not None else resolve_since(range_key)
    return query_facts(db, resolved_since, until, types, user_id=user_id)


def _window_frame(
    range_key: str, since: Optional[date], until: Optional[date]
) -> tuple[Optional[date], date]:
    """The ``(since, end)`` frame a chart builder buckets over.

    ``since`` defaults to the range's rolling start; ``until`` (the #413 calendar
    frame, e.g. the full Mon-Sun week) defaults to today. Stated once so every
    builder below frames identically.
    """
    resolved_since = since if since is not None else resolve_since(range_key)
    return resolved_since, (until if until is not None else date.today())


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
    full Mon-Sun week for 7D), with days after today rendered as empty bars.
    Defaults to today (rolling).
    """
    since, end = _window_frame(range_key, since, until)
    if since is not None:
        start = since
    elif daily_facts:
        start = daily_facts[0].local_date
    else:
        start = end
    return fill_days(daily_facts, start, end)


def build_weekly_buckets(
    daily_facts: List[DailyFact],
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
    pre_window_daily: Optional[List[DailyFact]] = None,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[Bucket]:
    """Roll daily facts into 7-day buckets, continuous across the window.

    A thin framing wrapper over the shared bucketer (#804): resolve the window, then
    ask for ``weekly`` granularity. ``rolling_anchor`` (today) buckets by 7-day blocks
    rolling back from that anchor instead of the runner's calendar weeks (#630);
    ``pre_window_daily`` carries a leading edge bucket's out-of-window value (#566).
    """
    since, end = _window_frame(range_key, since, until)
    return bucket_daily_facts(
        daily_facts, "weekly", since=since, end=end,
        rolling_anchor=rolling_anchor, week_starts_on=week_starts_on,
        pre_window_daily=pre_window_daily,
    )


def build_period_buckets(
    daily_facts: List[DailyFact],
    period: str,
    range_key: str = "30D",
    since: Optional[date] = None,
    until: Optional[date] = None,
    pre_window_daily: Optional[List[DailyFact]] = None,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[Bucket]:
    """Roll daily facts into coarse buckets (#432) for ``biweekly`` or ``monthly``.

    The same framing wrapper as ``build_weekly_buckets`` over the same shared
    bucketer — the two differ only in the granularity they ask for.
    """
    since, end = _window_frame(range_key, since, until)
    return bucket_daily_facts(
        daily_facts, period, since=since, end=end,
        rolling_anchor=rolling_anchor, week_starts_on=week_starts_on,
        pre_window_daily=pre_window_daily,
    )



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
    since, end = _window_frame(range_key, since, until)
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
# the projection, so a hilly, stop-heavy or hot activity is not silently read as
# less fit (#746). The two thresholds that already exist elsewhere are IMPORTED
# rather than restated, so the chart cannot drift from the analysis layer's own
# definition of "hilly" and "hot": HILLY is the classifier's hilly gate, HOT is the
# heat threshold the coach's discount signals already fire on. STOPPY has no prior
# home and is defined here — a meaningful share of elapsed time stopped (the speed
# term already uses moving time, so stops act mainly on avg_hr).
_EFFICIENCY_HILLY_GAIN_PER_KM = _HILLY_GAIN_PER_KM
_EFFICIENCY_HOT_TEMP_C = HEAT_TEMP_C
_EFFICIENCY_STOPPY_FRACTION = 0.10

# Which of the two means the headline should rest on is a DISPLAY decision, so the
# threshold lives with its only reader (`MIN_CLEAN_ACTIVITIES_FOR_COMPARISON` in
# frontend/app/trends/page.tsx). This module supplies the counts it needs, and
# `efficiency_window_stats` computes both means unconditionally.


def build_efficiency_trend(facts: List[ActivityFact]) -> List[dict]:
    """
    Build data points for Efficiency = Speed (m/s) / HR (bpm).
    Only includes activities with distance > 1km and valid HR.

    Each point also carries a stable activity_id (#745, so same-day activities are
    individually addressable) and condition flags — hills, stops and heat — derived
    from fields already on the projection, so the chart can SURFACE the confounders
    (#746) rather than present an unadjusted number as pure fitness.

    The confounders are flagged, never folded into the value: the metric itself is
    unchanged. A grade- or heat-ADJUSTED efficiency is deliberately not computed,
    because neither could be honestly grounded from what is stored — `elev_gain_m`
    is a single scalar with no climb/descent split, so a credible grade adjustment
    has nothing to work from, and `average_temp` is dry-bulb with no humidity, so
    there is no true heat index. An opaque "adjusted" figure would fail the issue's
    own explainability bar. The flags plus the like-for-like clean-window mean
    (`efficiency_window_stats`) do the work instead.
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

        # Heat. An UNRECORDED temperature is not hot — absent is absent, and
        # inventing heat that was never measured is exactly what the analysis
        # layer's own `confidence` field refuses to do.
        average_temp = f.average_temp
        hot = average_temp is not None and average_temp >= _EFFICIENCY_HOT_TEMP_C

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
            "average_temp": round(average_temp, 1) if average_temp is not None else None,
            "hot": hot,
        })

    return sorted(points, key=lambda p: p["date"])


def build_zone_load_weekly(
    activity_facts: List[ActivityFact],
    weekly_buckets: List[Bucket],
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[dict]:
    """Per-week 3-zone minutes, one point per weekly bucket (continuous).

    ``rolling_anchor`` must match the value passed to ``build_weekly_buckets`` so the
    zone keys line up with the bars (#630): today-anchored 7-day bins when set, the
    runner's calendar weeks otherwise.
    """
    return _zone_points(
        activity_facts, weekly_buckets, "week_start",
        bucket_key_fn("weekly", rolling_anchor, week_starts_on),
    )


def build_zone_load_daily(
    activity_facts: List[ActivityFact],
    continuous_daily: List[DailyFact],
) -> List[dict]:
    """Per-day 3-zone minutes, continuous (every day in the range gets a row)."""
    return _zone_points(activity_facts, continuous_daily, "date", lambda d: d)


def build_zone_load_period(
    activity_facts: List[ActivityFact],
    period_buckets: List[Bucket],
    period: str,
    rolling_anchor: Optional[date] = None,
    week_starts_on: int = MONDAY,
) -> List[dict]:
    """Per-coarse-bucket 3-zone minutes (#432), continuous over ``period_buckets``.

    ``rolling_anchor`` must match ``build_period_buckets`` so the zone keys line up
    with the bars (#630).
    """
    return _zone_points(
        activity_facts, period_buckets, "period_start",
        bucket_key_fn(period, rolling_anchor, week_starts_on),
    )


def _zone_points(
    activity_facts: List[ActivityFact],
    buckets,
    key_name: str,
    key,
) -> List[dict]:
    """Shape the shared zone accumulation into chart points, one per displayed
    bucket, zero-filled where the bucket has no zone data.

    ``key_name`` is the point's own date field (``date``/``week_start``/
    ``period_start``) and ``key`` the bucket-keying rule the value bars used. The
    three public builders above differ only in those two, which is why the
    accumulation itself lives once in ``activity_facts.bucket_zone_seconds``.
    """
    by_key = bucket_zone_seconds(activity_facts, key)
    result: List[dict] = []
    for b in buckets:
        bucket_key = b.local_date if key_name == "date" else b.start
        easy_s, mod_s, hard_s = by_key.get(bucket_key, (0, 0, 0))
        result.append({
            key_name: bucket_key.isoformat(),
            "easy_min": round(easy_s / 60, 1),
            "moderate_min": round(mod_s / 60, 1),
            "hard_min": round(hard_s / 60, 1),
        })
    return result


class EfficiencyWindowStats(NamedTuple):
    """One window's efficiency means, on both bases (#385, #746)."""
    avg: Optional[float]
    avg_clean: Optional[float]
    clean_count: int
    total_count: int


def efficiency_window_stats(facts: List[ActivityFact]) -> EfficiencyWindowStats:
    """Mean HR-efficiency (m/s per bpm) over the window on two bases.

    Reuses build_efficiency_trend so both means are taken over exactly the
    activities the efficiency chart plots (#385) and read the same condition flags
    the chart draws — one definition of "which activities count" and one of "which
    of them were clean", rather than a second opinion computed here.

    ``avg`` is the unweighted mean over every plotted activity, unchanged and
    retained so nothing reading it breaks. ``avg_clean`` is the mean over the
    CLEAN ones only — not hilly, not stoppy, not hot — which is what makes the
    headline "+X% vs prev" a like-for-like comparison instead of a mixed-conditions
    window mean that moves for reasons that are not fitness (#746). Both are None
    when the respective set is empty; the counts let the caller (and the runner)
    see which basis a comparison actually rests on.
    """
    points = build_efficiency_trend(facts)
    clean = [p for p in points if not (p["hilly"] or p["stoppy"] or p["hot"])]

    def _mean(rows) -> Optional[float]:
        if not rows:
            return None
        return round(sum(p["efficiency_mps_per_bpm"] for p in rows) / len(rows), 4)

    return EfficiencyWindowStats(
        avg=_mean(points),
        avg_clean=_mean(clean),
        clean_count=len(clean),
        total_count=len(points),
    )


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
    current_start, prev_start = period_window("7D")  # type: ignore[misc]

    current = query_facts(db, current_start, None, user_id=user_id)
    previous = query_facts(db, prev_start, current_start, user_id=user_id)

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
    as_of: Optional[date] = None,
) -> TrendsResponse:
    """
    Main entry point for generating the complete trends report.
    Orchestrates data fetching and aggregation.
    Pass ``user_id`` to restrict results to a single owner. ``mode`` is the #400
    global window framing: ``rolling`` (trailing N days, the default) or
    ``calendar`` (the current calendar period — week/month/quarter/half/year — for
    the range), which shifts the window start and the previous-period comparison
    for the whole report (summary, deltas, and every chart). ``as_of`` (#948) is
    the date the window is judged as of, defaulting to today; passing an earlier
    date shows the same (range, mode) window as it stood on that date instead of
    the one ending today, so window-navigation arrows can step the whole report
    back and forward a period at a time.
    """
    range_upper = range_key.upper()
    if range_upper not in ALLOWED_RANGES:
        range_upper = "30D"
    if mode not in ("rolling", "calendar"):
        mode = "rolling"
    resolved_as_of = as_of or date.today()

    # The (range, mode) window: `since` starts the current window; the previous
    # comparison spans [prev_start, prev_end). Calendar mode shifts both.
    since, prev_start, prev_end = resolve_window(range_upper, mode, today=resolved_as_of)

    # The runner's chosen week start (0=Monday default, 6=Sunday), which the
    # calendar-mode weekly/biweekly bars align to (#676). Rolling mode buckets by
    # today-anchored blocks, so it is week-start-independent there.
    week_starts_on = resolve_week_start(
        db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if user_id is not None
        else None
    )

    # Rolling mode buckets the bars in fixed-width blocks rolling back from
    # `as_of` rather than snapping to the calendar grid (#630). None keeps the
    # calendar keying (ISO-Monday weeks / calendar months / the fortnight grid).
    rolling_anchor: Optional[date] = resolved_as_of if mode == "rolling" else None

    # Chart x-axis frame end (#413): rolling stops at `as_of` (today by default);
    # calendar spans the whole current period (e.g. Mon–Sun for 7D), so the
    # period's last day frames the chart and days after `as_of` render as empty
    # bars. Set explicitly (rather than left None) so a stepped-back `as_of`
    # cannot fall through to a builder's own real-today default (#948).
    until: Optional[date] = resolved_as_of
    if mode == "calendar" and range_upper in RANGE_DAYS and RANGE_DAYS[range_upper]:
        _, until, _, _ = calendar_period(range_upper, resolved_as_of, week_starts_on)

    # 1. Activity-level facts (filtered by types if provided)
    activity_facts = build_activity_facts(
        db,
        range_upper,
        types=types,
        user_id=user_id,
        since=since,
        until=resolved_as_of + timedelta(days=1),
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
                rolling_bin_start(since, rolling_anchor, d) for d in (7, 14, 30)
            )
        else:
            pre_start = period_start(since, "monthly")
        if pre_start < since:
            pre_facts = query_facts(db, pre_start, since, types=types, user_id=user_id)
            pre_window_daily = build_daily_facts(pre_facts)

    # Summary totals across the entire range
    cur_easy, cur_mod, cur_hard = zone_minutes(activity_facts)
    cur_eff = efficiency_window_stats(activity_facts)
    summary = TrendsSummary(
        total_distance_m=sum(d.total_distance_m for d in daily_facts),
        total_moving_time_s=sum(d.total_moving_time_s for d in daily_facts),
        activity_count=sum(d.activity_count for d in daily_facts),
        total_suffer_score=sum(d.total_effort_score for d in daily_facts),
        avg_efficiency_mps_per_bpm=cur_eff.avg,
        avg_efficiency_clean_mps_per_bpm=cur_eff.avg_clean,
        efficiency_clean_count=cur_eff.clean_count,
        efficiency_total_count=cur_eff.total_count,
        zone_easy_minutes=cur_easy,
        zone_moderate_minutes=cur_mod,
        zone_hard_minutes=cur_hard,
    )

    # Previous period summary (vs the equivalent prior window for this mode)
    previous_summary = None
    if prev_start is not None and prev_end is not None:
        prev_facts = query_facts(db, prev_start, prev_end, types=types, user_id=user_id)
        prev_easy, prev_mod, prev_hard = zone_minutes(prev_facts)
        prev_eff = efficiency_window_stats(prev_facts)
        previous_summary = TrendsSummary(
            total_distance_m=sum(f.distance_m for f in prev_facts),
            total_moving_time_s=sum(f.moving_time_s for f in prev_facts),
            activity_count=len(prev_facts),
            total_suffer_score=sum(f.effort_score or 0.0 for f in prev_facts),
            avg_efficiency_mps_per_bpm=prev_eff.avg,
            avg_efficiency_clean_mps_per_bpm=prev_eff.avg_clean,
            efficiency_clean_count=prev_eff.clean_count,
            efficiency_total_count=prev_eff.total_count,
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

    def _period_distance(buckets: List[Bucket]) -> List[PeriodDistancePoint]:
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

    def _period_time(buckets: List[Bucket]) -> List[PeriodTimePoint]:
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

    def _period_suffer(buckets: List[Bucket]) -> List[PeriodSufferScorePoint]:
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
    from app.services.coach.volume import build_volume_report

    resolved = as_of or date.today()
    week_starts_on = resolve_week_start(
        db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if user_id is not None
        else None
    )
    key = range_key if range_key in RANGE_WINDOW_DAYS else "7D"
    n = RANGE_WINDOW_DAYS[key]
    roll_start = resolved - timedelta(days=n - 1)
    p_start, _, _, _ = calendar_period(key, resolved, week_starts_on)
    # Fetch back to the earliest window start plus the term-scaled norm baseline.
    earliest = min(roll_start, p_start) - timedelta(days=BASELINE_DAYS_BY_RANGE[key] + 1)
    facts = query_facts(db, earliest, resolved + timedelta(days=1), types, user_id=user_id)
    return build_volume_report(facts, resolved, key, week_starts_on)
