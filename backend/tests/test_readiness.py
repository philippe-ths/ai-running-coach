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


def _seed_activity(db, user, when, effort, *, idx=0, local=None):
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(when.timestamp()) + idx,
        start_date=when,
        start_date_local=local,   # naive local wall-clock; None => local_start falls back to UTC
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


# ---------------------------------------------------------------------------
# build_readiness — LOCAL-day bucketing (#507)
#
# Readiness must key its daily-load series to the runner's LOCAL calendar day (the
# `Activity.local_start` convention: start_date_local when present, else UTC
# start_date), consistent with the volume vs-norm signal. Otherwise a late-evening
# local run whose UTC instant has rolled to the next day, and a same-local-day double
# session straddling the UTC midnight, split across two days and understate the acute
# (fatigue) load spike the coach reads.
#
# The real callers now pass `activity.local_start` as `as_of`; these tests mirror that.
# ---------------------------------------------------------------------------

def test_build_readiness_buckets_late_evening_run_on_local_day(db):
    # A run at 23:00 local in a UTC-2 zone => UTC instant is 01:00 the NEXT calendar
    # day. Its local day is the EARLIER day; readiness must agree with volume and use
    # the local day. Two such runs on the SAME local day (but different UTC days) must
    # sum into ONE day's load, not split across two.
    user = _seed_user(db)
    local_day = datetime(2026, 6, 1)  # the runner's local calendar day

    # Morning: 09:00 local (UTC 11:00, same UTC day) and evening: 23:00 local (UTC
    # 01:00 the next UTC day). Both share local day 2026-06-01.
    morning_local = local_day.replace(hour=9)
    morning_utc = datetime(2026, 6, 1, 11, 0, tzinfo=timezone.utc)
    evening_local = local_day.replace(hour=23)
    evening_utc = datetime(2026, 6, 2, 1, 0, tzinfo=timezone.utc)  # rolled to next UTC day

    _seed_activity(db, user, morning_utc, 30.0, idx=0, local=morning_local)
    _seed_activity(db, user, evening_utc, 40.0, idx=1, local=evening_local)

    # As-of the runner's local wall-clock end of the evening run (the real call site).
    model = build_readiness(db, user.id, evening_local.replace(hour=23, minute=30))
    assert model is not None
    assert model.sample_count == 2
    # One day of history seeded at 0 with summed load 30+40=70:
    #   fitness = a_ctl * 70 = 0.0235284 * 70 = 1.6470 -> 1.6.
    # If the two runs had split across two UTC days the series would be [70-ish split],
    # giving a different (lower) fatigue spike; 1.6 confirms they summed into ONE day.
    assert model.fitness == 1.6
    assert model.history_span_days == 0   # both runs land on the single local day


def test_build_readiness_double_session_one_acute_spike_not_split(db):
    # The acute-fatigue point of the bug: a same-local-day double session must register
    # as one day's load (a bigger fatigue spike), not two half-loads across two days.
    user = _seed_user(db)
    # Establish a flat baseline of one run/day for 60 prior local days at load 20, in a
    # UTC-5 zone (so evening runs roll to the next UTC day but stay on their local day).
    offset = timedelta(hours=5)  # local = UTC - 5h
    base_local = datetime(2026, 4, 1, 18, 0)  # 18:00 local
    last_local = None
    for i in range(60):
        d_local = base_local + timedelta(days=i)
        d_utc = (d_local + offset).replace(tzinfo=timezone.utc)
        _seed_activity(db, user, d_utc, 20.0, idx=i, local=d_local)
        last_local = d_local

    # The final local day gets a SECOND session in the evening that crosses UTC midnight.
    second_local = last_local.replace(hour=23)          # 23:00 local, same local day
    second_utc = (second_local + offset).replace(tzinfo=timezone.utc)  # 04:00 next UTC day
    _seed_activity(db, user, second_utc, 60.0, idx=999, local=second_local)

    model = build_readiness(db, user.id, second_local.replace(minute=30))
    assert model is not None
    # The final local day carries 20 + 60 = 80 of load summed into ONE series day, so
    # the acute (fatigue) EWMA spikes above the steady ~20 baseline rather than being
    # diluted across two UTC days.
    assert model.fatigue > 25.0
    assert model.sample_count == 61   # 60 baseline + the second session, all counted


def test_build_readiness_dst_transition_day_uses_local_wall_clock(db):
    # On a DST "spring forward" day the local wall-clock day is unambiguous (Strava
    # supplies start_date_local already shifted), so _local_day just takes its date.
    # A run on the DST day must bucket on its local day regardless of the UTC instant.
    user = _seed_user(db)
    # US spring-forward 2026: 2026-03-08, clocks jump 02:00->03:00 (UTC-5 -> UTC-4).
    # An evening run at 22:00 local on the DST day, UTC-4 after the shift => UTC 02:00
    # on 2026-03-09 (rolled to the next UTC day).
    dst_local = datetime(2026, 3, 8, 22, 0)
    dst_utc = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)
    # A few prior days to give a non-trivial series, all UTC-4 evenings.
    for i in range(1, 6):
        prior_local = dst_local - timedelta(days=i)
        prior_utc = dst_utc - timedelta(days=i)
        _seed_activity(db, user, prior_utc, 25.0, idx=i, local=prior_local)
    _seed_activity(db, user, dst_utc, 25.0, idx=0, local=dst_local)

    model = build_readiness(db, user.id, dst_local.replace(minute=30))
    assert model is not None
    assert model.sample_count == 6
    # History spans the 5 prior local days to the DST local day inclusive -> 5 days.
    # (If the DST run were bucketed on its UTC day 2026-03-09 it would read 6.)
    assert model.history_span_days == 5


def _d(n):
    """A throwaway as_of date n days into an arbitrary epoch (pure tests don't read it)."""
    return (datetime(2026, 1, 1) + timedelta(days=n)).date()
