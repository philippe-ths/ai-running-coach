"""Unit tests for the per-activity training-load primitive (#186).

The primitive is Edwards-style ``zone-minutes``: ``load = Σ (minutes in zone z) × z``
where ``z`` is the HR zone 1-5 (or its best per-tier estimate). The same scale
holds whether or not the activity has heart-rate data, so values are comparable
and summable across a mixed history (the property the acute/chronic model needs).

Tiers, in precedence order:
  A. HR streams present -> a real per-sample zone distribution (Edwards weighting).
  B. summary HR only (avg_hr, no zones) -> the zone the AVERAGE HR falls in,
     against the ATHLETE max (never the activity's recorded peak).
  C. no HR -> session-RPE when a check-in exists, else a documented default
     aerobic weight (zone 2).
"""

from app.services.analysis.metrics import compute_training_load, TRIMP_DEFAULT_BAND


# --- Tier A: HR zone distribution (Edwards weighting) ---

def test_tier_a_single_zone():
    # 30 minutes entirely in Z2 -> 30 min × weight 2 = 60 zone-minutes.
    zones = {"Z1": 0, "Z2": 1800, "Z3": 0, "Z4": 0, "Z5": 0}
    assert compute_training_load(1800, time_in_zones=zones) == 60.0


def test_tier_a_mixed_zones_are_weighted_by_zone_number():
    # 10 min Z1 + 10 min Z2 + 10 min Z3 = 10·1 + 10·2 + 10·3 = 60 zone-minutes.
    zones = {"Z1": 600, "Z2": 600, "Z3": 600, "Z4": 0, "Z5": 0}
    assert compute_training_load(1800, time_in_zones=zones) == 60.0


def test_tier_a_harder_session_scores_higher_per_minute():
    # Same 30 minutes, but in Z4 -> 30 × 4 = 120, strictly above the Z2 run.
    easy = compute_training_load(1800, time_in_zones={"Z1": 0, "Z2": 1800, "Z3": 0, "Z4": 0, "Z5": 0})
    hard = compute_training_load(1800, time_in_zones={"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 1800, "Z5": 0})
    assert hard == 120.0
    assert hard > easy


def test_tier_a_takes_precedence_over_avg_hr_and_rpe():
    zones = {"Z1": 0, "Z2": 1800, "Z3": 0, "Z4": 0, "Z5": 0}
    # avg_hr/rpe would land elsewhere; zones win.
    assert compute_training_load(1800, time_in_zones=zones, avg_hr=180, athlete_max_hr=190, rpe=10) == 60.0


def test_tier_a_sub_zone_time_counts_at_weight_one():
    # 30-min run: 10 min hard (Z4) + 20 min below Z1 (HR gaps / <50% max).
    # 10·4 + 20·1 = 60 zone-minutes; the sub-zone time is not discarded.
    zones = {"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 600, "Z5": 0}
    assert compute_training_load(1800, time_in_zones=zones) == 60.0


def test_tier_a_mostly_sub_zone_run_does_not_collapse_to_zero():
    # The real-data failure: a 26-min easy run whose HR sat mostly below 50% of
    # an uncalibrated max. Only 1 min registered in Z1; the rest must still count
    # (1·1 + 25·1 = 26), not vanish to ~0 as raw Edwards exclusion would give.
    zones = {"Z1": 60, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0}
    assert compute_training_load(1560, time_in_zones=zones) == 26.0


def test_tier_a_every_minute_is_worth_at_least_one_load_unit():
    zones = {"Z1": 120, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0}  # 2 min Z1 of a 40-min run
    load = compute_training_load(2400, time_in_zones=zones)
    assert load >= 40.0  # 40 moving minutes -> at least 40 load units


def test_tier_a_all_zero_zones_falls_through():
    # An all-zero (or HR-less stream) zone dict is not a Tier-A signal.
    zones = {"Z1": 0, "Z2": 0, "Z3": 0, "Z4": 0, "Z5": 0}
    # Falls through to the default (no avg_hr, no rpe): 30 min × default band.
    assert compute_training_load(1800, time_in_zones=zones) == round(30.0 * TRIMP_DEFAULT_BAND, 1)


# --- Tier B: summary HR only, athlete-max denominator ---

def test_tier_b_uses_band_of_average_hr():
    # avg 150 / athlete-max 190 = 0.789 -> Z3 (70-80%) -> 60 min × 3 = 180.
    assert compute_training_load(3600, avg_hr=150, athlete_max_hr=190) == 180.0


def test_tier_b_easy_run_band_two():
    # avg 130 / 190 = 0.684 -> Z2 -> 60 × 2 = 120.
    assert compute_training_load(3600, avg_hr=130, athlete_max_hr=190) == 120.0


def test_tier_b_denominator_is_athlete_max_not_activity_peak():
    # The old bug divided by the activity's recorded peak (~0.85-0.97 always).
    # With the athlete max as denominator, the same avg_hr lands in a real band.
    against_athlete_max = compute_training_load(3600, avg_hr=150, athlete_max_hr=190)  # 0.789 -> band 3
    against_activity_peak = compute_training_load(3600, avg_hr=150, athlete_max_hr=155)  # 0.968 -> band 5
    assert against_athlete_max == 180.0
    assert against_activity_peak == 300.0
    assert against_athlete_max < against_activity_peak


# --- Tier C: no HR ---

def test_tier_c_uses_session_rpe_when_present():
    # RPE 8 -> band 4 (mirrors perceived_effort._rpe_band) -> 60 × 4 = 240.
    assert compute_training_load(3600, rpe=8) == 240.0


def test_tier_c_rpe_low():
    # RPE 3 -> band 2 -> 60 × 2 = 120.
    assert compute_training_load(3600, rpe=3) == 120.0


def test_tier_c_default_aerobic_weight_without_any_hr_or_rpe():
    # No HR, no RPE: assume population-typical aerobic intensity (zone 2).
    assert compute_training_load(3600, ) == 120.0
    assert TRIMP_DEFAULT_BAND == 2


# --- The comparability acceptance criterion (#186 AC3) ---

def test_same_easy_effort_scores_the_same_with_and_without_hr():
    # A 60-minute easy (Z2) run measured by HR vs the identical run with no strap
    # must produce comparable load. Old code gave 368 vs 60 (a ~6× gap).
    with_hr = compute_training_load(3600, time_in_zones={"Z1": 0, "Z2": 3600, "Z3": 0, "Z4": 0, "Z5": 0})
    no_hr = compute_training_load(3600)  # Tier C default = easy aerobic
    assert with_hr == no_hr == 120.0


def test_load_is_summable_across_a_mixed_history():
    # Three 30-min easy runs, one HR-measured, one summary-HR, one strap-less,
    # should each contribute the same ~easy load so the sum is meaningful.
    a = compute_training_load(1800, time_in_zones={"Z1": 0, "Z2": 1800, "Z3": 0, "Z4": 0, "Z5": 0})
    b = compute_training_load(1800, avg_hr=125, athlete_max_hr=190)  # 0.66 -> band 2
    c = compute_training_load(1800)  # default band 2
    assert a == b == c == 60.0
    assert a + b + c == 180.0


# --- Guards ---

def test_zero_or_missing_duration_is_zero_load():
    assert compute_training_load(0) == 0.0
    assert compute_training_load(None) == 0.0
