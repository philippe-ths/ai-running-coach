"""The raw persistence writer: parse Strava JSON into Activity/ActivityStream
rows.

Split out of the batch orchestrator (#702) so the writer is separable and
testable from the batch loop. It is mechanical persistence only: the one piece
of analysis domain knowledge it used to embed — which lap source is
authoritative — now lives with the interval logic it serves
(`analysis.intervals.merge_preserved_laps`) and is called from here.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.models import Activity, ActivityStream, StravaAccount
from app.services.analysis.intervals import merge_preserved_laps
from app.services.strava_ingestion.auth import ensure_valid_access_token
from app.services.strava_ingestion.port import StravaPort

# Stream types pulled during deep ingestion.
_STREAM_TYPES = [
    "time",
    "distance",
    "latlng",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "temp",
    "moving",
    "grade_smooth",
]


def upsert_activity(db: Session, raw: dict, user_id) -> Activity:
    """Parse raw Strava JSON and upsert an Activity row. Caller commits."""
    # undefer raw_summary (#359): the lap-preservation check below reads
    # existing.raw_summary["laps"], so load it with the row on this hot
    # (per-sync) upsert path rather than as a follow-up query.
    stmt = (
        select(Activity)
        .where(Activity.strava_activity_id == raw["id"])
        .options(undefer(Activity.raw_summary))
    )
    existing = db.execute(stmt).scalars().first()

    # Preserve recorded laps across a lap-less re-sync (#170): the interval logic
    # owns which lap source is authoritative, so the rule lives there.
    raw = merge_preserved_laps(existing.raw_summary if existing else None, raw)

    activity_data = {
        "user_id": user_id,
        "strava_activity_id": raw["id"],
        "name": raw.get("name", "Unknown Run"),
        "type": raw.get("type", "Run"),
        "start_date": datetime.strptime(raw["start_date"], "%Y-%m-%dT%H:%M:%SZ"),
        # Strava's local wall-clock start (#399). Same string format as start_date
        # but the trailing Z is misleading — it is already the runner's local time,
        # so parse it naive and store it as-is. Absent on rare payloads -> None,
        # and readers fall back to start_date via Activity.local_start.
        "start_date_local": (
            datetime.strptime(raw["start_date_local"], "%Y-%m-%dT%H:%M:%SZ")
            if raw.get("start_date_local")
            else None
        ),
        "distance_m": int(raw.get("distance", 0)),
        "moving_time_s": raw.get("moving_time", 0),
        "elapsed_time_s": raw.get("elapsed_time", 0),
        "elev_gain_m": raw.get("total_elevation_gain", 0.0),
        "avg_hr": raw.get("average_heartrate"),
        "max_hr": raw.get("max_heartrate"),
        "avg_cadence": raw.get("average_cadence"),
        "average_speed_mps": raw.get("average_speed"),
        "raw_summary": raw,
    }

    if existing:
        for key, value in activity_data.items():
            setattr(existing, key, value)
        db.add(existing)
        return existing

    new_activity = Activity(**activity_data)
    db.add(new_activity)
    return new_activity


async def _fetch_and_store_streams(
    db: Session,
    activity: Activity,
    access_token: str,
    port: StravaPort,
) -> bool:
    """Replace stored streams for activity with fresh data from Strava."""
    streams_data = await port.get_activity_streams(
        access_token, activity.strava_activity_id, _STREAM_TYPES
    )
    if not streams_data:
        return False

    db.query(ActivityStream).filter(ActivityStream.activity_id == activity.id).delete()

    for stream_type, payload in streams_data.items():
        db.add(
            ActivityStream(
                activity_id=activity.id,
                stream_type=stream_type,
                data=payload.get("data", []),
            )
        )
    db.commit()
    return True


async def refetch_streams(
    db: Session, account: StravaAccount, activity: Activity, port: StravaPort
) -> bool:
    """Re-fetch and store streams for a single activity. Used by deep-processing."""
    access_token = await ensure_valid_access_token(db, account, port)
    return await _fetch_and_store_streams(db, activity, access_token, port)
