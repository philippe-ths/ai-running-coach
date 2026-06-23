from app.services.units.cadence import (
    CADENCE_SINGLE_LEG_THRESHOLD_SPM,
    cadence_doubling_factor,
    normalize_cadence_spm,
)


def test_cadence_doubling_factor_threshold():
    """#442: the single source of the per-leg rule. Below the threshold -> 2
    (single leg), at or above -> 1, and a missing value -> 1."""
    assert cadence_doubling_factor(CADENCE_SINGLE_LEG_THRESHOLD_SPM - 0.1) == 2
    assert cadence_doubling_factor(CADENCE_SINGLE_LEG_THRESHOLD_SPM) == 1
    assert cadence_doubling_factor(CADENCE_SINGLE_LEG_THRESHOLD_SPM + 0.1) == 1
    assert cadence_doubling_factor(None) == 1


def test_normalize_cadence_spm_is_the_shared_factor_applied():
    """#442: normalize_cadence_spm is exactly the scalar application of the shared
    per-leg factor, so the read path and the stream-view builder cannot drift."""
    for v in (60.0, 79.1, 85.0, 129.9, 130.0, 168.0, 190.0):
        assert normalize_cadence_spm("Run", v) == v * cadence_doubling_factor(v)


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
