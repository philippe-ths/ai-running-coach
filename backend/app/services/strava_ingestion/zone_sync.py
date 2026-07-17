"""Ingestion-time HR-zone calibration (#297).

Fetches the runner's Strava HR zones and stores them on their UserProfile so
analysis bins time-in-zone against the runner's own boundaries. The parsing —
what a valid set of zone bounds is — is analysis domain knowledge and lives with
the binning it serves (`analysis.zones.extract_hr_zone_bounds`); this module is
the ingestion-time trigger that calls the Strava port and persists the result.
"""

import logging

from sqlalchemy.orm import Session

from app.models import StravaAccount, UserProfile
from app.services.analysis.zones import extract_hr_zone_bounds
from app.services.strava_ingestion.port import StravaPort

logger = logging.getLogger(__name__)


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
