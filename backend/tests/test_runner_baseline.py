"""
M2 — RunnerBaseline trend substrate.

Oracle: all expected values for the pure functions are hand-computed from the
formulas in the design brief (least-squares slope, percent change of the fitted
endpoints, medians). See inline comments for the arithmetic.
"""

import uuid
from datetime import datetime, timedelta, timezone
from statistics import median

import pytest

from app.models import User, Activity, DerivedMetric, RunnerBaseline
from app.services.analysis.baseline import (
    MIN_SAMPLES_FOR_TREND,
    temp_band,
    efficiency_factor,
    bucket_key,
    compute_trend,
    compute_bucketed_trends,
    compute_runner_baseline_scalars,
    recompute_runner_baseline,
)


# ---------------------------------------------------------------------------
# temp_band boundaries
# ---------------------------------------------------------------------------

def test_temp_band_boundaries():
    assert temp_band(None) == "unknown"
    assert temp_band(9.9) == "cold"     # < 10
    assert temp_band(10) == "cool"      # 10 <= x < 20
    assert temp_band(19.9) == "cool"
    assert temp_band(20) == "mild"      # 20 <= x < 25
    assert temp_band(24.9) == "mild"
    assert temp_band(25) == "hot"       # >= 25
    assert temp_band(30) == "hot"


# ---------------------------------------------------------------------------
# efficiency_factor
# ---------------------------------------------------------------------------

def test_efficiency_factor_prefers_efficiency_analysis_average():
    # efficiency_analysis.average is present and positive -> used verbatim.
    ef = efficiency_factor(avg_speed_mps=3.0, avg_hr=150.0,
                           efficiency_analysis={"average": 1.23, "unit": "m/min/bpm"})
    assert ef == 1.23


def test_efficiency_factor_falls_back_to_speed_times_60_over_hr():
    # No efficiency_analysis -> compute speed*60/hr.
    # 3.0 * 60 / 150 = 180 / 150 = 1.2 exactly.
    ef = efficiency_factor(avg_speed_mps=3.0, avg_hr=150.0, efficiency_analysis=None)
    assert ef == 1.2


def test_efficiency_factor_fallback_rounds_to_3dp():
    # 2.5 * 60 / 140 = 150 / 140 = 1.0714285... -> round 3dp = 1.071
    ef = efficiency_factor(avg_speed_mps=2.5, avg_hr=140.0, efficiency_analysis=None)
    assert ef == 1.071


def test_efficiency_factor_none_when_no_hr():
    assert efficiency_factor(avg_speed_mps=3.0, avg_hr=None, efficiency_analysis=None) is None
    assert efficiency_factor(avg_speed_mps=3.0, avg_hr=0, efficiency_analysis=None) is None


def test_efficiency_factor_ignores_non_positive_analysis_average():
    # average is zero/invalid -> fall back to speed*60/hr (1.2).
    ef = efficiency_factor(avg_speed_mps=3.0, avg_hr=150.0,
                           efficiency_analysis={"average": 0})
    assert ef == 1.2


# ---------------------------------------------------------------------------
# bucket_key
# ---------------------------------------------------------------------------

def test_bucket_key_composition():
    assert bucket_key("easy", False, 15) == "easy|flat|cool"
    assert bucket_key("hard", True, 28) == "hard|hilly|hot"
    assert bucket_key(None, False, None) == "unknown|flat|unknown"
    assert bucket_key("tempo", True, 9.9) == "tempo|hilly|cold"


# ---------------------------------------------------------------------------
# compute_trend  (hand-computed least squares)
# ---------------------------------------------------------------------------

def test_compute_trend_rising_ef_is_improving():
    # values = [10, 11, 12, 13], x = [0,1,2,3]
    # x_mean=1.5, y_mean=11.5
    # num = sum((x-xm)(y-ym)) = 2.25+0.25+0.25+2.25 = 5.0
    # den = sum((x-xm)^2)     = 2.25+0.25+0.25+2.25 = 5.0
    # slope = 1.0; intercept = 11.5 - 1.0*1.5 = 10.0
    # fitted_first = 10.0; fitted_last = 13.0
    # magnitude_pct = (13-10)/10*100 = 30.0
    result = compute_trend([10.0, 11.0, 12.0, 13.0], higher_is_better=True)
    assert result["direction"] == "improving"
    assert result["magnitude_pct"] == 30.0
    assert result["slope"] == 1.0
    assert result["n"] == 4


