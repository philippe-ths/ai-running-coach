"""HR-zone binning domain knowledge (#297).

The parser that turns a Strava `/athlete/zones` payload into the five ascending
lower bounds `calculate_time_in_zones` bins against. It lives with the analysis
it serves rather than inside the ingestion writer: the ingestion-time
zone-sync (`strava_ingestion.zone_sync`) calls this to calibrate, and the
analysis pipeline reads the stored bounds off `UserProfile.hr_zones`.
"""


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
