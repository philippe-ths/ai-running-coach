"""Read-time projection of recorded Strava laps for the activity detail view (#208).

Laps live in ``Activity.raw_summary["laps"]`` as Strava returned them at
detail ingestion. This projects them into the thin ``LapRead`` shape the
frontend renders. An activity with fewer than two laps has no meaningful
recorded segmentation (Strava always reports at least one whole-run lap), so
it projects to an empty list and the frontend hides the section.
"""

from typing import Optional

from app.schemas.detail import LapRead
from app.services.units.cadence import normalize_cadence_spm


def project_laps(raw_summary: Optional[dict], activity_type: str) -> list[LapRead]:
    if not raw_summary:
        return []
    laps = raw_summary.get("laps")
    if not isinstance(laps, list) or len(laps) < 2:
        return []

    projected: list[LapRead] = []
    for i, lap in enumerate(laps, start=1):
        if not isinstance(lap, dict):
            return []
        distance = lap.get("distance")
        elapsed = lap.get("elapsed_time")
        moving = lap.get("moving_time")
        speed = lap.get("average_speed")
        if speed is None and distance is not None and elapsed and elapsed > 0:
            speed = float(distance) / float(elapsed)
        projected.append(
            LapRead(
                lap=i,
                name=lap.get("name"),
                distance_m=float(distance) if distance is not None else None,
                elapsed_time_s=int(elapsed) if elapsed is not None else None,
                moving_time_s=int(moving) if moving is not None else None,
                avg_speed_mps=float(speed) if speed is not None else None,
                avg_hr=lap.get("average_heartrate"),
                max_hr=lap.get("max_heartrate"),
                avg_cadence=normalize_cadence_spm(activity_type, lap.get("average_cadence")),
            )
        )
    return projected
