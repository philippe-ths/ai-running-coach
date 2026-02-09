from app.services.units.cadence import normalize_cadence_spm

def test_normalize_cadence_spm_run_doubling():
    """Test that runs with low cadence (strides/min) are doubled to steps/min."""
    assert normalize_cadence_spm("Run", 79.1) == 158.2
    assert normalize_cadence_spm("Run", 85.0) == 170.0
    assert normalize_cadence_spm("Run", 60.0) == 120.0

def test_normalize_cadence_spm_run_unchanged():
    """Test that runs with normal SPM are unchanged."""
    assert normalize_cadence_spm("Run", 168.0) == 168.0
    assert normalize_cadence_spm("Run", 130.0) == 130.0
    assert normalize_cadence_spm("Run", 190.0) == 190.0

def test_normalize_cadence_spm_non_run_doubling():
    """Test that non-run activities are doubled if low, per user request."""
    # Assuming the logic is simply "if < 130, double it" for everyone.
    # Examples:
    # Walk: 50 spm reported -> 100 spm real.
    # Ride: 80 rpm reported -> 160 units? (User said 'always be doubled')
    assert normalize_cadence_spm("Walk", 50.0) == 100.0
    assert normalize_cadence_spm("Ride", 80.0) == 160.0
    assert normalize_cadence_spm("Hike", 50.0) == 100.0

def test_normalize_cadence_spm_none():
    """Test that None input returns None."""
    assert normalize_cadence_spm("Run", None) is None
    assert normalize_cadence_spm("Ride", None) is None
