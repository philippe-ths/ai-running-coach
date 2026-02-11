from typing import Dict, List, Any, Optional

def analyze_stops(streams: Dict[str, List[Any]]) -> Optional[Dict[str, Any]]:
    """
    Analyzes stops based on the 'moving' stream.
    Moving stream: boolean (True=moving, False=stopped).
    Time stream: seconds from start.
    Latlng: [lat, lng].
    Distance: meters from start.
    """
    moving = streams.get("moving")
    time_stream = streams.get("time")
    latlng = streams.get("latlng")
    distance_stream = streams.get("distance")

    if not moving or not time_stream:
        # Can't analyze stops without moving/time data
        return None

    if len(moving) != len(time_stream):
        # Mismatched streams
        return None

    stops = []
    
    current_stop_start_idx = None

    for i, is_moving in enumerate(moving):
        if not is_moving:
            if current_stop_start_idx is None:
                current_stop_start_idx = i
        else:
            if current_stop_start_idx is not None:
                # Stop ended at the previous index (i-1)
                stop_end_idx = i - 1
                
                # Minimum valid duration check (e.g. > 0s)
                # Durations in time stream
                start_time = time_stream[current_stop_start_idx]
                end_time = time_stream[stop_end_idx]
                duration = end_time - start_time
                
                if duration > 0:
                    location = None
                    if latlng and len(latlng) > current_stop_start_idx:
                        location = latlng[current_stop_start_idx]
                    
                    dist = None
                    if distance_stream and len(distance_stream) > current_stop_start_idx:
                        dist = distance_stream[current_stop_start_idx]

                    stops.append({
                        "start_time": start_time,
                        "duration_s": duration,
                        "location": location,
                        "distance_m": dist
                    })
                
                current_stop_start_idx = None
    
    # Handle stop at the very end
    if current_stop_start_idx is not None:
        stop_end_idx = len(time_stream) - 1
        start_time = time_stream[current_stop_start_idx]
        end_time = time_stream[stop_end_idx]
        duration = end_time - start_time
        
        if duration > 0:
             location = None
             if latlng and len(latlng) > current_stop_start_idx:
                 location = latlng[current_stop_start_idx]
             
             dist = None
             if distance_stream and len(distance_stream) > current_stop_start_idx:
                 dist = distance_stream[current_stop_start_idx]

             stops.append({
                "start_time": start_time,
                "duration_s": duration,
                "location": location,
                "distance_m": dist
            })

    if not stops:
        return {
            "total_stopped_time_s": 0,
            "stopped_count": 0,
            "longest_stop_s": 0,
            "stops": []
        }

    total_stopped_time = sum(s["duration_s"] for s in stops)
    longest_stop = max(s["duration_s"] for s in stops)

    return {
        "total_stopped_time_s": total_stopped_time,
        "stopped_count": len(stops),
        "longest_stop_s": longest_stop,
        "stops": stops
    }
