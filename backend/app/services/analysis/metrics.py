import numpy as np
from typing import Optional, List, Dict, Any
from app.models import Activity
from app.services.analysis.stops import analyze_stops

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

def calculate_efficiency(streams: Dict[str, List[any]]) -> Optional[Dict[str, Any]]:
    """
    Calculates Efficiency Factor (EF) stats.
    Efficiency = Speed (m/min) / HR (bpm).
    """
    velocity = streams.get("velocity_smooth", [])
    heartrate = streams.get("heartrate", [])
    
    if not velocity or not heartrate: 
        return None
    
    # Ensure same length, trim to min
    length = min(len(velocity), len(heartrate))
    if length < 180: # Min 3 mins for meaningful "best sustained"
        return None
    
    # Convert to numpy
    v_arr = np.array(velocity[:length])
    hr_arr = np.array(heartrate[:length])
    
    # --- 1. Average Efficiency (User for "Today's walk easy?") ---
    # Filter valid running/walking: Speed > 0.8 m/s (slow walk), HR > 40
    # User might walk? "Was today’s walk easy..."
    # 0.8 m/s = ~20:00/km (3 km/h).
    mask = (v_arr > 0.8) & (hr_arr > 40)
    
    if np.sum(mask) < 60:
        return None
        
    clean_v = v_arr[mask]
    clean_hr = hr_arr[mask]
    
    # Efficiency in m/min per bpm
    eff_values = (clean_v * 60.0) / clean_hr
    avg_efficiency = float(np.mean(eff_values))
    
    # --- 2. Best Sustained Efficiency (3 min) ---
    # We use the raw stream, speed 0 -> eff 0.
    # We want "sustained" so we treat stops as penalty.
    
    # Raw Efficiency Stream (0 where invalid/stopped to penalize stops in "sustained" window)
    # Avoid division by zero
    safe_hr = np.where(hr_arr > 40, hr_arr, 1) # avoid div by 0
    raw_eff = (v_arr * 60.0) / safe_hr
    
    # Zero out invalid speeds or low HRs
    raw_eff[(v_arr <= 0.8) | (hr_arr <= 40)] = 0.0
    
    # Convolution for moving average
    window_sec = 180 # 3 minutes
    kernel = np.ones(window_sec) / window_sec
    rolling_eff = np.convolve(raw_eff, kernel, mode='valid')
    
    best_sustained = float(np.max(rolling_eff)) if len(rolling_eff) > 0 else avg_efficiency

    # --- 3. Efficiency Curve (Rolling 60s) ---
    # For chart. Smoother than raw.
    chart_window = 60
    chart_kernel = np.ones(chart_window) / chart_window
    chart_curve = np.convolve(raw_eff, chart_kernel, mode='same')
    
    # Downsample for frontend (every 10th point)
    # Store as simple list of values. Frontend maps to time x-axis.
    curve_data = [round(x, 3) for x in chart_curve[::10].tolist()]

    return {
        "average": round(avg_efficiency, 2),
        "best_sustained": round(best_sustained, 2),
        "curve": curve_data, 
        "unit": "m/min/bpm"
    }

# --- Per-activity training-load primitive (#186) ---
#
# One comparable unit across activities with and without HR: Edwards-style
# "zone-minutes", load = Σ (minutes in zone z) × z, where z is the HR zone 1-5
# (or its best per-tier estimate). Because every tier maps onto the same 1-5
# intensity scale, the values are comparable and summable across a mixed
# history (the property the acute/chronic/balance model is built from). This
# replaced the old two-branch formula whose with-HR branch (minutes ×
# (avg/peak)³ × 10) and no-HR branch (raw minutes) lived on different scales.

# Edwards zone weights: the zone number is the intensity weight.
_ZONE_WEIGHT = {"Z1": 1, "Z2": 2, "Z3": 3, "Z4": 4, "Z5": 5}

# Tier C, no HR and no RPE: assume the population-typical aerobic intensity
# (zone 2, an easy/steady run — what a strap-less run usually is). Documented
# assumption; a pace-based refinement is possible follow-up work.
TRIMP_DEFAULT_BAND = 2


