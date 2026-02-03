import numpy as np
from typing import Optional, List, Dict, Any
from app.models import Activity

def calculate_time_in_zones(streams: Dict[str, List[any]], max_hr: int = 190) -> Optional[Dict[str, int]]:
    """
    Calculates time spent in 5 heart rate zones.
    Z1: 50-60%
    Z2: 60-70%
    Z3: 70-80%
    Z4: 80-90%
    Z5: 90-100%
    Returns: Dict with seconds in each zone, e.g. {"Z1": 300, "Z2": 100}
    """
    hr_list = streams.get("heartrate", [])
    if not hr_list:
        return None
    
    # Filter out zeros if any
    hr_arr = np.array([h for h in hr_list if h > 30]) 
    if len(hr_arr) == 0:
        return None

    zones = {
        "Z1": 0, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0
    }
    
    # Calculate thresholds
    t = [0.5 * max_hr, 0.6 * max_hr, 0.7 * max_hr, 0.8 * max_hr, 0.9 * max_hr]
    
    # Binning
    # Counts of HR readings (assuming 1 reading = 1 second for 'time' stream usually.
    # Ideally should use the 'time' stream deltas, but simple count is close enough for MVP.
    
    # Optimized numpy binning
    # bins: [0, t0, t1, t2, t3, t4, max_possible]
    # invalid (<50%), Z1, Z2, Z3, Z4, Z5
    bins = [0, t[0], t[1], t[2], t[3], t[4], 250] 
    
    hist, _ = np.histogram(hr_arr, bins=bins)
    
    # hist[0] is garbage (<50%), hist[1] is Z1, etc.
    zones["Z1"] = int(hist[1])
    zones["Z2"] = int(hist[2])
    zones["Z3"] = int(hist[3])
    zones["Z4"] = int(hist[4])
    zones["Z5"] = int(hist[5])
    
    return zones

def calculate_pace_variability(streams: Dict[str, List[any]]) -> Optional[float]:
    """
    Calculates Coefficient of Variation (CV) for velocity_smooth.
    Lower is steadier.
    """
    velocity = streams.get("velocity_smooth", [])
    if not velocity or len(velocity) < 60:
        return None
    
    # Filter zeros/stops
    v_arr = np.array([v for v in velocity if v > 0.5]) 
    if len(v_arr) == 0:
        return None

    mean_v = np.mean(v_arr)
    std_v = np.std(v_arr)
    
    if mean_v == 0: return None
    
    # CV as percentage
    return round((std_v / mean_v) * 100, 2)

def calculate_hr_drift(streams: Dict[str, List[any]]) -> Optional[float]:
    """
    Calculates Pace:HR decoupling (drift) over the run.
    Drift > 5% indicates fatigue/dehydration.
    Formula: (First_Half_Ratio - Second_Half_Ratio) / First_Half_Ratio
    Where Ratio = Speed / HR
    """
    hr = streams.get("heartrate", [])
    vel = streams.get("velocity_smooth", [])
    
    if not hr or not vel or len(hr) != len(vel) or len(hr) < 600: # Need at least ~10 mins
        return None
        
    # use numpy for ease
    hr_arr = np.array(hr)
    vel_arr = np.array(vel)
    
    # Filter valid moving data (speed > 0.5 m/s, hr > 60)
    mask = (vel_arr > 0.5) & (hr_arr > 60)
    if np.sum(mask) < 600:
        return None

    clean_hr = hr_arr[mask]
    clean_vel = vel_arr[mask]
    
    half_point = len(clean_hr) // 2
    
    # Efficiency Factor (EF) = Speed / HR
    # Higher is better.
    ef_first = np.mean(clean_vel[:half_point] / clean_hr[:half_point])
    ef_second = np.mean(clean_vel[half_point:] / clean_hr[half_point:])
    
    if ef_first == 0: return None
    
    # Decoupling %: (EF1 - EF2) / EF1 * 100
    decoupling = (1 - (ef_second / ef_first)) * 100
    return round(decoupling, 2)

def calculate_effort_score(activity: Activity) -> float:
    """
    Calculates a TRIMP-like effort score.
    If HR is available, use zone weighting (simplified).
    Else, use moving_time * intensity_factor.
    """
    if activity.avg_hr and activity.max_hr:
        # Very rough MVP TRIMP approximation
        # (avg_hr / max_hr) ^ 3 * duration_minutes
        hr_ratio = activity.avg_hr / activity.max_hr
        minutes = activity.moving_time_s / 60.0
        return round(minutes * (hr_ratio ** 3) * 10, 1) # Scaling factor for readable numbers
    
    # Fallback to duration only (assume 'moderate' effort if unknown)
    # Just a placeholder heuristic
    return round((activity.moving_time_s / 60.0), 1)

def compute_derived_metrics_data(activity: Activity, streams_dict: Dict[str, List[any]] = {}, max_hr: int = 190) -> Dict[str, Any]:
    """
    Aggregates metrics for the DerivedMetric model.
    """
    # Effort Score (Basic)
    # If streams available with HR, could be better, but sticking to basic for consistency
    effort = calculate_effort_score(activity)

    drift = None
    pace_var = None
    zones = None
    
    if streams_dict:
        drift = calculate_hr_drift(streams_dict)
        pace_var = calculate_pace_variability(streams_dict)
        
        # Use provided max_hr if reasonable, else default
        effective_max = max_hr if max_hr and max_hr > 150 else 190 
        
        zones = calculate_time_in_zones(streams_dict, effective_max)

    return {
        "effort_score": effort,
        "pace_variability": pace_var,
        "hr_drift": drift,
        "time_in_zones": zones
    }
