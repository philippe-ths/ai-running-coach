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


# --- #176: a confounder only warrants discounting when the drift is elevated ---

def test_low_drift_with_confounder_returns_none():
    # #176: a confounder is present (terrain) but the drift is negligible, so
    # there is nothing to discount. The stage must NOT hand the coach a "discount
    # this drift" instruction it would then contradict ("drift was just 0.8%").
    result = compute_discount_signals(
        average_temp=15.0, is_hilly=True, hr_drift=0.8, stimulant_use=False
    )
    assert result is None


def test_low_drift_unknown_temp_returns_none():
    # #176: a negligible drift needs no "treat cautiously" caution either, even
    # when temperature is unknown — there is nothing to be cautious about.
    result = compute_discount_signals(
        average_temp=None, is_hilly=False, hr_drift=0.8, stimulant_use=False
    )
    assert result is None


def test_drift_at_threshold_with_confounder_returns_none():
    # Boundary: 5.0% is NOT elevated (flags.py marks fatigue only on drift > 5%),
    # so a confounder at exactly the threshold still has nothing to discount.
    result = compute_discount_signals(
        average_temp=15.0, is_hilly=True, hr_drift=5.0, stimulant_use=False
    )
    assert result is None


def test_elevated_drift_with_confounder_still_fires():
    # The gate must not over-suppress: a drift above the threshold with a
    # confounder still produces the discount annotation.
    result = compute_discount_signals(
        average_temp=15.0, is_hilly=True, hr_drift=6.0, stimulant_use=False
    )
    assert result is not None
    assert result["likely_inflated_by"] == ["terrain"]
    assert result["hr_drift_pct"] == 6.0
