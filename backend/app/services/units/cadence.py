def normalize_cadence_spm(activity_type: str, avg_cadence: float | None) -> float | None:
    """
    Normalizes cadence to Steps Per Minute (SPM).
    
    Strava API sometimes returns cadence in full strides per minute (e.g., ~80)
    instead of steps per minute (e.g., ~160). This function doubles the value if it's
    suspiciously low (< 130), suggesting it's in strides/min.
    
    This applies to all activity types as per request to handle bike/walk cadence similarly.
    """
    if avg_cadence is None:
        return None

    if activity_type is None:
        pass # Allow fall-through to check value

    # Logic for all activities: check if it looks like strides/min (< 130)
    # User request: "Cadence from strava should always be doubled. Not just for running."
    if avg_cadence < 130:
        return avg_cadence * 2
    
    return avg_cadence
