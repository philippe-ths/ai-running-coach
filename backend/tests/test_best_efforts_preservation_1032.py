"""Strava best efforts survive a summary-only re-sync (#1032).

The detail endpoint (webhook, self-heal) carries `best_efforts` and their PB ranks;
the summary list endpoint (manual "Sync Now", the import) does not. Without the
preservation rule a routine sync silently erased a run's personal bests, and the
coach's notable-activity read lost them with it. Same rule as recorded laps (#170).

Payloads are synthetic test setup in Strava's real shape (trust level 5).
"""

import uuid

from app.models import User
from app.services.strava_ingestion.ingestion import upsert_activity

_STRAVA_ID = 18823360991


def _detail_raw():
    return {
        "id": _STRAVA_ID,
        "name": "Morning Run",
        "type": "Run",
        "start_date": "2026-05-03T08:00:00Z",
        "distance": 10100,
        "moving_time": 2900,
        "elapsed_time": 2950,
        "total_elevation_gain": 40.0,
        "best_efforts": [
            {"name": "5K", "distance": 5000, "elapsed_time": 1400, "pr_rank": 1},
            {"name": "10K", "distance": 10000, "elapsed_time": 2860, "pr_rank": 2},
        ],
    }


def _summary_raw():
    raw = _detail_raw()
    raw.pop("best_efforts")
    raw["name"] = "Renamed Run"
    return raw


def _user(db, suffix: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.add(User(id=user_id, email=f"best-efforts-{suffix}@example.com"))
    db.flush()
    return user_id


def test_a_summary_resync_keeps_the_stored_best_efforts(db):
    user_id = _user(db, "keep")
    upsert_activity(db, _detail_raw(), user_id)
    db.commit()

    activity = upsert_activity(db, _summary_raw(), user_id)
    db.commit()
    db.refresh(activity)

    efforts = activity.raw_summary.get("best_efforts")
    assert efforts and [e["name"] for e in efforts] == ["5K", "10K"], (
        f"a summary re-sync erased the run's best efforts: {activity.raw_summary!r}"
    )
    assert activity.name == "Renamed Run", "the rest of the summary must still apply"


def test_a_fresh_detail_payload_replaces_them(db):
    user_id = _user(db, "fresh")
    upsert_activity(db, _detail_raw(), user_id)
    db.commit()

    fresh = _detail_raw()
    fresh["best_efforts"] = fresh["best_efforts"][:1]
    activity = upsert_activity(db, fresh, user_id)
    db.commit()
    db.refresh(activity)

    assert [e["name"] for e in activity.raw_summary["best_efforts"]] == ["5K"]