def test_compute_trend_rising_hr_drift_is_declining():
    # values = [2, 4, 6, 8], x = [0,1,2,3]
    # x_mean=1.5, y_mean=5.0
    # num = (-1.5)(-3)+(-0.5)(-1)+(0.5)(1)+(1.5)(3) = 4.5+0.5+0.5+4.5 = 10.0
    # den = 5.0 -> slope = 2.0; intercept = 5.0 - 2.0*1.5 = 2.0
    # fitted_first = 2.0; fitted_last = 8.0
    # magnitude_pct = (8-2)/2*100 = 300.0
    # higher_is_better=False, slope>0 -> invert -> "declining"
    result = compute_trend([2.0, 4.0, 6.0, 8.0], higher_is_better=False)
    assert result["direction"] == "declining"
    assert result["magnitude_pct"] == 300.0
    assert result["slope"] == 2.0
    assert result["n"] == 4


def test_compute_trend_flat_is_stable():
    result = compute_trend([5.0, 5.0, 5.0, 5.0], higher_is_better=True)
    assert result["direction"] == "stable"
    assert result["magnitude_pct"] == 0.0
    assert result["slope"] == 0.0


def test_compute_trend_none_below_two_points():
    assert compute_trend([], higher_is_better=True) is None
    assert compute_trend([5.0], higher_is_better=True) is None


# ---------------------------------------------------------------------------
# compute_bucketed_trends
# ---------------------------------------------------------------------------

def _sample(day, bucket, ef, drift):
    return {"date": datetime(2026, 1, day, tzinfo=timezone.utc),
            "bucket": bucket, "ef": ef, "hr_drift": drift}


def test_bucketed_trends_abstains_below_threshold():
    # 3 samples in one bucket -> below MIN_SAMPLES_FOR_TREND (4) -> abstain.
    samples = [
        _sample(1, "easy|flat|cool", 10.0, 2.0),
        _sample(2, "easy|flat|cool", 11.0, 3.0),
        _sample(3, "easy|flat|cool", 12.0, 4.0),
    ]
    out = compute_bucketed_trends(samples)
    b = out["easy|flat|cool"]
    assert b["sample_count"] == 3
    assert b["abstained"] is True
    assert "efficiency_factor" not in b


def test_bucketed_trends_emits_trend_at_threshold():
    # 4 samples -> at threshold -> compute trends.
    # EF rising [10,11,12,13] -> improving 30.0 ; drift rising [2,4,6,8] -> declining
    samples = [
        _sample(1, "easy|flat|cool", 10.0, 2.0),
        _sample(2, "easy|flat|cool", 11.0, 4.0),
        _sample(3, "easy|flat|cool", 12.0, 6.0),
        _sample(4, "easy|flat|cool", 13.0, 8.0),
    ]
    out = compute_bucketed_trends(samples)
    b = out["easy|flat|cool"]
    assert b["sample_count"] == 4
    assert b["abstained"] is False
    assert b["efficiency_factor"]["direction"] == "improving"
    assert b["efficiency_factor"]["magnitude_pct"] == 30.0
    assert b["hr_drift"]["direction"] == "declining"
    assert b["hr_drift"]["magnitude_pct"] == 300.0


def test_bucketed_trends_keeps_distinct_buckets_separate():
    # A hilly-hot run and a flat-cool run must never merge.
    samples = [
        _sample(1, "easy|flat|cool", 10.0, 2.0),
        _sample(2, "hard|hilly|hot", 5.0, 9.0),
    ]
    out = compute_bucketed_trends(samples)
    assert set(out.keys()) == {"easy|flat|cool", "hard|hilly|hot"}
    assert out["easy|flat|cool"]["sample_count"] == 1
    assert out["hard|hilly|hot"]["sample_count"] == 1


def test_bucketed_trends_orders_by_date_and_skips_none():
    # Insert out of date order; trend must use chronological order.
    # Dates 4,1,3,2 with EF tied to date so chronological EF = [10,20,30,40].
    samples = [
        {"date": datetime(2026, 1, 4, tzinfo=timezone.utc), "bucket": "b", "ef": 40.0, "hr_drift": None},
        {"date": datetime(2026, 1, 1, tzinfo=timezone.utc), "bucket": "b", "ef": 10.0, "hr_drift": 1.0},
        {"date": datetime(2026, 1, 3, tzinfo=timezone.utc), "bucket": "b", "ef": 30.0, "hr_drift": None},
        {"date": datetime(2026, 1, 2, tzinfo=timezone.utc), "bucket": "b", "ef": 20.0, "hr_drift": 2.0},
    ]
    out = compute_bucketed_trends(samples)
    b = out["b"]
    assert b["sample_count"] == 4
    # EF chronological [10,20,30,40]: slope 10, intercept 10, first 10 last 40
    # magnitude_pct = (40-10)/10*100 = 300.0 -> improving
    assert b["efficiency_factor"]["direction"] == "improving"
    assert b["efficiency_factor"]["magnitude_pct"] == 300.0
    # hr_drift only has 2 non-None values [1.0, 2.0] -> a valid 2-point trend
    assert b["hr_drift"]["n"] == 2


