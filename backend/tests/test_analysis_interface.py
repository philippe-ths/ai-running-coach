"""
Characterisation test for the analysis pipeline.

Pins the current end-to-end output of `analyze` against a JSON snapshot
so any behaviour drift during the M1 refactor surfaces immediately. On the first
run (or when intentionally updating the snapshot), set `UPDATE_SNAPSHOTS=1` to
write the file. Without that, the test reads the snapshot and asserts equality.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models import Activity, CheckIn, User, UserProfile
from app.models.activity_stream import ActivityStream
from app.services.analysis import analyze


SNAPSHOT_DIR = Path(__file__).parent / "snapshots" / "analysis"
SNAPSHOT_FILE = SNAPSHOT_DIR / "process_activity_default_fixture.json"

# Stable identifiers so the snapshot is reproducible across runs.
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACTIVITY_TARGET_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
ACTIVITY_EASY_ID = uuid.UUID("00000000-0000-0000-0000-000000000011")
ACTIVITY_INTERVAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000012")
ACTIVITY_LONG_ID = uuid.UUID("00000000-0000-0000-0000-000000000013")
CHECKIN_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")

BASE_DATE = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)


def _seed_fixture(db):
    """Seed a representative scenario: target activity with streams, a history
    that exercises the 7-day training context, a check-in, and a calibrated
    profile."""
    db.add(User(id=USER_ID, email="snapshot@example.com"))
    db.add(
        UserProfile(
            user_id=USER_ID,
            goal_type="half",
            experience_level="intermediate",
            weekly_days_available=4,
            current_weekly_km=40,
            max_hr=190,
            max_hr_source="user_entered",
        )
    )
    db.flush()

    def _activity(aid, name, start, **kwargs):
        return Activity(
            id=aid,
            user_id=USER_ID,
            strava_activity_id=int(aid.int % (10**12)),
            name=name,
            type="Run",
            start_date=start,
            distance_m=kwargs.get("distance_m", 10000),
            moving_time_s=kwargs.get("moving_time_s", 3000),
            elapsed_time_s=kwargs.get("elapsed_time_s", 3100),
            elev_gain_m=kwargs.get("elev_gain_m", 50.0),
            avg_hr=kwargs.get("avg_hr", 150.0),
            max_hr=kwargs.get("max_hr", 175.0),
            avg_cadence=kwargs.get("avg_cadence", 170.0),
            average_speed_mps=kwargs.get("average_speed_mps", 3.33),
            user_intent=kwargs.get("user_intent"),
        )

    db.add(_activity(ACTIVITY_EASY_ID, "Easy Recovery", BASE_DATE - timedelta(days=1)))
    db.add(
        _activity(
            ACTIVITY_INTERVAL_ID,
            "Tuesday Intervals 6x800",
            BASE_DATE - timedelta(days=3),
            moving_time_s=2400,
            avg_hr=170.0,
            user_intent="Intervals",
        )
    )
    db.add(
        _activity(
            ACTIVITY_LONG_ID,
            "Sunday Long",
            BASE_DATE - timedelta(days=5),
            distance_m=20000,
            moving_time_s=6600,
        )
    )

    target = _activity(
        ACTIVITY_TARGET_ID,
        "Saturday Tempo",
        BASE_DATE,
        moving_time_s=2700,
        avg_hr=165.0,
        max_hr=180.0,
        user_intent="Tempo",
    )
    db.add(target)
    db.flush()

    # Lightweight streams: one constant-HR run, no GPS. Exercises HR-zone and
    # efficiency code paths without producing time-dependent stream noise.
    n = 2700
    db.add(
        ActivityStream(
            activity_id=target.id, stream_type="heartrate", data=[165] * n
        )
    )
    db.add(
        ActivityStream(
            activity_id=target.id,
            stream_type="velocity_smooth",
            data=[3.3] * n,
        )
    )
    db.add(
        ActivityStream(
            activity_id=target.id,
            stream_type="time",
            data=list(range(n)),
        )
    )
    db.add(
        ActivityStream(
            activity_id=target.id,
            stream_type="distance",
            data=[i * 3.3 for i in range(n)],
        )
    )

    db.add(
        CheckIn(
            id=CHECKIN_ID,
            activity_id=target.id,
            rpe=7,
            pain_score=2,
            pain_location=None,
            sleep_quality=3,
            notes=None,
        )
    )

    # Run the history activities through processing too, so DerivedMetric rows
    # exist for the load-spike history lookup the orchestrator does.
    db.commit()
    for aid in [ACTIVITY_EASY_ID, ACTIVITY_INTERVAL_ID, ACTIVITY_LONG_ID]:
        analyze(db, aid)

    return target


def _serialize_dm(dm) -> dict:
    """Capture the analytical fields of a DerivedMetric. Excludes non-deterministic
    columns (primary key, FK, timestamps)."""
    return {
        "effort": dm.effort,
        "duration_class": dm.duration_class,
        "structure": dm.structure,
        "is_hilly": dm.is_hilly,
        "is_race": dm.is_race,
        "effort_score": dm.effort_score,
        "pace_variability": dm.pace_variability,
        "hr_drift": dm.hr_drift,
        "time_in_zones": dm.time_in_zones,
        "stops_analysis": dm.stops_analysis,
        "efficiency_analysis": dm.efficiency_analysis,
        "flags": dm.flags,
        "confidence": dm.confidence,
        "confidence_reasons": dm.confidence_reasons,
        "interval_structure": dm.interval_structure,
        "workout_match": dm.workout_match,
        "interval_kpis": dm.interval_kpis,
        "risk_level": dm.risk_level,
        "risk_score": dm.risk_score,
        "risk_reasons": dm.risk_reasons,
        "training_context": dm.training_context,
    }


def test_process_activity_matches_snapshot(db):
    target = _seed_fixture(db)

    dm = analyze(db, target.id)
    assert dm is not None, "analyze returned None"

    actual = _serialize_dm(dm)
    actual_json = json.dumps(actual, sort_keys=True, indent=2, default=str)

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not SNAPSHOT_FILE.exists():
        SNAPSHOT_FILE.write_text(actual_json + "\n")
        pytest.skip(
            f"Snapshot written to {SNAPSHOT_FILE.relative_to(Path.cwd())}. "
            "Commit it and re-run without UPDATE_SNAPSHOTS to verify."
        )

    expected_json = SNAPSHOT_FILE.read_text().rstrip("\n")
    assert actual_json == expected_json, (
        "DerivedMetric output drifted from snapshot. If the change is intentional, "
        "re-run with UPDATE_SNAPSHOTS=1 to refresh."
    )
