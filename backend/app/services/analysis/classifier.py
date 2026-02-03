from typing import List
from app.models import Activity

def classify_activity(activity: Activity, history: List[Activity]) -> str:
    """
    Determines Activity Class: Easy, Long, Tempo, Interval, Race, Hills, Recovery.
    """
    # 0. User Intent Override
    if activity.user_intent:
        return activity.user_intent

    if not activity.name:
        return "Easy Run"

    # 1. Parsing name/type override
    name_lower = activity.name.lower()
    if "race" in name_lower:
        return "Race"
    if "workout" in name_lower or "interval" in name_lower:
        return "Intervals"
    if "hill" in name_lower:
        return "Hills"
    if "recovery" in name_lower:
        return "Recovery"

    # 2. Duration heuristics (Long Run)
    # Long run = > 75 mins OR > 1.3x average of recent runs
    recent_durations = [a.moving_time_s for a in history if a.moving_time_s > 0]
    avg_duration = sum(recent_durations) / len(recent_durations) if recent_durations else 0
    
    threshold_s = max(4500, avg_duration * 1.3) # 75 mins or 1.3x average
    if activity.moving_time_s > threshold_s:
        return "Long Run"

    # 3. Hill Detection (New)
    # Heuristic: Elevation gain > 15m per km implies significant rolling/hilly terrain
    if activity.distance_m > 0:
        km = activity.distance_m / 1000.0
        gain_per_km = activity.elev_gain_m / km
        
        # If it's very hilly
        if gain_per_km > 20: 
            return "Hills"
        
        # If it's moderately hilly but effort is high (HR data check if available)
        if gain_per_km > 15:
             # If HR is high (e.g. > 150bpm default or > 80% max), classify as Hills
             if activity.avg_hr and activity.avg_hr > 150: 
                 return "Hills"

    # 4. Intensity heuristics (Tempo vs Easy)
    # Default
    return "Easy Run"
