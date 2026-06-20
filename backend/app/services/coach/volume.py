"""#400: the deterministic frequency-/volume-vs-norm signal.

Answers, per training metric, "is this window up, down, or normal for this
runner" — so the coach reads a deliberate easy week as intentional rather than as
a vague worry. Pure functions over already-fetched activity facts (no DB, no LLM);
the DB read and pack wiring live in `context.py`.

Two framings, both surfaced (the runner reasons in both):
  - rolling_7d: the trailing 7 days, a full week directly comparable to the norm.
  - calendar_week: the current Monday-Sunday block to date (partial until Sunday),
    judged against the norm PRO-RATED to the elapsed days so a partial week is fair.

The norm is per-week, all-activities, computed over history BEFORE the current 7
days (so "current vs norm" compares this week to prior typical weeks, not to
itself): a 12-week stable baseline plus a 4-week recent baseline. Direction uses a
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

    # Norm baselines: history strictly BEFORE the current 7-day window.
    baseline_end = as_of - timedelta(days=7)
    base_12w_start = baseline_end - timedelta(weeks=_BASELINE_WEEKS)
    base_4w_start = baseline_end - timedelta(weeks=_RECENT_WEEKS)
    base_12w = [f for f in facts if base_12w_start < f.local_date <= baseline_end]
    base_4w = [f for f in facts if base_4w_start < f.local_date <= baseline_end]

    has_baseline = len(base_12w) >= _MIN_BASELINE_ACTIVITIES
    if has_baseline:
        norms_weekly = {m: _sum(base_12w, m) / _BASELINE_WEEKS for m in _METRICS}
        norms_recent = {m: _sum(base_4w, m) / _RECENT_WEEKS for m in _METRICS}
    else:
        norms_weekly = {m: None for m in _METRICS}
        norms_recent = {m: None for m in _METRICS}

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

_NORM_BASELINE_DAYS = 84  # 12 weeks, the stable per-day norm
_NORM_RECENT_DAYS = 28    # 4 weeks, recent context


def _norm_per_day(
    facts: List[Any], baseline_end: date, baseline_days: int
) -> Dict[str, Optional[float]]:
    """Per-metric average daily output over the `baseline_days` ending on
    `baseline_end`, or None per metric when the baseline holds too few activities.
    Calendar days (not active days) are the denominator, so rest days count as zero
    — a true average daily rate that scales to any window length."""
    start = baseline_end - timedelta(days=baseline_days - 1)
    window = [f for f in facts if start <= f.local_date <= baseline_end]
    if len(window) < _MIN_BASELINE_ACTIVITIES:
        return {m: None for m in _METRICS}
    return {m: _sum(window, m) / baseline_days for m in _METRICS}


def _report_metric(
    metric: str,
    window_facts: List[Any],
    days_elapsed: int,
    norm_pd: Dict[str, Optional[float]],
    norm_pd_recent: Dict[str, Optional[float]],
) -> VolumeMetricVsNorm:
    current_all = _sum(window_facts, metric)
    current_runs = _sum(window_facts, metric, runs_only=True)
    pd = norm_pd.get(metric)
    pdr = norm_pd_recent.get(metric)
    # Scale the per-day norm to the window's elapsed days, so a partial calendar
    # period is judged against an equally-partial norm.
    norm = pd * days_elapsed if pd is not None else None
    norm_recent = pdr * days_elapsed if pdr is not None else None

    pct: Optional[float] = None
    if norm is not None and norm > 0:
        pct = round((current_all - norm) / norm * 100.0, 1)

    return VolumeMetricVsNorm(
        metric=metric,
        current_all=current_all,
        current_runs=current_runs,
        norm=round(norm, 1) if norm is not None else None,
        norm_recent=round(norm_recent, 1) if norm_recent is not None else None,
        pct_vs_norm=pct,
        direction=_direction(current_all, norm, 1.0),  # norm already scaled
        direction_recent=_direction(current_all, norm_recent, 1.0),
    )


def _report_framing(
    facts: List[Any],
    framing: str,
    label: str,
    win_start: date,
    win_end: date,
    window_days: int,
    complete: bool,
) -> VolumeFraming:
    window_facts = [f for f in facts if win_start <= f.local_date <= win_end]
    days_elapsed = (win_end - win_start).days + 1
    # The norm baseline ends the day before THIS framing's window starts, so the
    # comparison is "this window vs the runner's typical preceding history".
    baseline_end = win_start - timedelta(days=1)
    norm_pd = _norm_per_day(facts, baseline_end, _NORM_BASELINE_DAYS)
    norm_pd_recent = _norm_per_day(facts, baseline_end, _NORM_RECENT_DAYS)
    return VolumeFraming(
        framing=framing,
        label=label,
        window_days=window_days,
        days_elapsed=days_elapsed,
        complete=complete,
        metrics=[
            _report_metric(m, window_facts, days_elapsed, norm_pd, norm_pd_recent)
            for m in _METRICS
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
    date) — each metric vs the runner's norm scaled to the window. Reuses the same
    pure core as the coach pack so the two never drift."""
    n = _RANGE_WINDOW_DAYS[range_key]
    roll_start = as_of - timedelta(days=n - 1)
    rolling = _report_framing(
        facts, "rolling", f"{n}-day rolling", roll_start, as_of, n, complete=True
    )

    p_start, p_last, p_days, p_label = _calendar_period(range_key, as_of)
    calendar = _report_framing(
        facts, "calendar", p_label, p_start, as_of, p_days, complete=as_of >= p_last
    )

    has_baseline = any(m.norm is not None for m in rolling.metrics)
    return VolumeReport(
        range=range_key,
        rolling=rolling,
        calendar=calendar,
        has_baseline=has_baseline,
        baseline_weeks=_BASELINE_WEEKS,
    )