# ---------------------------------------------------------------------------
# compute_runner_baseline_scalars
# ---------------------------------------------------------------------------

def _activity(day, *, hr, dist_m, time_s, speed):
    return Activity(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        strava_activity_id=day,
        start_date=datetime(2026, 1, day, tzinfo=timezone.utc),
        type="Run",
        name=f"Run {day}",
        distance_m=dist_m,
        moving_time_s=time_s,
        elapsed_time_s=time_s,
        elev_gain_m=0.0,
        avg_hr=hr,
        average_speed_mps=speed,
        raw_summary={},
    )


def _metric(effort, ef_avg=None):
    return DerivedMetric(
        id=uuid.uuid4(),
        activity_id=uuid.uuid4(),
        effort=effort,
        effort_score=10.0,
        confidence="high",
        is_hilly=False,
        efficiency_analysis=({"average": ef_avg} if ef_avg is not None else None),
    )


def test_scalars_typical_easy_hr_is_median():
    # Three easy runs, HR = 140, 150, 160 -> median 150.
    rows = [
        (_activity(1, hr=140, dist_m=5000, time_s=1500, speed=3.0), _metric("easy")),
        (_activity(2, hr=150, dist_m=5000, time_s=1500, speed=3.0), _metric("easy")),
        (_activity(3, hr=160, dist_m=5000, time_s=1500, speed=3.0), _metric("recovery")),
    ]
    scalars = compute_runner_baseline_scalars(rows)
    assert scalars["typical_easy_hr"] == 150.0
    # pace = time / (dist/1000) = 1500 / 5 = 300 s/km for all -> median 300.
    assert scalars["typical_easy_pace_s_per_km"] == 300.0


def test_scalars_excludes_non_easy_efforts_from_easy_hr():
    rows = [
        (_activity(1, hr=140, dist_m=5000, time_s=1500, speed=3.0), _metric("easy")),
        (_activity(2, hr=180, dist_m=5000, time_s=1200, speed=4.0), _metric("hard")),
    ]
    scalars = compute_runner_baseline_scalars(rows)
    # Only the easy run (HR 140) counts.
    assert scalars["typical_easy_hr"] == 140.0


# ---------------------------------------------------------------------------
# recompute_runner_baseline  (DB-backed)
# ---------------------------------------------------------------------------

def _seed_user(db):
    user = User(email=f"u-{uuid.uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _seed_activity_with_metric(db, user, day, *, hr, effort, ef_avg=None,
                               dist_m=5000, time_s=1500, speed=3.0):
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(datetime(2026, 1, day).timestamp()),
        start_date=datetime(2026, 1, day, tzinfo=timezone.utc),
        type="Run",
        name=f"Run {day}",
        distance_m=dist_m,
        moving_time_s=time_s,
        elapsed_time_s=time_s,
        elev_gain_m=0.0,
        avg_hr=hr,
        average_speed_mps=speed,
        raw_summary={"average_temp": 15.0},
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    dm = DerivedMetric(
        activity_id=act.id,
        effort=effort,
        effort_score=10.0,
        confidence="high",
        is_hilly=False,
        hr_drift=2.0,
        efficiency_analysis=({"average": ef_avg} if ef_avg is not None else None),
    )
    db.add(dm)
    db.commit()
    return act


def test_recompute_persists_baseline_with_abstaining_bucket(db):
    user = _seed_user(db)
    # 3 easy/flat/cool runs (temp 15 -> cool) -> below threshold (4) -> abstain.
    for day, hr in [(1, 140), (2, 150), (3, 160)]:
        _seed_activity_with_metric(db, user, day, hr=hr, effort="easy", ef_avg=1.2)

    row = recompute_runner_baseline(db, user.id)

    assert isinstance(row, RunnerBaseline)
    assert row.user_id == user.id
    assert row.bucketed_trends is not None
    bucket = "easy|flat|cool"
    assert bucket in row.bucketed_trends
    assert row.bucketed_trends[bucket]["sample_count"] == 3
    assert row.bucketed_trends[bucket]["abstained"] is True
    # typical_easy_hr median of [140,150,160] = 150
    assert row.typical_easy_hr == 150.0
    # sample_count = number of eligible (avg_hr-bearing, metric-bearing) acts.
    assert row.sample_count == 3
    assert row.computed_at is not None