def _band_from_pct(pct: float) -> int:
    """The HR zone (1-5) the average HR falls in, against the athlete max.

    Boundaries match ``calculate_time_in_zones`` / the effort-axis fallback
    (Z1 50-60%, Z2 60-70%, Z3 70-80%, Z4 80-90%, Z5 90-100%).
    """
    if pct < 0.60:
        return 1
    if pct < 0.70:
        return 2
    if pct < 0.80:
        return 3
    if pct < 0.90:
        return 4
    return 5


def _rpe_to_band(rpe: int) -> int:
    """Map a 1-10 RPE onto the 1-5 intensity scale.

    Mirrors ``services.coach.perceived_effort._rpe_band`` (duplicated rather
    than imported so the analysis layer stays independent of the coach layer).
    """
    return min(5, max(1, (rpe + 1) // 2))


def compute_training_load(
    moving_time_s: Optional[float],
    time_in_zones: Optional[Dict[str, int]] = None,
    avg_hr: Optional[float] = None,
    athlete_max_hr: Optional[int] = None,
    rpe: Optional[int] = None,
) -> float:
    """Per-activity training load in Edwards "zone-minutes" (#186).

    One scale regardless of HR availability, estimated from the best signal:

    * Tier A — a per-sample HR zone distribution (``time_in_zones``): the true
      Edwards sum Σ (minutes in zone z) × z.
    * Tier B — summary HR only (``avg_hr`` against the ATHLETE max, never the
      activity's recorded peak): the whole run weighted by the zone its average
      HR falls in.
    * Tier C — no HR: session-RPE when present, else the default aerobic band.

    Returns load rounded to one decimal; 0.0 when there is no usable duration.
    """
    minutes = (moving_time_s or 0) / 60.0

    # Tier A: a real per-sample zone distribution.
    if time_in_zones:
        zone_items = [(z, sec) for z, sec in time_in_zones.items() if z in _ZONE_WEIGHT]
        zone_seconds = sum(sec for _, sec in zone_items)
        if zone_seconds > 0:
            weighted = sum((sec / 60.0) * _ZONE_WEIGHT[z] for z, sec in zone_items)
            # Moving time below Z1 (HR < 50% max) or in HR gaps is excluded from
            # the zones but is still aerobic work; count it at weight 1 so an easy
            # run never collapses toward zero. Every minute is worth >= 1 load unit.
            sub_zone_minutes = max(0.0, minutes - zone_seconds / 60.0)
            return round(weighted + sub_zone_minutes, 1)

    if minutes <= 0:
        return 0.0

    # Tier B: summary HR, athlete-max denominator.
    if avg_hr and athlete_max_hr and athlete_max_hr > 0:
        return round(minutes * _band_from_pct(avg_hr / athlete_max_hr), 1)

    # Tier C: session-RPE, then the documented default aerobic weight.
    if rpe:
        return round(minutes * _rpe_to_band(rpe), 1)
    return round(minutes * TRIMP_DEFAULT_BAND, 1)

def compute_derived_metrics_data(
    activity: Activity,
    streams_dict: Dict[str, List[any]] = {},
    max_hr: int = 190,
    rpe: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Aggregates metrics for the DerivedMetric model.
    """
    drift = None
    pace_var = None
    zones = None
    stops = None
    efficiency = None

    if streams_dict:
        drift = calculate_hr_drift(streams_dict)
        pace_var = calculate_pace_variability(streams_dict)
        stops = analyze_stops(streams_dict)
        efficiency = calculate_efficiency(streams_dict)

        # Use provided max_hr if reasonable, else default
        effective_max = max_hr if max_hr and max_hr > 150 else 190

        zones = calculate_time_in_zones(streams_dict, effective_max)

    # Training-load primitive (#186): comparable zone-minutes across HR/no-HR.
    # Tier A uses the zone distribution; Tier B the summary HR against the
    # athlete max; Tier C the session RPE or the default aerobic band.
    effort = compute_training_load(
        activity.moving_time_s,
        time_in_zones=zones,
        avg_hr=activity.avg_hr,
        athlete_max_hr=max_hr,
        rpe=rpe,
    )

    return {
        "effort_score": effort,
        "pace_variability": pace_var,
        "hr_drift": drift,
        "time_in_zones": zones,
        "stops_analysis": stops,
        "efficiency_analysis": efficiency
    }
