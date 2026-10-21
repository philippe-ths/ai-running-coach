"""The runner's own calendar: what "today" is where they are (#1073, #1085).

The app stores no time zone, and the server's date is UTC, so near midnight a
runner far from UTC was a day out: the chat's "this week" and a session ticked by
hand both used the server's day, while every session was bucketed by its
activity's local day. The zone comes from the same place that local day does,
the runner's most recent Strava activity, so the two cannot disagree.

Travel is the known gap: until the first activity recorded in the new place, the
runner's day is still the last place's.
"""

import re
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models import Activity

# Strava writes "(GMT+00:00) Europe/London": the offset when the activity was
# recorded, then the zone it was recorded in.
_STRAVA_ZONE = re.compile(r"^\(GMT[+-]\d{2}:\d{2}\)\s+(\S+)$")


def _zone_of(activity: Activity) -> Optional[tzinfo]:
    name = _STRAVA_ZONE.match(((activity.raw_summary or {}).get("timezone") or "").strip())
    if name:
        try:
            return ZoneInfo(name.group(1))
        except (ZoneInfoNotFoundError, ValueError):
            pass
    # No zone name: the offset the activity's own local start was written at.
    if activity.start_date is not None and activity.start_date_local is not None:
        start = activity.start_date
        if start.tzinfo is not None:
            start = start.astimezone(timezone.utc).replace(tzinfo=None)
        offset = activity.start_date_local - start
        if abs(offset) <= timedelta(hours=14):
            return timezone(offset)
    return None


def runner_zone(db: Session, user_id) -> tzinfo:
    """The zone of the runner's most recent activity, or UTC with none."""
    latest = (
        db.query(Activity)
        .filter(Activity.user_id == user_id)
        .order_by(Activity.start_date.desc())
        .first()
    )
    return (_zone_of(latest) if latest is not None else None) or timezone.utc


def runner_today(db: Session, user_id, *, now: Optional[datetime] = None) -> date:
    """Today's date where the runner is."""
    return local_date(now or datetime.now(timezone.utc), runner_zone(db, user_id))


def local_date(instant: datetime, zone: tzinfo) -> date:
    """The runner's calendar day for a stored instant. A naive instant is UTC,
    as every stored timestamp here is."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(zone).date()
