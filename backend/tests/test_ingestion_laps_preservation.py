"""Recorded laps survive a lap-less re-sync (#170 finding F1).

The detail endpoint (webhook/polling) carries the runner's `laps`; the summary
list endpoint (manual sync, backfill) does not. upsert_activity must not let a
routine lap-less sync clobber laps it already has, or a lap-sourced interval
structure silently downgrades to the stream heuristic on the next "Sync Now".
"""

import uuid

from app.models import User
from app.services.strava_ingestion.ingestion import upsert_activity


_STRAVA_ID = 18823360273


def _detail_raw():
    """A detail-endpoint payload that carries recorded laps."""
    return {
        "id": _STRAVA_ID,
        "name": "Track 10x200",
        "type": "Run",
        "start_date": "2026-05-01T07:00:00Z",
        "distance": 5000,
        "moving_time": 1800,
        "elapsed_time": 2000,
        "total_elevation_gain": 10.0,
        "average_heartrate": 160.0,
        "max_heartrate": 185.0,
        "average_speed": 3.3,
        "laps": [
            {"lap_index": 1, "distance": 200.0, "average_speed": 5.0, "elapsed_time": 40},
            {"lap_index": 2, "distance": 100.0, "average_speed": 2.0, "elapsed_time": 50},
            {"lap_index": 3, "distance": 200.0, "average_speed": 5.0, "elapsed_time": 40},
        ],
    }


def _summary_raw():
    """A summary list-endpoint payload for the same activity: no laps."""
    raw = _detail_raw()
    raw.pop("laps")
    raw["name"] = "Afternoon Run"  # summaries can differ; should still apply
    return raw


def test_lapless_resync_preserves_recorded_laps(db):
    user_id = uuid.UUID("00000000-0000-0000-0000-0000000003aa")
    db.add(User(id=user_id, email="laps-preserve@example.com"))
    db.flush()

    # First ingest via the detail path (laps present).
    upsert_activity(db, _detail_raw(), user_id)
    db.commit()

    # Re-sync via the summary path (no laps).
    activity = upsert_activity(db, _summary_raw(), user_id)
    db.commit()
    db.refresh(activity)

    # Laps preserved; other summary fields still updated.
    assert activity.raw_summary.get("laps"), "recorded laps were clobbered by re-sync"
    assert len(activity.raw_summary["laps"]) == 3
    assert activity.name == "Afternoon Run"


def test_fresh_detail_laps_win_over_preserved(db):
    user_id = uuid.UUID("00000000-0000-0000-0000-0000000003ab")
    db.add(User(id=user_id, email="laps-fresh@example.com"))
    db.flush()

    upsert_activity(db, _detail_raw(), user_id)
    db.commit()

    # A fresh detail re-fetch with different laps is authoritative.
    new_detail = _detail_raw()
    new_detail["laps"] = new_detail["laps"][:1]
    activity = upsert_activity(db, new_detail, user_id)
    db.commit()
    db.refresh(activity)

    assert len(activity.raw_summary["laps"]) == 1
