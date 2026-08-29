"""#400: the deterministic frequency-/volume-vs-norm signal.

Answers, per training metric, "is this window up, down, or normal for this
runner" — so the coach reads a deliberate easy week as intentional rather than as
a vague worry. Pure functions over already-fetched activity facts (no DB, no LLM);
the DB read and pack wiring live in `context.py`.

Two framings, both surfaced (the runner reasons in both):
  - rolling_7d: the trailing 7 days, a full week directly comparable to the norm.
  - calendar_week: the current calendar-week block to date, aligned to the runner's
    own week-start (partial until the week closes), judged against the norm PRO-RATED
    to the elapsed days so a partial week is fair.

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

One lane per fact: in the COACH PACK, under a recent-training-aware prompt (v11+),
the `rolling_7d` window drops its raw `current_all`/`current_runs` at serialization,
because it spans the same trailing 7 days as `recent_training.last_7d` and so its
totals were a second copy of that section's roll-up. `rolling_7d` then carries the
vs-norm VERDICT only (norm + direction + pct); the actual trailing-7d numbers live in
`recent_training`. The drop is GATED on `recent_training` being present (v9/v10 keep
the values, since no descriptive lane exists there to carry them).
`calendar_week` keeps its current values (no other section carries the
week-start-to-date window). The builder below still COMPUTES current on both windows (direction/pct
derive from it); the drop is a pack-serialization concern only (see
`coach_context._drop_training_volume_rolling_current`), so this module and the
Trends-page `build_volume_report` are unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.schemas.coach_context import (
    TrainingVolumeContext,
    VolumeMetricComparison,
    VolumeWindow,
)
from app.schemas.trends import VolumeFraming, VolumeMetricVsNorm, VolumeReport
from app.services.activity_facts import (
    BASELINE_DAYS_BY_RANGE,
    BASELINE_LABEL_BY_RANGE,
    BASELINE_WEEKS,
    METRICS,
    RANGE_WINDOW_DAYS,
    RECENT_WEEKS,
    baseline_window,
    calendar_period,
    direction,
    norm_per_day,
    sum_metric,
)
from app.services.weeks import MONDAY, days_into_week, week_start

TRENDS_METRICS = (*METRICS, "zone_2_plus_minutes")

# #804: the rollup primitives this module used to own (the metric set, the per-metric
# sum, the runs-only distinction, the clamped per-day norm, the deadbanded direction
# verdict) and the calendar-period arithmetic now live in `app.services.activity_facts`
# and are imported above. `recent_weeks` and `recent_training` used to import them from
# HERE by their private names, and the Trends module reached in for `_calendar_period`
# from inside a function body to dodge the resulting import cycle. With one definition
# underneath both, that cycle is gone and nothing borrows a private name.


def _metric_comparison(
    metric: str,
    window_facts: List[Any],
    factor: float,
    norm_weekly: Optional[float],
    norm_recent: Optional[float],
) -> VolumeMetricComparison:
    current_all = round(sum_metric(window_facts, metric), 1)
    current_runs = round(sum_metric(window_facts, metric, runs_only=True), 1)

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
        direction=direction(current_all, norm_weekly, factor),
        direction_recent=direction(current_all, norm_recent, factor),
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
            for m in METRICS
        ],
    )


def build_training_volume(
    facts: List[Any], as_of: date, week_starts_on: int = MONDAY
) -> TrainingVolumeContext:
    """Build the volume-vs-norm signal as of `as_of` from facts spanning at least
    the trailing ~91 days. Facts are duck-typed: each needs `local_date`,
    `activity_type`, `distance_m`, `moving_time_s`, `effort_score`.

    `week_starts_on` (0=Monday default, 6=Sunday) sets the calendar-week boundary
    so it matches the runner's chosen week start (#676); rolling_7d is
    week-start-independent."""
    # Current windows.
    rolling_start = as_of - timedelta(days=6)  # 7 days ending on as_of inclusive
    rolling_facts = [f for f in facts if rolling_start <= f.local_date <= as_of]

    wk_start = week_start(as_of, week_starts_on)  # first day of this week
    week_facts = [f for f in facts if wk_start <= f.local_date <= as_of]
    week_days_elapsed = days_into_week(as_of, week_starts_on)

    # Norm baselines: the runner's own per-day training rate over history strictly
    # BEFORE the current 7-day window, projected to a weekly figure (#451). This is the
    # SAME clamped per-day-rate definition the Trends page and recent_training use
    # (baseline_window clamps the window to the runner's first activity, norm_per_day
    # divides by actual calendar days), so "typical" has ONE meaning across the product
    # — and a newer/returning runner's norm is no longer deflated by dividing a short
    # history by a fixed 12 (or 4) weeks.
    baseline_end = as_of - timedelta(days=7)

    def _weekly_norm(nominal_weeks: int) -> Dict[str, Optional[float]]:
        bl = baseline_window(facts, baseline_end, nominal_weeks * 7)
        if bl is None:
            return {m: None for m in METRICS}
        b_start, b_end, b_count = bl
        per_day = norm_per_day(facts, b_start, b_end, b_count)
        return {m: (per_day[m] * 7 if per_day[m] is not None else None) for m in METRICS}

    norms_weekly = _weekly_norm(BASELINE_WEEKS)
    norms_recent = _weekly_norm(RECENT_WEEKS)
    # has_baseline tracks whether the stable (12-week) norm resolved; every metric
    # resolves together (one shared activity-count threshold), so any metric answers.
    has_baseline = norms_weekly[METRICS[0]] is not None

    return TrainingVolumeContext(
        rolling_7d=_window("rolling_7d", rolling_facts, 7, norms_weekly, norms_recent),
        calendar_week=_window(
            "calendar_week", week_facts, week_days_elapsed, norms_weekly, norms_recent
        ),
        baseline_weeks=BASELINE_WEEKS,
        baseline_weeks_recent=RECENT_WEEKS,
        has_baseline=has_baseline,
    )


# ---------------------------------------------------------------------------
# #400 range-aware report for the Trends page. Reuses the core helpers above
# (sum_metric, _is_run, direction, METRICS); the coach pack path (build_training_volume)
# is unchanged — the coach always reasons about "this week vs norm", while the
# Trends page generalizes to the selected range with a scaled norm.
# ---------------------------------------------------------------------------

def _report_metric(
    metric: str,
    window_facts: List[Any],
    norm_days: int,
    norm_pd: Dict[str, Optional[float]],
) -> VolumeMetricVsNorm:
    current_all = round(sum_metric(window_facts, metric), 1)
    current_runs = round(sum_metric(window_facts, metric, runs_only=True), 1)
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
        direction=direction(current_all, norm, 1.0),  # norm already scaled
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
    bl = baseline_window(facts, win_start - timedelta(days=1), baseline_days)
    if bl is None:
        norm_pd: Dict[str, Optional[float]] = {m: None for m in TRENDS_METRICS}
        b_start = b_end = None
    else:
        b_start, b_end, b_count = bl
        norm_pd = norm_per_day(facts, b_start, b_end, b_count, TRENDS_METRICS)
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
            _report_metric(m, window_facts, window_days, norm_pd)
            for m in TRENDS_METRICS
        ],
    )


def build_volume_report(
    facts: List[Any], as_of: date, range_key: str, week_starts_on: int = MONDAY
) -> VolumeReport:
    """The Trends-page volume-vs-norm report for `range_key` (7D/30D/3M/6M/1Y) as of
    `as_of`. Two framings — rolling (trailing N days) and calendar (current period to
    date) — each metric vs the runner's norm for a FULL period of that length (#436):
    rolling compares a complete window, and calendar compares the period-to-date total
    against the typical full-period total (so it reads as progress through the period,
    consistent with "vs last period", rather than pro-rating to days elapsed). Reuses
    the same pure core as the coach pack so the two never drift."""
    n = RANGE_WINDOW_DAYS[range_key]
    baseline_days = BASELINE_DAYS_BY_RANGE[range_key]
    roll_start = as_of - timedelta(days=n - 1)
    rolling = _report_framing(
        facts, "rolling", f"{n}-day rolling", roll_start, as_of, n, True, baseline_days
    )

    p_start, p_last, p_days, p_label = calendar_period(range_key, as_of, week_starts_on)
    calendar = _report_framing(
        facts, "calendar", p_label, p_start, as_of, p_days, as_of >= p_last, baseline_days
    )

    has_baseline = any(m.norm is not None for m in rolling.metrics)
    return VolumeReport(
        range=range_key,
        rolling=rolling,
        calendar=calendar,
        has_baseline=has_baseline,
        baseline_label=BASELINE_LABEL_BY_RANGE[range_key],
    )
