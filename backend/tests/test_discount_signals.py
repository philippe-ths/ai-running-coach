"""Tests for the deterministic confounder stage (N4).

Hand-authored fixtures pin the exact templated contract: which confounders are
named, the confidence floor when temperature is unknown, and the cases where no
annotation should be emitted at all.
"""

from app.services.analysis.discount_signals import compute_discount_signals


def test_heat_terrain_stimulant_all_named_high_confidence():
    result = compute_discount_signals(
        average_temp=28.0, is_hilly=True, hr_drift=8.0, stimulant_use=True
    )
    assert result is not None
    assert result["likely_inflated_by"] == ["heat", "terrain", "stimulant"]
    assert result["confidence"] == "high"
    assert result["hr_drift_pct"] == 8.0
    assert result["temperature_c"] == 28.0
    assert result["interpretation"] == (
        "This HR drift is likely inflated by heat, terrain, stimulant; "
        "discount it as a fatigue signal."
    )


def test_unknown_temperature_yields_empty_inflators_low_confidence():
    result = compute_discount_signals(
        average_temp=None, is_hilly=False, hr_drift=6.0, stimulant_use=False
    )
    assert result is not None
    assert result["likely_inflated_by"] == []
    assert result["temperature_c"] is None
    assert result["confidence"] == "low"
    assert result["hr_drift_pct"] == 6.0
    assert result["interpretation"] == (
        "Temperature was not recorded, so heat inflation of this HR drift "
        "cannot be ruled out; treat the drift cautiously."
    )


def test_no_drift_returns_none():
    result = compute_discount_signals(
        average_temp=30.0, is_hilly=True, hr_drift=None, stimulant_use=True
    )
    assert result is None


def test_clean_run_with_known_temp_is_trustworthy_returns_none():
    result = compute_discount_signals(
        average_temp=15.0, is_hilly=False, hr_drift=5.0, stimulant_use=False
    )
    assert result is None


def test_heat_only_high_confidence():
    result = compute_discount_signals(
        average_temp=30.0, is_hilly=False, hr_drift=7.0, stimulant_use=False
    )
    assert result is not None
    assert result["likely_inflated_by"] == ["heat"]
    assert result["confidence"] == "high"
    assert result["temperature_c"] == 30.0
    assert result["hr_drift_pct"] == 7.0
    assert result["interpretation"] == (
        "This HR drift is likely inflated by heat; "
        "discount it as a fatigue signal."
    )
