"""Batch ingestion orchestrators.

The top-level flow over the StravaPort: fetch recent activities (or one by id),
upsert them via the persistence writer, optionally fetch streams, and converge
every ingest path on the same block grouping. The individual contracts this used
to bundle now live behind their own seams (#702):

  - token refresh + per-account lock -> `auth.py`
  - the raw persistence writer + stream fetch -> `persistence.py`
  - HR-zone calibration -> `zone_sync.py` (parser in `analysis.zones`)
  - lap-preservation -> `analysis.intervals.merge_preserved_laps`
  - block grouping + stranded-row recovery -> `blocks.py`

Analysis is the caller's responsibility.
"""

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.models import StravaAccount
from app.schemas import SyncResponse
from app.services.blocks import (
    assign_block_guarded,
    reconcile_unassigned_activities,
)
from app.services.strava_ingestion.auth import ensure_valid_access_token
from app.services.strava_ingestion.persistence import (
    _fetch_and_store_streams,
    upsert_activity,
)
from app.services.strava_ingestion.port import StravaRateLimited
from app.services.strava_ingestion.zone_sync import sync_athlete_zones

logger = logging.getLogger(__name__)


async def ingest_recent_activities(
    db: Session,
    account: StravaAccount,
    port,
    *,
    since: datetime | None = None,
    fetch_streams: bool = True,
) -> tuple[list, SyncResponse]:
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
    ingested: list = []

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

                assign_block_guarded(db, activity)
                ingested.append(activity)
            except Exception as exc:
                db.rollback()
                msg = f"Error ingesting activity {raw.get('id')}: {exc}"
                logger.error(msg)
                stats.errors.append(msg)

        # Once per batch (not per activity): recover any activity an earlier
        # ingest committed but left block-less because its guarded assignment
        # raised (#515).
        reconcile_unassigned_activities(db, account.user_id)
    except StravaRateLimited:
        # A rate limit is a "retry shortly" signal, not a per-activity error to
        # log-and-continue: propagate it so the live path returns HTTP 429 (#602,
        # via the app-level handler) rather than a 200 with the failure buried in
        # stats.errors. Background callers let it surface to the job/budget gate.
        raise
    except Exception as exc:
        msg = f"Ingestion failed globally: {exc}"
        logger.error(msg)
        stats.errors.append(msg)

    return ingested, stats


async def ingest_activity_by_id(
    db: Session,
    account: StravaAccount,
    port,
    strava_activity_id: int,
):
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
    assign_block_guarded(db, activity)
    # One sweep per ingest event (single-activity path, e.g. self-heal diff):
    # recover any earlier activity left block-less by a guarded failure (#515).
    reconcile_unassigned_activities(db, account.user_id)
    return activity
