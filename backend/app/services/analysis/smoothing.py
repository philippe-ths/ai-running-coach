import warnings
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
    
    # Convert to numpy for easier handling. velocity_data / moving_data are part
    # of the contract but no longer consulted for the dropout rule (#325): a raw
    # cadence of 0 is a dropout regardless of moving state.
    cad_arr = np.array(cadence_data, dtype=float)
    time_arr = np.array(time_data, dtype=float)
    
    # 1. Clean dropouts & Spikes
    # Rule: If cadence == 0, set to NaN (a dropout, never a measurement)
    # Rule: If cadence > 220, set to NaN
    #
    # A raw cadence of 0 is always a dropout, not a reading: Strava reports 0
    # before the first detected step and while not running / paused, and you
    # cannot run at 0 spm. Treating it as missing (NaN -> None downstream) keeps
    # the rolling median from averaging dropout zeros together with the first
    # real cadence, which previously bled a physiologically meaningless low ramp
    # value into smoothed_cadence at the dropout/real boundary (#325). The
    # earlier guard only NaN'd zeros while "physically moving", so a leading
    # not-yet-moving dropout survived as 0.0 and corrupted the boundary window.

    # Zero dropouts
    dropout_mask = cad_arr == 0
    cad_arr[dropout_mask] = np.nan
    
    # Unrealistic spikes
    cad_arr[cad_arr > MAX_VALID_CADENCE] = np.nan
    
    # 2. Median Filter (NaN-aware rolling median, vectorized — #363)
    # The prior implementation was a per-sample Python loop that sliced a
    # MEDIAN_FILTER_WINDOW-wide window, stripped NaNs, and called np.median —
    # ~n iterations dominated by dispatch overhead. The vectorized form is
    # exactly equivalent: NaN-pad the series by half_window on each side so
    # every output index has a full window, build all windows in one C call
    # with sliding_window_view, then take the NaN-ignoring median per row.
    #  - Interior dropout NaNs are stripped by nanmedian just as the loop's
    #    valid_window stripped them.
    #  - The NaN edge padding reproduces the loop's shrinking edge window (the
    #    out-of-range samples the loop omitted are NaN here, and nanmedian
    #    ignores them), so edge outputs are identical.
    #  - An all-NaN window yields NaN (a preserved gap), matching the loop's
    #    len(valid_window) == 0 branch; nanmedian's expected All-NaN
    #    RuntimeWarning for that case is suppressed.
    half_window = MEDIAN_FILTER_WINDOW // 2
    padded = np.pad(cad_arr, half_window, mode="constant", constant_values=np.nan)
    windows = np.lib.stride_tricks.sliding_window_view(padded, MEDIAN_FILTER_WINDOW)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        smoothed = np.nanmedian(windows, axis=1)

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
