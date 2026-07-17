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


def test_calculate_time_in_zones_uses_runner_boundaries():
    """#297: when the runner's own Strava zone bounds are supplied, binning
    follows those boundaries instead of the %-of-max-HR scheme, so the result
    lines up with Strava. Oracle: the runner's real Strava zones
    0-124 / 125-154 / 155-169 / 170-184 / 185+ (lower bounds below)."""
    from app.services.analysis.metrics import calculate_time_in_zones

    bounds = [0, 125, 155, 170, 185]
    # One reading squarely inside each Strava zone, plus a boundary value (155)
    # that must land in Z3 (Strava Z3 is 155-169).
    streams = {"heartrate": [120, 140, 155, 160, 175, 190]}

    zones = calculate_time_in_zones(streams, max_hr=190, zone_boundaries=bounds)

    assert zones is not None
    assert zones["Z1"] == 1   # 120  -> 0-124
    assert zones["Z2"] == 1   # 140  -> 125-154
    assert zones["Z3"] == 2   # 155, 160 -> 155-169
    assert zones["Z4"] == 1   # 175  -> 170-184
    assert zones["Z5"] == 1   # 190  -> 185+
    assert sum(zones.values()) == 6


def test_runner_boundaries_reclassify_aerobic_run_below_strava_threshold():
    """#297 regression: a run sitting at 155-169 bpm read as Z4 under the old
    %max scheme (Z4 floor 152 at max 190) but is Z3 on Strava. With the runner's
    bounds it must land in Z3, not Z4."""
    from app.services.analysis.metrics import calculate_time_in_zones

    bounds = [0, 125, 155, 170, 185]
    streams = {"heartrate": [160] * 100}  # steady aerobic effort

    with_bounds = calculate_time_in_zones(streams, max_hr=190, zone_boundaries=bounds)
    fallback = calculate_time_in_zones(streams, max_hr=190)

    assert with_bounds["Z3"] == 100 and with_bounds["Z4"] == 0
    # The old/fallback scheme misfiles the same effort as Z4.
    assert fallback["Z4"] == 100


def test_extract_hr_zone_bounds_parses_strava_payload():
    """#297: pull the 5 ascending HR-zone lower bounds from a Strava
    /athlete/zones payload (real shape: heart_rate.zones with min/max)."""
    from app.services.analysis.zones import extract_hr_zone_bounds

    payload = {
        "heart_rate": {
            "custom_zones": False,
            "zones": [
                {"min": 0, "max": 124},
                {"min": 125, "max": 154},
                {"min": 155, "max": 169},
                {"min": 170, "max": 184},
                {"min": 185, "max": -1},
            ],
        },
        "power": {"zones": [{"min": 0, "max": 100}]},
    }

    assert extract_hr_zone_bounds(payload) == [0, 125, 155, 170, 185]


def test_extract_hr_zone_bounds_rejects_bad_shapes():
    """Off-shape payloads return None so analysis falls back to the %max scheme
    rather than binning against garbage."""
    from app.services.analysis.zones import extract_hr_zone_bounds

    assert extract_hr_zone_bounds(None) is None
    assert extract_hr_zone_bounds({}) is None
    assert extract_hr_zone_bounds({"heart_rate": {"zones": []}}) is None
    # Only 4 zones.
    assert extract_hr_zone_bounds(
        {"heart_rate": {"zones": [{"min": 0}, {"min": 1}, {"min": 2}, {"min": 3}]}}
    ) is None
    # Non-ascending bounds.
    assert extract_hr_zone_bounds(
        {"heart_rate": {"zones": [{"min": 0}, {"min": 9}, {"min": 5}, {"min": 7}, {"min": 8}]}}
    ) is None
