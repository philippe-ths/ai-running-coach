from typing import List, Optional
import numpy as np

# Constants from user requirements
MIN_VELOCITY_MOVING = 0.3  # m/s
MAX_VALID_CADENCE = 220.0  # spm
MEDIAN_FILTER_WINDOW = 7
MAX_GAP_INTERPOLATE_S = 10

def smooth_cadence(
    cadence_data: List[float],
    velocity_data: List[float],
    moving_data: List[bool],
    time_data: List[int]
) -> List[Optional[float]]:
    """
    Produces a smoothed cadence implementation:
    1) Clean dropouts (cadence=0 but moving)
    2) Remove spikes (>220 spm)
    3) Rolling median filter
    4) Linear interpolation for short gaps (<= 10s)
    """
    n = len(cadence_data)
    if n == 0:
        return []
    
    # Ensure all inputs are aligned (same length)
    # Using cadence only where we have corresponding aux data.
    # If partial data, min length is used or we assume aligned.
    # Strava streams are typically consistently length-aligned.
    
    # Convert to numpy for easier handling
    cad_arr = np.array(cadence_data, dtype=float)
    vel_arr = np.array(velocity_data, dtype=float) if velocity_data else np.zeros(n)
    mov_arr = np.array(moving_data, dtype=bool) if moving_data else np.ones(n, dtype=bool) # Assume moving if no stream
    time_arr = np.array(time_data, dtype=float)
    
    # 1. Clean dropouts & Spikes
    # Rule: If cadence == 0 AND (moving == true OR velocity > 0.3), set to NaN
    # Rule: If cadence > 220, set to NaN
    
    # Create a mask for "physically moving"
    is_moving_physically = (mov_arr) | (vel_arr > MIN_VELOCITY_MOVING)
    
    # Zero dropouts
    dropout_mask = (cad_arr == 0) & is_moving_physically
    cad_arr[dropout_mask] = np.nan
    
    # Unrealistic spikes
    cad_arr[cad_arr > MAX_VALID_CADENCE] = np.nan
    
    # 2. Median Filter
    # Rolling median ignores NaNs if possible, but standard rolling median might not.
    # We can skip NaNs or treat them. 
    # Better approach: 
    # For the median filter, we usually want to operate on valid data. 
    # However, preserving the gaps is important.
    # Let's run a window over the data.
    
    # To use a moving median robustly with NaNs, we can use a loop or scipy.ndimage.generic_filter
    # Since we only have numpy, let's implement a simple sliding window median that ignores NaNs.
    
    smoothed = np.full(n, np.nan)
    half_window = MEDIAN_FILTER_WINDOW // 2
    
    for i in range(n):
        start = max(0, i - half_window)
        end = min(n, i + half_window + 1)
        window = cad_arr[start:end]
        valid_window = window[~np.isnan(window)]
        
        if len(valid_window) > 0:
            smoothed[i] = np.median(valid_window)
        else:
            smoothed[i] = np.nan
            
    # 3. Fill short gaps (Linear Interpolation)
    # logic: identify gaps (sequences of NaNs). If gap duration <= MAX_GAP_INTERPOLATE_S, interpolate.
    
    # We can use pandas interpolate if available, but pure numpy/python is safer if pandas isn't guaranteed.
    # (pyproject.toml didn't strictly show pandas, only numpy).
    
    output = smoothed.copy()
    
    # Find indices of valid data
    valid_mask = ~np.isnan(output)
    valid_indices = np.flatnonzero(valid_mask)
    
    if len(valid_indices) < 2:
        # Not enough data to interpolate
        return [None if np.isnan(x) else float(x) for x in output]
    
    # Iterate through gaps between valid points
    for k in range(len(valid_indices) - 1):
        idx_start = valid_indices[k]
        idx_end = valid_indices[k+1]
        
        if idx_end - idx_start > 1:
            # We have a gap
            gap_duration = time_arr[idx_end] - time_arr[idx_start]
            
            if gap_duration <= MAX_GAP_INTERPOLATE_S:
                # Interpolate
                val_start = output[idx_start]
                val_end = output[idx_end]
                
                # Number of points to fill
                steps = idx_end - idx_start
                # Slope
                slope = (val_end - val_start) / float(steps)
                
                for step in range(1, steps):
                    fill_idx = idx_start + step
                    output[fill_idx] = val_start + slope * step

    # 4. Optional: Light EMA smoothing could go here, but median + interp is usually solid.
    # Requirements said "Optional: ... apply a light EMA". Skipping for now to keep it clean, 
    # unless median looks too stepped. Median is step-preserving. Linterp smooths transitions.
    
    # Convert back to list of Optional[float]
    result = []
    for x in output:
        if np.isnan(x):
            result.append(None)
        else:
            result.append(float(x))
            
    return result