def test_recompute_emits_trend_at_threshold_and_is_idempotent(db):
    user = _seed_user(db)
    # 4 easy/flat/cool runs -> at threshold -> trend computed.
    for day, hr in [(1, 140), (2, 145), (3, 150), (4, 155)]:
        _seed_activity_with_metric(db, user, day, hr=hr, effort="easy", ef_avg=1.2)

    row1 = recompute_runner_baseline(db, user.id)
    bucket = "easy|flat|cool"
    assert row1.bucketed_trends[bucket]["sample_count"] == 4
    assert row1.bucketed_trends[bucket]["abstained"] is False

    # Idempotent: re-running upserts the same single row.
    row2 = recompute_runner_baseline(db, user.id)
    assert row2.id == row1.id
    count = db.query(RunnerBaseline).filter(RunnerBaseline.user_id == user.id).count()
    assert count == 1


def test_min_samples_constant_is_four():
    assert MIN_SAMPLES_FOR_TREND == 4


# ---------------------------------------------------------------------------
# Hardening from the adversarial review
# ---------------------------------------------------------------------------

def test_temp_band_non_numeric_degrades_to_unknown():
    """A stray non-numeric average_temp (untyped Strava JSON) must not raise."""
    assert temp_band("warm") == "unknown"
    assert temp_band("15") == "cool"   # numeric string still bands (10<=15<20)
    assert temp_band("20") == "mild"   # 20 is the cool/mild boundary (<20 is cool)


def test_compute_trend_zero_first_value_uses_slope_not_stable():
    """A real slope whose fitted first value is exactly 0 must not read 'stable'."""
    up = compute_trend([0.0, 1.0, 2.0, 3.0], higher_is_better=True)
    assert up["direction"] == "improving"
    # hr_drift rising from 0 is a worsening (lower is better)
    worse = compute_trend([0.0, 1.0, 2.0, 3.0], higher_is_better=False)
    assert worse["direction"] == "declining"


# ---------------------------------------------------------------------------
# #167 — bound the recompute scan to a rolling comparison window
#
# Oracle: the captured behaviour of the existing recompute over the in-window
# samples. The window changes which activities are *included* (out-of-window
# history ages out), never the math over the included samples — so a history
# that fits entirely within the window is byte-for-byte unchanged.
# ---------------------------------------------------------------------------

from app.services.analysis.baseline import BASELINE_COMPARISON_WINDOW_DAYS


