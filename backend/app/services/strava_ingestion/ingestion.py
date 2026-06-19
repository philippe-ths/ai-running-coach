import logging
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from app.models import Activity, ActivityStream, StravaAccount, UserProfile
from app.schemas import SyncResponse
from app.services.strava_ingestion.port import StravaPort, Tokens

logger = logging.getLogger(__name__)


def extract_hr_zone_bounds(raw_zones: dict | None) -> list[int] | None:
    """Pull the 5 ascending HR-zone lower bounds (bpm) from a Strava
    `/athlete/zones` payload, or None if the shape is not as expected (#297).

    Strava returns ``{"heart_rate": {"custom_zones": bool, "zones": [{"min",
    "max"}, ...]}}``. We keep each zone's ``min`` as its lower bound; the binning
    only needs the ordering. A payload that is not exactly 5 ascending integer
    zones is rejected so analysis falls back to the %-of-max-HR scheme rather
    than binning against garbage.
    """
    if not isinstance(raw_zones, dict):
        return None
    hr = raw_zones.get("heart_rate")
    if not isinstance(hr, dict):
        return None
    zones = hr.get("zones")
    if not isinstance(zones, list) or len(zones) != 5:
        return None
    bounds: list[int] = []
    for zone in zones:
        if not isinstance(zone, dict):
            return None
        low = zone.get("min")
        if not isinstance(low, (int, float)) or isinstance(low, bool):
            return None
        bounds.append(int(low))
    if bounds != sorted(bounds):
        return None
    return bounds


async def sync_athlete_zones(
    db: Session,
    account: StravaAccount,
    port: StravaPort,
    access_token: str,
) -> list[int] | None:
    """Fetch the runner's Strava HR zones and store them on their UserProfile
    (#297). Guarded: a zones failure must never break a sync, since zones only
    refine the time-in-zone metric and analysis degrades to the %max scheme.
    Returns the stored bounds, or None when unavailable/unchanged-but-absent.
    """
    try:
        raw_zones = await port.get_athlete_zones(access_token)
        bounds = extract_hr_zone_bounds(raw_zones)
        if not bounds:
            return None
        profile = (
            db.query(UserProfile)
            .filter(UserProfile.user_id == account.user_id)
            .first()
        )
        if profile is None:
            return None
        profile.hr_zones = bounds
        profile.hr_zones_source = "strava"
        db.commit()
        return bounds
    except Exception as exc:  # never break sync over zones
        db.rollback()
        logger.warning("strava_zones_sync_failed: %s", exc)
        return None

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
    db: Session, account: StravaAccount, port: StravaPort, *, force: bool = False
) -> str:
    """Return a valid access token, refreshing and persisting if needed.

    Pass force=True to unconditionally refresh (e.g. after a mid-flight 401).
    """
    if not force and account.expires_at > time.time() + _TOKEN_REFRESH_BUFFER_S:
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
    # undefer raw_summary (#359): the lap-preservation check below reads
    # existing.raw_summary["laps"], so load it with the row on this hot
    # (per-sync) upsert path rather than as a follow-up query.
    stmt = (
        select(Activity)
        .where(Activity.strava_activity_id == raw["id"])
        .options(undefer(Activity.raw_summary))
    )
    existing = db.execute(stmt).scalars().first()

    # Preserve recorded laps across a lap-less re-sync. The detail endpoint
    # (webhook/polling, ingest_activity_by_id) returns the runner's `laps`;
    # the summary list endpoint (manual sync, backfill) does not. Without this,
    # a routine "Sync Now" would overwrite an activity's recorded laps with a
    # lap-less payload and re-analysis would silently downgrade its lap-sourced
    # interval structure back to the stream heuristic (#170). A fresh payload
    # that does carry laps still wins (the detail re-fetch is authoritative).
    if (
        existing
        and not raw.get("laps")
        and isinstance(existing.raw_summary, dict)
        and existing.raw_summary.get("laps")
    ):
        raw = {**raw, "laps": existing.raw_summary["laps"]}

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
        # Refresh the runner's Strava HR zones once per sync so time-in-zone
        # matches Strava (#297). Guarded internally; never blocks ingestion.
        await sync_athlete_zones(db, account, port, access_token)
        try:
            raw_activities = await port.list_recent_activities(
                access_token=access_token, since=since, per_page=50
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.warning("strava_401_mid_flight: force-refreshing token and retrying list")
            access_token = await ensure_valid_access_token(db, account, port, force=True)
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

                _assign_block(db, activity)
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
    try:
        raw = await port.get_activity(access_token, strava_activity_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 401:
            raise
        logger.warning("strava_401_mid_flight: force-refreshing token and retrying get_activity")
        access_token = await ensure_valid_access_token(db, account, port, force=True)
        raw = await port.get_activity(access_token, strava_activity_id)
    activity = upsert_activity(db, raw, account.user_id)
    db.commit()
    db.refresh(activity)
    _assign_block(db, activity)
    return activity


def _assign_block(db: Session, activity) -> None:
    """A1: every newly-ingested activity is grouped into its Block here, so all
    ingest paths (webhook, polling, manual sync, backfill) converge on the same
    grouping. A re-synced activity keeps its block. Guarded: a grouping failure
    must never break ingestion (the baseline-recompute pattern)."""
    if activity.block_id is not None:
        return
    try:
        from app.services.blocks import assign_activity_to_block

        assign_activity_to_block(db, activity)
    except Exception:
        db.rollback()
        logger.exception(
            "block assignment failed for activity %s", activity.strava_activity_id
        )
