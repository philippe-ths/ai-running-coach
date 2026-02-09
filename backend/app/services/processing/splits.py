from typing import List, Dict, Any, Optional
import numpy as np

from app.services.units.cadence import normalize_cadence_spm

def calculate_splits(streams: List[Any], activity_type: str = "Run", split_distance_m: int = 1000) -> List[Dict[str, Any]]:
    """
    Calculates splits based on provided streams.
    Requires 'distance' and 'time' streams at minimum.
    Optional streams: 'heartrate', 'grade_smooth', 'cadence', 'velocity_smooth'.
    
    Returns a list of split dictionaries.
    """
    # Convert list of stream objects to a dict for easier access
    stream_map = {s.stream_type: s.data for s in streams}
    
    if "distance" not in stream_map or "time" not in stream_map:
        return []

    distance = stream_map["distance"]
    time = stream_map["time"]
    heartrate = stream_map.get("heartrate")
    grade = stream_map.get("grade_smooth")
    cadence = stream_map.get("cadence")
    
    # Normalize cadence stream if present
    if cadence:
        # Quick heuristic check on the whole stream to see if normalization is needed
        # We use a sample value (e.g. average) to check against expected range
        nums = [x for x in cadence if isinstance(x, (int, float))]
        if nums:
            avg = sum(nums) / len(nums)
            # Use the service logic to determine factor
            normalized = normalize_cadence_spm(activity_type, avg) 
            if normalized > avg + 10: # If normalized is significantly different (e.g. doubled)
                factor = normalized / avg
                # rounding to integer factors 1.0 or 2.0 mostly
                if 1.8 < factor < 2.2:
                    cadence = [x * 2 for x in cadence]

    # Ensure all used streams are same length as distance
    n_points = len(distance)
    if len(time) != n_points:
        return [] # Mismatch
    
    splits = []
    
    current_split_start_idx = 0
    current_split_target_dist = split_distance_m
    
    split_number = 1
    
    # We iterate through points. When distance exceeds current target, we verify the split.
    # Note: Optimization could be done with numpy searchsorted if data was guaranteed numpy arrays.
    
    for i in range(1, n_points):
        d_curr = distance[i]
        
        # Check if we crossed a split boundary
        # We handle multiple splits in one step if gaps are huge (unlikely in streams, but possible)
        while d_curr >= current_split_target_dist:
            # Found a split boundary at index i (inclusive or exclusive? let's say up to i)
            # Actually, the point i is AFTER the boundary.
            # Interpolation would be precise, but nearest point is usually fine for dense streams.
            # Let's use index i as the end of the split.
            
            # Slice range: [current_split_start_idx, i+1) involves points that might be past the mark.
            # Using 'i' as end index means data[current_split_start_idx : i]
            
            # Refine: if distance[i] is 1005 and distance[i-1] is 998, the cut is between i-1 and i.
            # Including i in the average might bias slightly, but effectively standard for app-level analysis.
            
            end_idx = i
            
            # Calculate metrics for this range
            split_data = _compute_split_metrics(
                split_number,
                current_split_start_idx, 
                end_idx, 
                distance, 
                time, 
                heartrate, 
                grade, 
                cadence
            )
            splits.append(split_data)
            
            # Next split setup
            prev_split_dist = current_split_target_dist
            current_split_target_dist += split_distance_m
            current_split_start_idx = end_idx
            split_number += 1
            
            # If we reached the end of data inside the loop (perfect finish), break
            if current_split_start_idx >= n_points:
                break
                
    # Handle partial last split if there is remaining distance meaningful
    # e.g. if > 100m left
    if current_split_start_idx < n_points - 1:
        total_dist = distance[-1]
        last_split_dist_start = (split_number - 1) * split_distance_m
        if total_dist - last_split_dist_start > 100: # Show partial if > 100m
             split_data = _compute_split_metrics(
                split_number,
                current_split_start_idx, 
                n_points, 
                distance, 
                time, 
                heartrate, 
                grade, 
                cadence
            )
             splits.append(split_data)

    return splits

def _compute_split_metrics(
    number: int,
    start_idx: int, 
    end_idx: int, 
    distance_stream, 
    time_stream, 
    hr_stream, 
    grade_stream, 
    cadence_stream
) -> Dict[str, Any]:
    
    # Distance in this split
    # distance_stream is cumulative
    d_start = distance_stream[start_idx] if start_idx > 0 else 0
    # Actually distance stream starts at 0 usually if [0] is 0. 
    # But usually distance[0] is small number or 0.
    
    # Safe access
    if start_idx >= len(distance_stream): 
        # Should not happen
        return {}
        
    s_dist_start = distance_stream[start_idx]
    s_dist_end = distance_stream[end_idx-1] if end_idx > 0 else 0
    
    dist_diff = s_dist_end - s_dist_start
    
    t_start = time_stream[start_idx]
    t_end = time_stream[end_idx-1] if end_idx > 0 else 0
    time_diff = t_end - t_start
    
    if time_diff <= 0: time_diff = 1 # Avoid div zero
    if dist_diff <= 0: dist_diff = 1
    
    # Pace: time per km (in seconds per km) = time_diff / (dist_diff / 1000)
    pace_seconds_per_km = time_diff / (dist_diff / 1000.0)
    
    # Speed: m/s
    speed_mps = dist_diff / time_diff
    
    avg_hr = None
    if hr_stream:
        segment = hr_stream[start_idx:end_idx]
        if len(segment) > 0:
            avg_hr = sum(segment) / len(segment)
            
    avg_grade = None
    if grade_stream:
        segment = grade_stream[start_idx:end_idx]
        if len(segment) > 0:
            avg_grade = sum(segment) / len(segment)

    avg_cadence = None
    if cadence_stream:
        segment = cadence_stream[start_idx:end_idx]
        # Strava often reports raw cadence (spm or rpm).
        # We won't normalize here, we assume frontend or normalization logic handles display.
        # But usually in streams it is spm (runs) or rpm (cycle).
        # For runs, users often see one-legged cadence from Strava streams (~85) so we might need doubling if stream is raw.
        # But let's return average of whatever is there.
        if len(segment) > 0:
            # Filter zeros for cycling coasting or running stops?
            # For avg cadence in a split, usually we include zeros if it's "elapsed" time based? 
            # But "moving" cadence is better.
            # Strava streams usually include zeros when not moving?
            # Let's just create raw average first.
            avg_cadence = sum(segment) / len(segment)

    return {
        "split": number,
        "distance": dist_diff,
        "elapsed_time": time_diff,
        "pace": pace_seconds_per_km,
        "speed": speed_mps,
        "avg_hr": avg_hr,
        "avg_grade": avg_grade,
        "avg_cadence": avg_cadence
    }
