"""P3 Training Load — the read-time EWMA fitness/fatigue/form readiness model.

Oracle (aiw-ground-truth, New modality): the expected fitness/fatigue/form values
are hand-computed from the EWMA recurrence in the formula itself, not from a prior
run of the code. The smoothing factors are:

    a_ctl = 1 - exp(-1/42) = 0.0235284   (fitness, 42-day time constant)
    a_atl = 1 - exp(-1/7)  = 0.1331221   (fatigue,  7-day time constant)

and the recurrence (seeded at 0) is `x += a * (load - x)` applied oldest-first.
See inline comments for each hand-computed series.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import User, Activity, DerivedMetric
from app.services.readiness import (
    FITNESS_TC_DAYS,
    FATIGUE_TC_DAYS,
    READINESS_WINDOW_DAYS,
    MIN_HISTORY_DAYS_FOR_READINESS,
    MIN_ACTIVITIES_FOR_READINESS,
    ReadinessModel,
    compute_readiness,
    condition_for,
    trend_for,
    build_readiness,
)


# ---------------------------------------------------------------------------
# Pure EWMA compute (the math oracle)
# ---------------------------------------------------------------------------

def test_constant_load_converges_to_steady_state():
    # A long run of constant daily load L: both EWMAs converge to L, so form -> 0
    # (CTL = L(1-(1-a)^n); for n=400, (1-a_ctl)^400 = exp(-400/42) ~ 7e-5, so CTL ~ L).
    model = compute_readiness(
        [10.0] * 400, as_of=_d(400), history_span_days=399, sample_count=400
    )
    assert model.fitness == pytest.approx(10.0, abs=0.1)
    assert model.fatigue == pytest.approx(10.0, abs=0.1)
    assert model.form == pytest.approx(0.0, abs=0.1)
    assert model.condition == "balanced"   # form/fitness ~ 0 -> balanced
    assert model.trend == "steady"          # flat CTL -> ~0 ramp
    assert model.warming_up is False


def test_hard_day_then_rest_exact_hand_computation():
    # Series [100, 0, 0], seeded 0:
    #   ctl: 2.35284 -> 2.29748 -> 2.24340     (round 1 -> 2.2)
    #   atl: 13.3122 -> 11.5410 -> 10.0044     (round 1 -> 10.0)
    #   form = 2.24340 - 10.0044 = -7.761      (round 1 -> -7.8)
    model = compute_readiness(
        [100.0, 0.0, 0.0], as_of=_d(3), history_span_days=2, sample_count=1
    )
    assert model.fitness == 2.2
    assert model.fatigue == 10.0
    assert model.form == -7.8
    assert model.warming_up is True            # span 2 < 42 and count 1 < 4
    assert model.condition == "building_baseline"


def test_warming_up_thresholds():
    # Enough span AND count -> not warming.
    assert compute_readiness([5.0] * 100, as_of=_d(100),
                             history_span_days=99, sample_count=10).warming_up is False
    # Span below the chronic floor -> warming.
    assert compute_readiness([5.0] * 100, as_of=_d(100),
                             history_span_days=30, sample_count=10).warming_up is True
    # Too few activities -> warming.
    assert compute_readiness([5.0] * 100, as_of=_d(100),
                             history_span_days=99, sample_count=3).warming_up is True


def test_empty_series_is_safe():
    model = compute_readiness([], as_of=_d(1), history_span_days=0, sample_count=0)
    assert model.fitness == 0.0
    assert model.fatigue == 0.0
    assert model.form == 0.0
    assert model.warming_up is True
    assert model.condition == "building_baseline"


# ---------------------------------------------------------------------------
# Condition + trend band classifiers (form/ramp normalised by fitness)
# ---------------------------------------------------------------------------

def test_condition_bands():
    # ratio = form / fitness. Bands: >=0.05 fresh, [-0.20,0.05) balanced,
    # [-0.40,-0.20) fatigued, < -0.40 overreaching.
    assert condition_for(10.0, 100.0, warming_up=False) == "fresh"      # 0.10
    assert condition_for(5.0, 100.0, warming_up=False) == "fresh"       # 0.05 boundary
    assert condition_for(0.0, 100.0, warming_up=False) == "balanced"    # 0.0
    assert condition_for(-20.0, 100.0, warming_up=False) == "balanced"  # -0.20 boundary
    assert condition_for(-25.0, 100.0, warming_up=False) == "fatigued"  # -0.25
    assert condition_for(-40.0, 100.0, warming_up=False) == "fatigued"  # -0.40 boundary
    assert condition_for(-50.0, 100.0, warming_up=False) == "overreaching"


def test_condition_warming_up_and_zero_fitness_abstain():
    assert condition_for(-50.0, 100.0, warming_up=True) == "building_baseline"
    assert condition_for(10.0, 0.0, warming_up=False) == "building_baseline"


def test_trend_bands():
    # ratio = ramp / fitness. >=0.05 building (>=0.15 aggressive), <=-0.05 detraining.
    assert trend_for(10.0, 100.0) == ("building", False)   # 0.10
    assert trend_for(15.0, 100.0) == ("building", True)    # 0.15 aggressive
    assert trend_for(5.0, 100.0) == ("building", False)    # 0.05 boundary
    assert trend_for(0.0, 100.0) == ("steady", False)
    assert trend_for(-5.0, 100.0) == ("detraining", False) # -0.05 boundary
    assert trend_for(0.0, 0.0) == ("steady", False)        # zero fitness -> steady


# ---------------------------------------------------------------------------
# build_readiness — DB read (windowed, as-of, guarded)
# ---------------------------------------------------------------------------

def _seed_user(db):
    user = User(email=f"u-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_activity(db, user, when, effort, *, idx=0):
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(when.timestamp()) + idx,
        start_date=when,
        type="Run",
        name="Run",
        distance_m=5000,
        moving_time_s=1500,
        elapsed_time_s=1500,
        elev_gain_m=0.0,
        avg_hr=150,
        average_speed_mps=3.0,
        raw_summary={},
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    dm = DerivedMetric(activity_id=act.id, effort_score=effort, confidence="high")
    db.add(dm)
    db.commit()
    return act


_BASE = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)


def test_build_readiness_basic(db):
    user = _seed_user(db)
    # 30 runs, every other day over 60 days, ending at as_of.
    last = None
    for i in range(30):
        last = _BASE + timedelta(days=2 * i)
        _seed_activity(db, user, last, 50.0)
    model = build_readiness(db, user.id, last)
    assert model is not None
    assert model.sample_count == 30
    assert model.history_span_days == 58       # day 0 .. day 58
    assert model.warming_up is False           # span 58 >= 42, count 30 >= 4
    assert model.fitness > 0
    assert model.fatigue > 0


def test_build_readiness_windows_out_old_activities(db):
    user = _seed_user(db)
    # A huge load 200 days before as_of: outside the 182d series window, so it must
    # not inflate fitness (the #167 / AC6 windowing). A few recent small runs set
    # the actual recent fitness.
    as_of = _BASE + timedelta(days=200)
    _seed_activity(db, user, _BASE, 9999.0)                 # 200d old -> windowed out
    for i in range(5):
        _seed_activity(db, user, as_of - timedelta(days=i), 20.0, idx=i + 1)
    model = build_readiness(db, user.id, as_of)
    assert model is not None
    # history spans 200 days (the old run counts for span), so not warming up...
    assert model.history_span_days == 200
    assert model.warming_up is False
    # ...but the 9999 load is excluded from the series, so fitness stays small.
    assert model.fitness < 100


def test_build_readiness_sums_same_day_activities(db):
    user = _seed_user(db)
    # Two activities on the only day of history (30 + 40 = 70). With one day seeded
    # at 0: fitness = a_ctl * 70 = 0.0235284 * 70 = 1.6470 -> round 1.6.
    _seed_activity(db, user, _BASE, 30.0, idx=0)
    _seed_activity(db, user, _BASE.replace(hour=18), 40.0, idx=1)
    model = build_readiness(db, user.id, _BASE.replace(hour=20))
    assert model is not None
    assert model.sample_count == 2
    assert model.fitness == 1.6              # confirms the day summed to 70, not 30/40


def test_build_readiness_as_of_excludes_future_activities(db):
    user = _seed_user(db)
    for i in range(10):
        _seed_activity(db, user, _BASE + timedelta(days=i), 25.0, idx=i)
    # As of day 4 (the 5th run): days 5..9 are in the future and excluded.
    as_of = _BASE + timedelta(days=4, hours=1)
    model = build_readiness(db, user.id, as_of)
    assert model is not None
    assert model.sample_count == 5           # days 0..4 only


def test_build_readiness_none_when_no_history(db):
    user = _seed_user(db)
    assert build_readiness(db, user.id, _BASE) is None


def test_build_readiness_skips_activities_without_metrics(db):
    user = _seed_user(db)
    _seed_activity(db, user, _BASE, 50.0, idx=0)
    # An un-analyzed activity (no DerivedMetric) carries no load and is dropped by
    # the inner join, so it is not counted as a load sample.
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(_BASE.timestamp()) + 99,
        start_date=_BASE + timedelta(days=1),
        type="Run", name="Run", distance_m=5000, moving_time_s=1500,
        elapsed_time_s=1500, elev_gain_m=0.0, avg_hr=150, average_speed_mps=3.0,
        raw_summary={},
    )
    db.add(act)
    db.commit()
    model = build_readiness(db, user.id, _BASE + timedelta(days=1, hours=2))
    assert model is not None
    assert model.sample_count == 1           # the metric-less activity is not counted


def _d(n):
    """A throwaway as_of date n days into an arbitrary epoch (pure tests don't read it)."""
    return (datetime(2026, 1, 1) + timedelta(days=n)).date()
