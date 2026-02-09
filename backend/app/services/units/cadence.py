def normalize_cadence_spm(activity_type: str, avg_cadence: float | None) -> float | None:
    """
    Normalizes cadence to Steps Per Minute (SPM) for runs.
    
    Strava API sometimes returns running cadence in full strides per minute (e.g., ~80)
    instead of steps per minute (e.g., ~160). This function doubles the value if it's
    suspiciously low for a run, suggesting it's in strides/min.
    
    Rules:
    - If avg_cadence is None -> None
    - If activity_type is not a Run variant -> return avg_cadence unchanged
    - If activity_type implies Run:
        - if avg_cadence < 130: return avg_cadence * 2
        - else: return avg_cadence unchanged
    """
    if avg_cadence is None:
        return None

    if activity_type is None:
        return avg_cadence

    # Check for Run variants: "Run", "VirtualRun", "TrailRun", or intents like "Long Run", "Tempo Run"
    # We normalized to lowercase for safer checking
    t = activity_type.lower()

    # Intent Strings from frontend/components/IntentSelector.tsx:
    # "Easy Run", "Recovery", "Long Run", "Tempo", "Intervals", "Hills", "Race"
    # Note: "Tempo", "Intervals", "Hills", "Race" do not contain "run".
    
    # Explicit run intents that might be used
    run_intents = ["run", "jog", "sprint", "tempo", "intervals", "hills", "race", "fartlek", "track"]
    
    # Check if ANY run intent keyword is in the type string
    is_run_intent = any(keyword in t for keyword in run_intents)
    
    # Exclusion: Ensure it's not a bike ride (e.g. "Bike Race")
    is_bike = "bike" in t or "ride" in t or "cycle" in t or "swim" in t or "walk" in t or "hike" in t
    
    if not is_run_intent or is_bike:
        if "run" not in t: # Not a run type — return raw cadence
            return avg_cadence
        if is_bike: # Explicit bike override
             return avg_cadence

    # Logic for runs: check if it looks like strides/min (< 130)
    if avg_cadence < 130:
        return avg_cadence * 2
    
    return avg_cadence
