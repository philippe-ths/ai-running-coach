"""A2b: working context as an assembled view (B baseline + focus payload).

Pins the new assembly seam in context.py:
  - build_focus_payload pulls the SUBJECT activity's own detail, for any subject
    (not just the current run) — the memory half of subject resolution;
  - deep=True pulls the consolidated stream view via the retrieval seam, deep on
    demand; the default path leaves it None and never loads the deferred column
    (A2a leanness);
  - the working context splits into B baseline (relationship slice) + focus
    (subject detail), and flattens to the same CoachContextPack.

The byte-identical-emitted-pack guarantee itself lives in test_coach_context_pack.py
(round-trip + fingerprint); this file pins the new structure and the leanness.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect as sa_inspect

from app.models import Activity, DerivedMetric, User
from app.services.coach.context import (
    assemble_working_context,
    build_context_pack,
    build_focus_payload,
)


def _user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"wc_{uid}@example.com"))
    db.flush()
    return uid


def _activity(db, user_id, *, day: int, name: str) -> Activity:
    a = Activity(
        id=uuid.uuid4(),
        user_id=user_id,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name=name,
        type="Run",
        start_date=datetime(2026, 5, day, 8, 0, tzinfo=timezone.utc),
        distance_m=10000,
        moving_time_s=3000,
        elapsed_time_s=3050,
        avg_hr=150.0,
        max_hr=175.0,
        avg_cadence=170.0,
        elev_gain_m=40.0,
        average_speed_mps=3.3,
        raw_summary={},
    )
    db.add(a)
    db.flush()
    return a


def _metric(db, activity, *, effort: str, stream_view=None) -> DerivedMetric:
    dm = DerivedMetric(
        id=uuid.uuid4(),
        activity_id=activity.id,
        effort=effort,
        duration_class="standard",
        structure="continuous",
        is_hilly=False,
        is_race=False,
        effort_score=5.0,
        confidence="high",
        flags=[],
        confidence_reasons=[],
        stream_view=stream_view,
    )
    db.add(dm)
    db.flush()
    return dm


def test_build_focus_payload_targets_the_given_subject(db):
    """The focus payload reflects the SUBJECT activity, not some default/current
    run — the on-demand pull-a-specific-activity capability."""
    user_id = _user(db)
    anchor = _activity(db, user_id, day=1, name="Anchor easy run")
    _metric(db, anchor, effort="easy")
    subject = _activity(db, user_id, day=5, name="Subject tempo run")
    _metric(db, subject, effort="tempo")
    db.refresh(anchor)
    db.refresh(subject)

    focus = build_focus_payload(db, subject)

    assert focus.activity.name == "Subject tempo run"
    assert focus.metrics.effort == "tempo"  # subject's, not the anchor's "easy"


def test_focus_payload_deep_pulls_stream_view_default_omits_it(db):
    """deep=True pulls the consolidated stream view through the retrieval seam;
    the default (deep=False) leaves it None."""
    user_id = _user(db)
    subject = _activity(db, user_id, day=1, name="Run")
    view = {"n_points": 2, "source_n": 4, "time_s": [0, 2], "hr": [150, 152],
            "pace_s_per_km": [303, 300], "grade_pct": [1.0, 1.1], "cadence_spm": [170, 171]}
    _metric(db, subject, effort="easy", stream_view=view)
    db.refresh(subject)

    assert build_focus_payload(db, subject).stream_view is None
    assert build_focus_payload(db, subject, deep=True).stream_view == view


def test_default_context_pack_does_not_load_deferred_stream_view(db):
    """The default post-activity pack must NOT load the deferred stream_view
    column — that is the A2a leanness the pull model protects. Building the pack
    leaves the column unloaded on the metrics row."""
    user_id = _user(db)
    activity = _activity(db, user_id, day=1, name="Run")
    _metric(db, activity, effort="easy",
            stream_view={"n_points": 1, "source_n": 1, "time_s": [0],
                         "hr": [150], "pace_s_per_km": [300],
                         "grade_pct": [0.0], "cadence_spm": [170]})
    db.flush()
    # Expire so the metrics row reloads through the normal (deferred) path rather
    # than serving the in-memory object that still has stream_view set.
    db.expire_all()

    activity = db.query(Activity).filter(Activity.id == activity.id).first()
    build_context_pack(db, activity)

    assert "stream_view" in sa_inspect(activity.metrics).unloaded


def test_working_context_splits_baseline_and_focus(db):
    """assemble_working_context separates the relationship slice (B baseline) from
    the subject's detail (focus), and the focus carries the measured facts."""
    user_id = _user(db)
    activity = _activity(db, user_id, day=1, name="Run")
    _metric(db, activity, effort="tempo")
    db.refresh(activity)

    wc = assemble_working_context(db, activity)

    # Focus owns the subject detail; B baseline owns the relationship facts.
    assert wc.focus.metrics.effort == "tempo"
    assert wc.focus.activity.name == "Run"
    assert wc.b_baseline.safety_rules.never_diagnose is True
    assert hasattr(wc.b_baseline, "preference_profile")
    # The flattened pack draws its sections from the two halves.
    pack = build_context_pack(db, activity)
    assert pack.metrics == wc.focus.metrics
    assert pack.safety_rules == wc.b_baseline.safety_rules
