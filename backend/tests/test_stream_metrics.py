from app.services.analysis.metrics import calculate_hr_drift, calculate_pace_variability

def test_hr_drift_calculation():
    # 10 data points, first half efficient, second half inefficient (HR spikes for same speed)
    # Speed constant = 3.0 m/s
    # HR first half = 150, second half = 165
    # Just need arrays
    velocity = [3.0] * 1000 # 1000 points
    hr = [150] * 500 + [165] * 500 # Spikes in 2nd half
    
    streams = {"velocity_smooth": velocity, "heartrate": hr}
    
    drift = calculate_hr_drift(streams)
    assert drift is not None
    assert drift > 0 # Should be positive drift
    # EF1 = 3/150 = 0.02
    # EF2 = 3/165 = 0.01818
    # Drift = (0.02 - 0.01818)/0.02 = ~9%
    assert 8.0 < drift < 10.0

def test_pace_variability():
    # Steady state
    velocity = [3.0, 3.0, 3.1, 2.9, 3.0] * 20
    streams = {"velocity_smooth": velocity}
    cv = calculate_pace_variability(streams)
    assert cv is not None
    assert cv < 5.0 # Low variability

    # Erratic
    velocity_bad = [2.0, 4.0, 2.0, 4.0] * 20
    streams_bad = {"velocity_smooth": velocity_bad}
    cv_bad = calculate_pace_variability(streams_bad)
    assert cv_bad > 20.0

def test_calculate_time_in_zones():
    from app.services.analysis.metrics import calculate_time_in_zones
    
    # Max HR = 200
    # Zones:
    # Z1 (50-60%): 100-120
    # Z2 (60-70%): 120-140
    # Z3 (70-80%): 140-160
    # Z4 (80-90%): 160-180
    # Z5 (90-100%): 180-200
    
    # Data
    # 3x Z1 (110)
    # 2x Z2 (130)
    # 1x Z5 (190)
    streams = {
        "heartrate": [110, 110, 110, 130, 130, 190, 40] # 40 should be ignored (<50%) or counted as 0 index
    }
    
    zones = calculate_time_in_zones(streams, max_hr=200)
    
    assert zones is not None
    assert zones["Z1"] == 3
    assert zones["Z2"] == 2
    assert zones["Z3"] == 0
    assert zones["Z4"] == 0
    assert zones["Z5"] == 1
    # Total sum check
    assert sum(zones.values()) == 6
