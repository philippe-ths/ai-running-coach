import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, ActivityStream, StravaAccount
from app.schemas import SyncResponse
from app.services.strava_ingestion.port import StravaPort, Tokens

logger = logging.getLogger(__name__)

# Buffer in seconds before token expiry we'll trigger a refresh.
_TOKEN_REFRESH_BUFFER_S = 60

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


async def ensure_valid_access_token(
    db: Session, account: StravaAccount, port: StravaPort
) -> str:
    """Return a valid access token, refreshing and persisting if needed."""
    if account.expires_at > time.time() + _TOKEN_REFRESH_BUFFER_S:
        return account.access_token

    tokens = await port.refresh_token(account.refresh_token)
    _apply_tokens(account, tokens)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account.access_token


def _apply_tokens(account: StravaAccount, tokens: Tokens) -> None:
    account.access_token = tokens.access_token
    account.refresh_token = tokens.refresh_token
    account.expires_at = tokens.expires_at


def upsert_activity(db: Session, raw: dict, user_id) -> Activity:
    """Parse raw Strava JSON and upsert an Activity row. Caller commits."""
    stmt = select(Activity).where(Activity.strava_activity_id == raw["id"])
    existing = db.execute(stmt).scalars().first()

    activity_data = {
        "user_id": user_id,
        "strava_activity_id": raw["id"],
        "name": raw.get("name", "Unknown Run"),
        "type": raw.get("type", "Run"),
        "start_date": datetime.strptime(raw["start_date"], "%Y-%m-%dT%H:%M:%SZ"),
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


async def ingest_recent_activities(
    db: Session,
    account: StravaAccount,
    port: StravaPort,
    *,
    since: datetime | None = None,
    fetch_streams: bool = True,
) -> tuple[list[Activity], SyncResponse]:
    """Fetch recent activities, upsert them, and (optionally) fetch their streams.

    Returns the persisted Activity rows alongside a SyncResponse summary.
    Analysis is the caller's responsibility.

    `fetch_streams=False` upserts activity summaries only, skipping the
    per-activity stream call. This is the rate-limit-safe path for a
    full-history backfill: streams cost one Strava call per activity, so
    eagerly fetching them across a long window blows the 100-requests/15-min
    ceiling. Summaries alone fully populate the activity list and the distance
    / time trend charts; stream-derived analysis backfills separately. See #109.
    """
    stats = SyncResponse()
    ingested: list[Activity] = []

    if since is None:
        since = datetime.now() - timedelta(days=30)

    try:
        access_token = await ensure_valid_access_token(db, account, port)
        raw_activities = await port.list_recent_activities(
            access_token=access_token, since=since, per_page=50
        )
        stats.fetched = len(raw_activities)

        for raw in raw_activities:
            try:
                activity = upsert_activity(db, raw, account.user_id)
                db.flush()
                stats.upserted += 1

                if fetch_streams:
                    await _fetch_and_store_streams(db, activity, access_token, port)
                db.commit()

                ingested.append(activity)
            except Exception as exc:
                db.rollback()
                msg = f"Error ingesting activity {raw.get('id')}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)
    except Exception as exc:
        msg = f"Ingestion failed globally: {exc}"
        logger.error(msg)
        stats.errors.append(msg)

    return ingested, stats


async def ingest_activity_by_id(
    db: Session,
    account: StravaAccount,
    port: StravaPort,
    strava_activity_id: int,
) -> Activity:
    """Fetch a single activity summary and upsert it. No streams, no analysis."""
    access_token = await ensure_valid_access_token(db, account, port)
    raw = await port.get_activity(access_token, strava_activity_id)
    activity = upsert_activity(db, raw, account.user_id)
    db.commit()
    db.refresh(activity)
    return activity
