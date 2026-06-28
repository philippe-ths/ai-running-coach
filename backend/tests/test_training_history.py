"""#561: the multi-year training-history builder.

Ground truth is the two archetypes from the design: a long-tenured low-volume
runner (20 km/week for 10 years) and a short-tenured high-volume runner (100 km/week
for 2 years, ramped). They have near-identical lifetime distance (~10,400 km) and
must read as completely different athletes — the durability traits and the ladder
depth are what separate them.
"""

from datetime import date, timedelta

import pytest

from app.services.coach.training_history import (
    _MIN_ACTIVITIES,
    build_training_history,
)

AS_OF = date(2026, 6, 28)


class _Fact:
    """A duck-typed ActivityFact: the builder reads local_date/activity_type/distance_m."""

    def __init__(self, local_date, *, activity_type="Run", distance_m=0.0):
        self.local_date = local_date
        self.activity_type = activity_type
        self.distance_m = distance_m
        # harmless extras the builder ignores
        self.moving_time_s = 0
        self.effort_score = 0.0
        self.effort = None


def _weekly(n_weeks, dist_for_week, *, activity_type="Run", as_of=AS_OF):
    """One activity per week going back `n_weeks`, the week-w distance from `dist_for_week(w)`."""
    return [
        _Fact(as_of - timedelta(days=7 * w), activity_type=activity_type, distance_m=dist_for_week(w))
        for w in range(n_weeks)
    ]


def _veteran_low_volume():
    """20 km/week for 10 years."""
    return _weekly(520, lambda w: 20_000)


def _newer_high_volume():
    """100 km/week this past year, ramped up from 50 km/week the year before."""
    return _weekly(104, lambda w: 100_000 if w < 52 else 50_000)


# --------------------------------------------------------------------------- #
# The two archetypes render differently                                       #
# --------------------------------------------------------------------------- #


def test_veteran_low_volume_traits():
    ctx = build_training_history(_veteran_low_volume(), AS_OF)
    assert ctx is not None
    t = ctx.traits
    assert 9.5 <= t.training_age_years <= 10.5
    # Flat decade: recent year matches the prior year.
    assert t.trajectory_direction == "in_line"
    # At peak (steady), and the current load has been held for most of the decade.
    assert t.current_vs_peak_pct == 100.0
    assert t.time_at_current_load_years >= 8.0
    # Low volume throughout.
    assert 18_000 <= t.current_weekly_distance_m <= 22_000


def test_newer_high_volume_traits():
    ctx = build_training_history(_newer_high_volume(), AS_OF)
    assert ctx is not None
    t = ctx.traits
    assert 1.8 <= t.training_age_years <= 2.2
    # Ramped: recent year well above the prior year.
    assert t.trajectory_direction == "up"
    assert t.trajectory_pct is not None and t.trajectory_pct > 50.0
    # High current load, but held for only about a year.
    assert t.current_weekly_distance_m >= 80_000
    assert 0.7 <= t.time_at_current_load_years <= 1.5


def test_archetypes_are_distinguished():
    """Near-identical lifetime distance, but the signals that matter diverge."""
    vet = build_training_history(_veteran_low_volume(), AS_OF).traits
    new = build_training_history(_newer_high_volume(), AS_OF).traits
    assert vet.training_age_years > new.training_age_years
    assert vet.time_at_current_load_years > new.time_at_current_load_years
    assert vet.current_weekly_distance_m < new.current_weekly_distance_m
    assert vet.trajectory_direction == "in_line" and new.trajectory_direction == "up"


def test_ladder_depth_self_sizes_to_history():
    """The veteran fills the deep tail; the newer runner's ladder stops at 1-2 years."""
    vet = build_training_history(_veteran_low_volume(), AS_OF)
    new = build_training_history(_newer_high_volume(), AS_OF)
    vet_labels = {b.label for b in vet.timeline}
    new_labels = {b.label for b in new.timeline}
    assert "5+ years ago" in vet_labels
    assert "2-5 years ago" in vet_labels
    assert "5+ years ago" not in new_labels
    assert "2-5 years ago" not in new_labels
    # Both still describe the near-but-not-recent horizon.
    assert "1-3 months ago" in vet_labels and "1-3 months ago" in new_labels


