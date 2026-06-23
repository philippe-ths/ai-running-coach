"""#400: the deterministic frequency-/volume-vs-norm signal.

Answers, per training metric, "is this window up, down, or normal for this
runner" — so the coach reads a deliberate easy week as intentional rather than as
a vague worry. Pure functions over already-fetched activity facts (no DB, no LLM);
the DB read and pack wiring live in `context.py`.

Two framings, both surfaced (the runner reasons in both):
  - rolling_7d: the trailing 7 days, a full week directly comparable to the norm.
  - calendar_week: the current Monday-Sunday block to date (partial until Sunday),
    judged against the norm PRO-RATED to the elapsed days so a partial week is fair.

The norm is the runner's own per-day training rate over history BEFORE the current
7 days, projected to a weekly figure (#451): the SAME clamped per-day-rate
definition the Trends page and `recent_training` use, so "typical" means one thing
across the product. A 12-week stable baseline plus a 4-week recent baseline, each
clamped to the runner's first activity so a short or gappy history is not deflated
by dividing it across weeks the runner had not started training. Direction uses a
deadband so small fluctuations read as in_line.

Metrics are reported holistically (every logged activity — the cardio view the
runner intends) with a runs-only figure alongside, so the coach never reads a walk
as a run. The norm and the direction verdict are on the holistic total.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.coach_context import (
    TrainingVolumeContext,
    VolumeMetricComparison,
    VolumeWindow,
)
from app.schemas.trends import VolumeFraming, VolumeMetricVsNorm, VolumeReport

# The metrics compared, in a fixed order (byte-stable pack).
_METRICS = ("sessions", "distance_m", "moving_time_s", "effort_score")

_BASELINE_WEEKS = 12        # the stable norm window (history before the current 7d)
_RECENT_WEEKS = 4           # the recent-context norm window
_DEADBAND_PCT = 15.0        # within +/- this of norm reads as in_line
# Below this many activities in the 12-week baseline, history is too thin to call a
# norm — every direction abstains as no_norm (mirrors the M9/baseline abstain tiers).
_MIN_BASELINE_ACTIVITIES = 4


def _is_run(fact: Any) -> bool:
    return (getattr(fact, "activity_type", "") or "").lower() == "run"


def _metric_value(fact: Any, metric: str) -> float:
    if metric == "sessions":
        return 1
    if metric == "distance_m":
        return fact.distance_m or 0
    if metric == "moving_time_s":
        return fact.moving_time_s or 0
    if metric == "effort_score":
        return fact.effort_score or 0
    return 0


def _sum(facts: List[Any], metric: str, *, runs_only: bool = False) -> float:
    return sum(
        _metric_value(f, metric)
        for f in facts
        if (not runs_only or _is_run(f))
    )


def _direction(current: float, norm: Optional[float], factor: float) -> str:
    """Label current against a norm pro-rated by `factor` (1.0 for a full window,
    days_elapsed/7 for a partial calendar week)."""
    if norm is None:
        return "no_norm"
    comparable = norm * factor
    if comparable <= 0:
        return "no_norm"
    pct = (current - comparable) / comparable * 100.0
    if pct > _DEADBAND_PCT:
        return "up"
    if pct < -_DEADBAND_PCT:
        return "down"
    return "in_line"


def _metric_comparison(
    metric: str,
    window_facts: List[Any],
    factor: float,
    norm_weekly: Optional[float],
    norm_recent: Optional[float],
) -> VolumeMetricComparison:
    current_all = _sum(window_facts, metric)
    current_runs = _sum(window_facts, metric, runs_only=True)

    pct: Optional[float] = None
    if norm_weekly is not None and norm_weekly * factor > 0:
        comparable = norm_weekly * factor
        pct = round((current_all - comparable) / comparable * 100.0, 1)

    return VolumeMetricComparison(
        metric=metric,
        current_all=current_all,
        current_runs=current_runs,
        norm_weekly=round(norm_weekly, 1) if norm_weekly is not None else None,
        norm_weekly_recent=round(norm_recent, 1) if norm_recent is not None else None,
        pct_vs_norm=pct,
        direction=_direction(current_all, norm_weekly, factor),
        direction_recent=_direction(current_all, norm_recent, factor),
    )


def _window(
    window: str,
    window_facts: List[Any],
    days_elapsed: int,
    norms_weekly: dict,
    norms_recent: dict,
) -> VolumeWindow:
    factor = days_elapsed / 7.0
    return VolumeWindow(
        window=window,
        days_elapsed=days_elapsed,
        complete=days_elapsed >= 7,
        metrics=[
            _metric_comparison(
                m, window_facts, factor, norms_weekly.get(m), norms_recent.get(m)
            )
            for m in _METRICS
        ],
    )


def build_training_volume(facts: List[Any], as_of: date) -> TrainingVolumeContext:
    """Build the volume-vs-norm signal as of `as_of` from facts spanning at least
    the trailing ~91 days. Facts are duck-typed: each needs `local_date`,
    `activity_type`, `distance_m`, `moving_time_s`, `effort_score`."""
    # Current windows.
    rolling_start = as_of - timedelta(days=6)  # 7 days ending on as_of inclusive
    rolling_facts = [f for f in facts if rolling_start <= f.local_date <= as_of]

    week_start = as_of - timedelta(days=as_of.weekday())  # Monday of this week
    week_facts = [f for f in facts if week_start <= f.local_date <= as_of]
    week_days_elapsed = as_of.weekday() + 1  # Mon=1 .. Sun=7

    # Norm baselines: the runner's own per-day training rate over history strictly
    # BEFORE the current 7-day window, projected to a weekly figure (#451). This is the
    # SAME clamped per-day-rate definition the Trends page and recent_training use
    # (_baseline_window clamps the window to the runner's first activity, _norm_per_day
    # divides by actual calendar days), so "typical" has ONE meaning across the product
    # — and a newer/returning runner's norm is no longer deflated by dividing a short
    # history by a fixed 12 (or 4) weeks.
    baseline_end = as_of - timedelta(days=7)

    def _weekly_norm(nominal_weeks: int) -> Dict[str, Optional[float]]:
        bl = _baseline_window(facts, baseline_end, nominal_weeks * 7)
        if bl is None:
            return {m: None for m in _METRICS}
        b_start, b_end, b_count = bl
        per_day = _norm_per_day(facts, b_start, b_end, b_count)
        return {m: (per_day[m] * 7 if per_day[m] is not None else None) for m in _METRICS}

    norms_weekly = _weekly_norm(_BASELINE_WEEKS)
    norms_recent = _weekly_norm(_RECENT_WEEKS)
    # has_baseline tracks whether the stable (12-week) norm resolved; every metric
    # resolves together (one shared activity-count threshold), so any metric answers.
    has_baseline = norms_weekly[_METRICS[0]] is not None

    return TrainingVolumeContext(
        rolling_7d=_window("rolling_7d", rolling_facts, 7, norms_weekly, norms_recent),
        calendar_week=_window(
            "calendar_week", week_facts, week_days_elapsed, norms_weekly, norms_recent
        ),
        baseline_weeks=_BASELINE_WEEKS,
        baseline_weeks_recent=_RECENT_WEEKS,
        has_baseline=has_baseline,
    )


# ---------------------------------------------------------------------------
# #400 range-aware report for the Trends page. Reuses the core helpers above
# (_sum, _is_run, _direction, _METRICS); the coach pack path (build_training_volume)
# is unchanged — the coach always reasons about "this week vs norm", while the
# Trends page generalizes to the selected range with a scaled norm.
# ---------------------------------------------------------------------------

# Range key -> the rolling window length in days.
_RANGE_WINDOW_DAYS = {"7D": 7, "30D": 30, "3M": 90, "6M": 180, "1Y": 365}

# Term-scaled norm baseline: a longer term reads "typical" from a longer history.
# Capped at ~1 year to stay within a typical runner's data; the actual span is
# further clamped to available history (so pre-history days never deflate the rate).
_BASELINE_DAYS_BY_RANGE = {"7D": 84, "30D": 168, "3M": 365, "6M": 365, "1Y": 365}
_BASELINE_LABEL_BY_RANGE = {
    "7D": "the last 12 weeks",
    "30D": "the last 6 months",
    "3M": "the last year",
    "6M": "the last year",
    "1Y": "the last year",
}


def _baseline_window(
    facts: List[Any], baseline_end: date, nominal_days: int
) -> Optional[Tuple[date, date, int]]:
    """The norm baseline date range ending on `baseline_end`, clamped so it never
    starts before the runner's first activity (pre-history zeros would otherwise
    deflate the per-day rate). Returns (start, end, day_count) or None."""
    nominal_start = baseline_end - timedelta(days=nominal_days - 1)
    earliest = min((f.local_date for f in facts), default=None)
    start = nominal_start if earliest is None else max(nominal_start, earliest)
    if start > baseline_end:
        return None
    return start, baseline_end, (baseline_end - start).days + 1


def _norm_per_day(
    facts: List[Any], start: date, end: date, day_count: int
) -> Dict[str, Optional[float]]:
    """Per-metric average daily output over [start, end] (`day_count` calendar days),
    or None per metric when too few activities fall in it. Calendar days (not active
    days) are the denominator, so rest days count as zero — a true average daily
    rate that scales to any window length."""
    window = [f for f in facts if start <= f.local_date <= end]
    if len(window) < _MIN_BASELINE_ACTIVITIES:
        return {m: None for m in _METRICS}
    return {m: _sum(window, m) / day_count for m in _METRICS}


def _report_metric(
    metric: str,
    window_facts: List[Any],
    norm_days: int,
    norm_pd: Dict[str, Optional[float]],
) -> VolumeMetricVsNorm:
    current_all = _sum(window_facts, metric)
    current_runs = _sum(window_facts, metric, runs_only=True)
    pd = norm_pd.get(metric)
    # Scale the per-day norm to the framing's FULL period length (#436), so the
    # period-to-date total is judged against the runner's typical full-period total
    # — e.g. a Monday's run vs a typical full week, not vs a single typical day.
    # For rolling, norm_days == the window length, so this is unchanged there; only
    # a partial calendar period changes (it no longer reads "in line" mid-period).
    norm = pd * norm_days if pd is not None else None

    pct: Optional[float] = None
    if norm is not None and norm > 0:
        pct = round((current_all - norm) / norm * 100.0, 1)

    return VolumeMetricVsNorm(
        metric=metric,
        current_all=current_all,
        current_runs=current_runs,
        norm=round(norm, 1) if norm is not None else None,
        norm_recent=None,
        pct_vs_norm=pct,
        direction=_direction(current_all, norm, 1.0),  # norm already scaled
        direction_recent="no_norm",
    )


def _report_framing(
    facts: List[Any],
    framing: str,
    label: str,
    win_start: date,
    win_end: date,
    window_days: int,
    complete: bool,
    baseline_days: int,
) -> VolumeFraming:
    window_facts = [f for f in facts if win_start <= f.local_date <= win_end]
    days_elapsed = (win_end - win_start).days + 1
    # The norm baseline ends the day before THIS framing's window starts, so the
    # comparison is "this window vs the runner's typical preceding history".
    bl = _baseline_window(facts, win_start - timedelta(days=1), baseline_days)
    if bl is None:
        norm_pd: Dict[str, Optional[float]] = {m: None for m in _METRICS}
        b_start = b_end = None
    else:
        b_start, b_end, b_count = bl
        norm_pd = _norm_per_day(facts, b_start, b_end, b_count)
        # If the window didn't qualify (too few activities), drop the dates too.
        if all(v is None for v in norm_pd.values()):
            b_start = b_end = None
    return VolumeFraming(
        framing=framing,
        label=label,
        window_days=window_days,
        days_elapsed=days_elapsed,
        complete=complete,
        period_start=win_start,
        period_end=win_end,
        baseline_start=b_start,
        baseline_end=b_end,
        metrics=[
            # Scale the norm by the FULL period length (#436), not days_elapsed, so
            # the "vs typical" read is full-period (consistent with "vs last period").
            _report_metric(m, window_facts, window_days, norm_pd) for m in _METRICS
        ],
    )


def _calendar_period(range_key: str, as_of: date) -> Tuple[date, date, int, str]:
    """The current calendar period for a range: (start, last_day, length_days, label).
    The period runs from `start` to `last_day`; the caller compares to-date (start..as_of)."""
    if range_key == "7D":
        start = as_of - timedelta(days=as_of.weekday())  # Monday
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


def build_volume_report(facts: List[Any], as_of: date, range_key: str) -> VolumeReport:
    """The Trends-page volume-vs-norm report for `range_key` (7D/30D/3M/6M/1Y) as of
    `as_of`. Two framings — rolling (trailing N days) and calendar (current period to
    date) — each metric vs the runner's norm for a FULL period of that length (#436):
    rolling compares a complete window, and calendar compares the period-to-date total
    against the typical full-period total (so it reads as progress through the period,
    consistent with "vs last period", rather than pro-rating to days elapsed). Reuses
    the same pure core as the coach pack so the two never drift."""
    n = _RANGE_WINDOW_DAYS[range_key]
    baseline_days = _BASELINE_DAYS_BY_RANGE[range_key]
    roll_start = as_of - timedelta(days=n - 1)
    rolling = _report_framing(
        facts, "rolling", f"{n}-day rolling", roll_start, as_of, n, True, baseline_days
    )

    p_start, p_last, p_days, p_label = _calendar_period(range_key, as_of)
    calendar = _report_framing(
        facts, "calendar", p_label, p_start, as_of, p_days, as_of >= p_last, baseline_days
    )

    has_baseline = any(m.norm is not None for m in rolling.metrics)
    return VolumeReport(
        range=range_key,
        rolling=rolling,
        calendar=calendar,
        has_baseline=has_baseline,
        baseline_label=_BASELINE_LABEL_BY_RANGE[range_key],
    )
