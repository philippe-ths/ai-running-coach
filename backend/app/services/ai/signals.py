"""Deterministic signal availability detection for ContextPack."""

from typing import Any, List, Optional, Tuple, Union


def infer_signals(
    activity: Any,
    streams: Optional[List[Any]] = None,
    weather: Optional[Any] = None,
) -> Tuple[List[str], List[str]]:
    """Determine available and missing signals for a unified context pack.

    Args:
        activity: DB model or Pydantic schema with summary attributes.
        streams: Optional list of stream objects (or dicts).
        weather: Optional weather data (presence implies availability).

    Returns:
        (available_signals, missing_signals) lists of strings.
    """
    available = []

    # ── Helpers ──
    def get_attr(obj, attr_name, default=None):
        if isinstance(obj, dict):
            return obj.get(attr_name, default)
        return getattr(obj, attr_name, default)

    def has_stream(name: str) -> bool:
        if not streams:
            return False
        for s in streams:
            # Handle both obj.stream_type and dict['type'] for flexibility
            st = get_attr(s, "stream_type") or get_attr(s, "type")
            if st == name:
                return True
        return False

    # ── Detection Logic ──

    # 1. Heart Rate
    # Available if summary has avg/max HR OR stream exists
    if (
        get_attr(activity, "avg_hr") is not None
        or get_attr(activity, "max_hr") is not None
        or has_stream("heartrate")
    ):
        available.append("heart_rate")

    # 2. Cadence
    # Available if summary has avg_cadence OR stream exists
    if get_attr(activity, "avg_cadence") is not None or has_stream("cadence"):
        available.append("cadence")

    # 3. Power
    # Available if summary has avg_watts (sometimes in raw_summary) OR stream exists
    # Check common places for watts
    raw = get_attr(activity, "raw_summary") or {}
    has_watts_stream = has_stream("watts")
    if (
        get_attr(activity, "avg_watts") is not None
        or raw.get("average_watts") is not None
        or has_watts_stream
    ):
        available.append("power")

    # 4. Elevation
    # Available if elevation_gain > 0 OR stream exists
    # Note: flat runs might have 0 gain, but usually 'elevation' signal implies we know the profile.
    # If we have an altitude stream, we definitely have it.
    # If gain is explicitly 0, we effectively have the signal "it is flat".
    # But if gain is None, we don't.
    elev_gain = get_attr(activity, "elev_gain_m") or get_attr(activity, "elevation_gain_m")
    if elev_gain is not None or has_stream("altitude"):
        available.append("elevation")

    # 5. GPS
    # Available if map polyline exists or latlng stream exists
    map_data = raw.get("map") or {}
    polyline = map_data.get("summary_polyline") or map_data.get("polyline")
    if polyline or has_stream("latlng"):
        available.append("gps")

    # 6. Splits
    # Available if we have distance stream (allowing calculation) OR splits in summary
    # Strava summaries often have 'splits_metric' or 'splits_standard'
    if (
        has_stream("distance")
        or raw.get("splits_metric")
        or raw.get("splits_standard")
    ):
        available.append("splits")

    # 7. Weather
    if weather is not None:
        available.append("weather")

    # ── Missing Logic ──
    # "Missing" implies: Absent AND materially affects interpretation for a RUN.
    # We define the universe of desirable signals for a Running Coach:
    desirable = {
        "heart_rate",
        "cadence",
        "power",
        "gps",
        "splits",
        "elevation",
        "weather",
    }
    
    # We always report missing signals relative to this desirable set
    missing = sorted(list(desirable - set(available)))
    available = sorted(available)

    return available, missing
