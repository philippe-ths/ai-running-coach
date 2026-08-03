"""Characterisation tests for the analysis *composition* (#701).

The individual analysis stages are unit-tested elsewhere; these tests pin how
`_orchestrator.analyze` composes them — the stage-to-stage intermediate, the
single "is this an interval session" gate, and the three documented ordering
invariants. They are behaviour-preservation nets for the typed-intermediate /
single-gate refactor: each asserts an observable consequence that a reordering
or a re-smeared gate would break.

Invariants under test (mirroring the orchestrator's composition contract):
  1. probe-before-classify: the interval-structure probe runs BEFORE
     classification and feeds the classifier's structure axis.
  2. structure-axis-gates-KPIs: interval_structure is kept only when the
     structure axis resolved to "intervals"; that one gated value drives
     interval matching, KPIs, and the interval confidence reasons.
  3. commit-before-baseline: the runner-baseline recompute runs AFTER the
     DerivedMetric commit, so the just-analysed row is visible to it.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.models import Activity, User, UserProfile, DerivedMetric
from app.models.activity_stream import ActivityStream
from app.services.analysis import analyze
from app.services.analysis import _orchestrator
from app.services.analysis import baseline as baseline_mod
from app.services.analysis.composition import IntervalSession


BASE_DATE = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)


def _seed_run(db, *, with_streams=True, avg_hr=160.0, user_intent=None):
    """Seed one analysable run (and its user/profile). Returns the Activity."""
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"{user_id}@example.com"))
    db.add(
        UserProfile(
            user_id=user_id,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=40,
            max_hr=190,
            max_hr_source="user_entered",
        )
    )
    db.flush()

    activity = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=int(uuid.uuid4().int % (10**12)),
        name="Test Run",
        type="Run",
        start_date=BASE_DATE,
        distance_m=10000,
        moving_time_s=3000,
        elapsed_time_s=3100,
        elev_gain_m=50.0,
        avg_hr=avg_hr,
        max_hr=175.0,
        avg_cadence=170.0,
        average_speed_mps=3.33,
        user_intent=user_intent,
    )
    db.add(activity)
    db.flush()

    if with_streams:
        n = 3000
        db.add(ActivityStream(activity_id=activity.id, stream_type="heartrate", data=[int(avg_hr)] * n))
        db.add(ActivityStream(activity_id=activity.id, stream_type="velocity_smooth", data=[3.3] * n))
        db.add(ActivityStream(activity_id=activity.id, stream_type="time", data=list(range(n))))
        db.add(ActivityStream(activity_id=activity.id, stream_type="distance", data=[i * 3.3 for i in range(n)]))

    db.commit()
    return activity


# A recorded-laps interval structure the probe can return, with a
# match-mismatch workout so the interval-only confidence reasons are visible
# only when the session gate is open.
_LAP_STRUCTURE = {
    "source": "recorded_laps",
    "summary": {"total_work_time_s": 600, "num_reps": 4},
    "warmup_duration_s": 120,
    "reps": [{"duration_s": 150}, {"duration_s": 150}, {"duration_s": 150}, {"duration_s": 150}],
}


# --- Invariant 1: probe-before-classify (the probe feeds the classifier) ---

def test_probe_result_is_fed_to_classifier(db, monkeypatch):
    """The classifier receives has_interval_structure=True exactly when the
    probe found a structure — proving the probe ran first and fed it."""
    activity = _seed_run(db)

    captured = {}
    real_classify = _orchestrator.classify_activity

    def spy_classify(*args, **kwargs):
        captured["has_interval_structure"] = kwargs.get("has_interval_structure")
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(_orchestrator, "classify_activity", spy_classify)
    monkeypatch.setattr(
        _orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE
    )
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    analyze(db, activity.id)

    assert captured["has_interval_structure"] is True


def test_no_probe_structure_is_fed_as_false(db, monkeypatch):
    activity = _seed_run(db)

    captured = {}
    real_classify = _orchestrator.classify_activity

    def spy_classify(*args, **kwargs):
        captured["has_interval_structure"] = kwargs.get("has_interval_structure")
        return real_classify(*args, **kwargs)

    monkeypatch.setattr(_orchestrator, "classify_activity", spy_classify)
    monkeypatch.setattr(_orchestrator, "detect_intervals_from_laps", lambda *a, **k: None)
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    analyze(db, activity.id)

    assert captured["has_interval_structure"] is False


# --- Invariant 2: the structure axis gates interval KPIs + confidence ---

def test_probe_structure_gated_out_when_axis_not_intervals(db, monkeypatch):
    """A probe can find a structure, but if the classifier does not resolve the
    structure axis to 'intervals' the gate closes: interval_structure /
    interval_kpis are dropped and no interval-only confidence reason leaks."""
    activity = _seed_run(db)

    # Probe DOES find a (mismatching) structure...
    monkeypatch.setattr(_orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE)
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    # ...but the classifier resolves structure to continuous.
    real_classify = _orchestrator.classify_activity

    def force_continuous(*args, **kwargs):
        c = real_classify(*args, **kwargs)
        c.structure = "continuous"
        return c

    monkeypatch.setattr(_orchestrator, "classify_activity", force_continuous)

    dm = analyze(db, activity.id)

    assert dm.structure == "continuous"
    assert dm.interval_structure is None
    assert dm.interval_kpis is None
    # The interval-only confidence reasons must not leak onto a non-interval run.
    for reason in ("no_intervals_detected", "interval_structure_mismatch",
                   "work_time_implausibly_high", "high_rep_distance_variability"):
        assert reason not in (dm.confidence_reasons or [])


def test_interval_session_keeps_structure_and_kpis(db, monkeypatch):
    """When the axis resolves to intervals the gate is open: the probed
    structure is kept and interval KPIs are computed from it."""
    activity = _seed_run(db)

    monkeypatch.setattr(_orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE)
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    real_classify = _orchestrator.classify_activity

    def force_intervals(*args, **kwargs):
        c = real_classify(*args, **kwargs)
        c.structure = "intervals"
        return c

    monkeypatch.setattr(_orchestrator, "classify_activity", force_intervals)

    dm = analyze(db, activity.id)

    assert dm.structure == "intervals"
    assert dm.interval_structure == _LAP_STRUCTURE
    assert dm.interval_kpis is not None


def _spy_on_confidence(monkeypatch):
    """Capture the arguments `analyze` hands to compute_confidence, call through."""
    captured = {}
    real = _orchestrator.compute_confidence

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(_orchestrator, "compute_confidence", spy)
    return captured


def test_confidence_consumes_the_gates_own_session(db, monkeypatch):
    """#739: compute_confidence receives the single gate's `IntervalSession`
    itself, not a separately-derived structure, so the two cannot drift into
    deciding "is this an interval session" independently."""
    activity = _seed_run(db)

    monkeypatch.setattr(_orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE)
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    real_classify = _orchestrator.classify_activity

    def force_intervals(*args, **kwargs):
        c = real_classify(*args, **kwargs)
        c.structure = "intervals"
        return c

    monkeypatch.setattr(_orchestrator, "classify_activity", force_intervals)
    captured = _spy_on_confidence(monkeypatch)

    dm = analyze(db, activity.id)

    session = captured["interval_session"]
    assert isinstance(session, IntervalSession)
    assert session.is_session is True
    # The very object the probe produced reaches the confidence stage (identity,
    # not just equality), so there is one structure in play and not two. The
    # persisted column compares by value: a JSON column re-serialises on commit.
    assert session.structure is _LAP_STRUCTURE
    assert dm.interval_structure == _LAP_STRUCTURE


def test_confidence_sees_a_closed_gate_as_a_structureless_session(db, monkeypatch):
    """The gated-out case reaches compute_confidence as an IntervalSession with
    no structure, rather than as a bare None the function could interpret."""
    activity = _seed_run(db)

    monkeypatch.setattr(_orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE)
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    real_classify = _orchestrator.classify_activity

    def force_continuous(*args, **kwargs):
        c = real_classify(*args, **kwargs)
        c.structure = "continuous"
        return c

    monkeypatch.setattr(_orchestrator, "classify_activity", force_continuous)
    captured = _spy_on_confidence(monkeypatch)

    analyze(db, activity.id)

    session = captured["interval_session"]
    assert isinstance(session, IntervalSession)
    assert session.is_session is False
    assert session.structure is None


# --- Invariant 3: commit-before-baseline ---

def test_baseline_recompute_runs_after_derivedmetric_commit(db, monkeypatch):
    """When the runner-baseline recompute runs, the just-analysed activity's
    DerivedMetric row is already committed and visible."""
    activity = _seed_run(db)

    seen = {}
    real_recompute = baseline_mod.recompute_runner_baseline

    def spy_recompute(session, user_id):
        row = (
            session.query(DerivedMetric)
            .filter(DerivedMetric.activity_id == activity.id)
            .first()
        )
        seen["dm_visible"] = row is not None
        return real_recompute(session, user_id)

    monkeypatch.setattr(baseline_mod, "recompute_runner_baseline", spy_recompute)

    analyze(db, activity.id)

    assert seen.get("dm_visible") is True


def test_skip_baseline_defers_recompute(db, monkeypatch):
    """skip_baseline=True must not call the baseline recompute (the bulk-caller
    contract), leaving the composition otherwise identical."""
    activity = _seed_run(db)

    called = {"n": 0}

    def spy_recompute(session, user_id):
        called["n"] += 1

    monkeypatch.setattr(baseline_mod, "recompute_runner_baseline", spy_recompute)

    analyze(db, activity.id, skip_baseline=True)

    assert called["n"] == 0