def _seed_activity_on(db, user, start_date, *, hr, effort, ef_avg=1.2,
                      dist_m=5000, time_s=1500, speed=3.0, hr_drift=2.0):
    """Seed one analysed activity at an explicit datetime (the day-of-January
    helpers above can't express out-of-window spans)."""
    act = Activity(
        user_id=user.id,
        strava_activity_id=int(start_date.timestamp()),
        start_date=start_date,
        type="Run",
        name="Run",
        distance_m=dist_m,
        moving_time_s=time_s,
        elapsed_time_s=time_s,
        elev_gain_m=0.0,
        avg_hr=hr,
        average_speed_mps=speed,
        raw_summary={"average_temp": 15.0},
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    dm = DerivedMetric(
        activity_id=act.id,
        effort=effort,
        effort_score=10.0,
        confidence="high",
        is_hilly=False,
        hr_drift=hr_drift,
        efficiency_analysis={"average": ef_avg},
    )
    db.add(dm)
    db.commit()
    return act


def test_baseline_comparison_window_constant_is_182_days():
    assert BASELINE_COMPARISON_WINDOW_DAYS == 182


def test_recompute_excludes_activities_older_than_window(db):
    """An activity older than the comparison window (measured back from the
    latest run) does not contribute to the baseline — the rolling-window bound."""
    user = _seed_user(db)
    anchor = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # Recent in-window easy runs: HR 160/150/140 -> median 150.
    for offset, hr in [(0, 160), (10, 150), (20, 140)]:
        _seed_activity_on(db, user, anchor - timedelta(days=offset), hr=hr, effort="easy")
    # An old run well outside the window whose extreme HR WOULD move the median
    # if it were (wrongly) counted.
    _seed_activity_on(
        db, user, anchor - timedelta(days=200), hr=999, effort="easy"
    )

    row = recompute_runner_baseline(db, user.id)

    # Only the 3 in-window runs count.
    assert row.sample_count == 3
    assert row.typical_easy_hr == 150.0


def test_recompute_window_anchors_on_latest_activity_not_now(db):
    """The window is relative to the runner's most recent activity, so a history
    sitting entirely years in the past but clustered within the window still
    counts — it is a rolling window over their data, not a wall-clock cutoff."""
    user = _seed_user(db)
    old_anchor = datetime(2024, 3, 1, tzinfo=timezone.utc)  # years before "now"
    for offset, hr in [(0, 160), (10, 150), (20, 140)]:
        _seed_activity_on(db, user, old_anchor - timedelta(days=offset), hr=hr, effort="easy")

    row = recompute_runner_baseline(db, user.id)

    assert row.sample_count == 3
    assert row.typical_easy_hr == 150.0


def test_recompute_in_window_history_is_unchanged(db):
    """Characterization: when every activity falls within the window, the
    baseline is exactly what the unbounded recompute produced (scalars + the
    emitted trend), proving the window only changes inclusion, not the math."""
    user = _seed_user(db)
    anchor = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # 4 easy/flat/cool runs within the window -> trend emitted (>= MIN_SAMPLES).
    for offset, hr in [(30, 140), (20, 145), (10, 150), (0, 155)]:
        _seed_activity_on(db, user, anchor - timedelta(days=offset), hr=hr, effort="easy")

    row = recompute_runner_baseline(db, user.id)

    bucket = "easy|flat|cool"
    assert row.sample_count == 4
    assert row.bucketed_trends[bucket]["sample_count"] == 4
    assert row.bucketed_trends[bucket]["abstained"] is False
    # median HR of [140,145,150,155] = 147.5
    assert row.typical_easy_hr == 147.5


# ---------------------------------------------------------------------------
# Baseline recompute failure VISIBILITY (#513)
#
# The runner-baseline recompute is best-effort: a failure must never break
# analyze(), so the orchestrator swallows it and analysis continues. But a GENUINE
# internal failure (a malformed stored bucketed_trends, a bad value type) must be
# logged at ERROR and routed to Sentry capture rather than swallowed silently --
# otherwise corruption is indistinguishable from a normal "not enough data"
# abstention (which returns None cleanly without raising) and the baseline freezes
# at its last good value with no symptom. The legitimate thin-data path must stay
# quiet so the new logging does not cry wolf.
# ---------------------------------------------------------------------------

def test_post_commit_baseline_internal_error_is_visible_but_safe(db, monkeypatch, caplog):
    import logging

    from app.services.analysis import _orchestrator

    captured: list[BaseException] = []
    monkeypatch.setattr(
        "app.core.observability.capture_exception",
        lambda exc: captured.append(exc),
    )

    def _boom(_db, _user_id):
        raise ValueError("malformed stored bucketed_trends")

    # Patched on the ORCHESTRATOR namespace (#805): the recompute used to be
    # imported lazily inside `_post_commit_baseline`, so patching its source
    # module intercepted it. It is now a module-level name on the orchestrator —
    # the one seam every composition test uses — so that is where it is patched.
    monkeypatch.setattr(
        "app.services.analysis._orchestrator.recompute_runner_baseline", _boom
    )

    user = _seed_user(db)

    with caplog.at_level(logging.ERROR, logger=_orchestrator.__name__):
        # Best-effort guard: must NOT raise even though the recompute blows up.
        _orchestrator._post_commit_baseline(db, user.id)

    # Visible: logged at ERROR and routed to Sentry capture.
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert "runner baseline recompute failed" in caplog.text
    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)


def test_post_commit_baseline_thin_data_abstention_stays_quiet(db, monkeypatch, caplog):
    import logging

    from app.services.analysis import _orchestrator

    captured: list[BaseException] = []
    monkeypatch.setattr(
        "app.core.observability.capture_exception",
        lambda exc: captured.append(exc),
    )

    # No activities -> recompute_runner_baseline returns None cleanly (abstains).
    user = _seed_user(db)

    with caplog.at_level(logging.ERROR, logger=_orchestrator.__name__):
        _orchestrator._post_commit_baseline(db, user.id)

    # A normal abstention: no ERROR log, no Sentry capture.
    assert not any(r.levelno == logging.ERROR for r in caplog.records)
    assert captured == []
