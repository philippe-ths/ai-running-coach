from app.services.units.cadence import normalize_cadence_spm
from app.services.ai.context_pack import CPActivity

def test_regression_cadence_plausible():
    """
    Ensure that normalizing a typical Strava cadence (which might be strides/min)
    results in a plausible SPM value, and doesn't double an already SPM value.
    """
    # Case 1: Typical Strava "raw" cadence (strides/min) -> Needs doubling
    run_raw = 82.0
    normalized_run = normalize_cadence_spm("Run", run_raw)
    assert 160 < normalized_run < 170
    assert normalized_run == 164.0

    # Case 2: Already SPM (e.g. from a different device/source or already fixed)
    # The normalizer threshold is 130. 
    # If we feed it 164.0, it should NOT become 328.0
    already_spm = 164.0
    normalized_again = normalize_cadence_spm("Run", already_spm)
    assert normalized_again == 164.0

    # Case 3: Verify ContextPack structure (typing verification mainly)
    # Docs say it must be SPM.
    activity = CPActivity(
        id="test",
        start_time="2023-01-01T00:00:00",
        type="Run",
        name="Test",
        distance_m=1000,
        moving_time_s=300,
        elapsed_time_s=300,
        avg_cadence=normalized_run
    )
    assert activity.avg_cadence == 164.0