def test_ladder_buckets_are_weekly_rates_not_raw_totals():
    """A wide bucket and a narrow bucket are directly comparable because both are
    average weekly rates; the veteran's are all ~20 km/week."""
    vet = build_training_history(_veteran_low_volume(), AS_OF)
    for b in vet.timeline:
        assert 17_000 <= b.avg_weekly_distance_m <= 23_000, b.label
        assert b.run_share_pct == 100.0
    new = build_training_history(_newer_high_volume(), AS_OF)
    near = next(b for b in new.timeline if b.label == "1-3 months ago")
    assert near.avg_weekly_distance_m >= 80_000


def test_modality_mix_is_carried():
    """A runner whose recent training is half walks reads as such in the bucket share."""
    facts = []
    for w in range(60):
        facts.append(_Fact(AS_OF - timedelta(days=7 * w), activity_type="Run", distance_m=10_000))
        facts.append(_Fact(AS_OF - timedelta(days=7 * w + 2), activity_type="Walk", distance_m=4_000))
    ctx = build_training_history(facts, AS_OF)
    near = next(b for b in ctx.timeline if b.label == "1-3 months ago")
    assert 40.0 <= near.run_share_pct <= 60.0


# --------------------------------------------------------------------------- #
# Abstention                                                                   #
# --------------------------------------------------------------------------- #


def test_no_history_returns_none():
    assert build_training_history([], AS_OF) is None


def test_too_few_activities_returns_none():
    facts = _weekly(_MIN_ACTIVITIES - 1, lambda w: 10_000)
    assert build_training_history(facts, AS_OF) is None


def test_history_shorter_than_minimum_returns_none():
    # Five weeks of frequent activity: plenty of rows, but < the 42-day floor.
    facts = [
        _Fact(AS_OF - timedelta(days=d), distance_m=8_000)
        for d in range(0, 35, 2)
    ]
    assert build_training_history(facts, AS_OF) is None


def test_thin_history_abstains_on_trajectory_but_still_emits():
    """~4 months of history: the section is present (it has something to say beyond
    the recent window), but the multi-year trajectory cannot resolve."""
    facts = _weekly(18, lambda w: 30_000)  # ~126 days
    ctx = build_training_history(facts, AS_OF)
    assert ctx is not None
    assert ctx.traits.trajectory_direction == "no_norm"
    assert ctx.traits.trajectory_pct is None
    labels = {b.label for b in ctx.timeline}
    assert "1-3 months ago" in labels
    # No bucket beyond the available ~4 months.
    assert "6-12 months ago" not in labels
    assert "1-2 years ago" not in labels


def test_one_year_runner_abstains_on_trajectory():
    """Found on real data: a runner with ~1 year of history must NOT get a
    year-over-year 'up' trend computed against a two-week sliver that happens to
    spill into the prior-year window. The trajectory abstains until the prior year
    holds enough real history."""
    facts = _weekly(53, lambda w: 40_000)  # ~371 days: a full recent year, a sliver before
    ctx = build_training_history(facts, AS_OF)
    assert ctx is not None
    assert ctx.traits.trajectory_direction == "no_norm"
    assert ctx.traits.trajectory_pct is None
    # And no misleading sub-three-week "1-2 years ago" sliver bucket.
    assert all(b.weeks >= 3.0 for b in ctx.timeline)
    assert "1-2 years ago" not in {b.label for b in ctx.timeline}


def test_single_stray_old_activity_does_not_paint_a_deep_bucket():
    """One lone run 6 years back must not create a '5+ years ago' bucket."""
    facts = _weekly(30, lambda w: 25_000)  # ~7 months of real history
    facts.append(_Fact(AS_OF - timedelta(days=2200), distance_m=25_000))  # one stray
    ctx = build_training_history(facts, AS_OF)
    labels = {b.label for b in ctx.timeline}
    assert "5+ years ago" not in labels
