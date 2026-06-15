from app.models import Activity, CheckIn
from app.services.analysis.classifier import (
    Classification,
    classify_activity,
    compose_headline,
    compute_effort,
    playbook_key,
)
from app.services.analysis.metrics import compute_training_load
from app.services.analysis.flags import generate_flags


def _run(**kwargs):
    kwargs.setdefault("type", "Run")
    return Activity(**kwargs)


# --- Effort axis (ADR 0007) ---

def test_effort_from_dominant_zone():
    # Mostly Z4 -> tempo.
    assert compute_effort({"Z1": 0, "Z2": 10, "Z3": 20, "Z4": 200, "Z5": 10}) == "tempo"


def test_effort_z5_escalation_to_hard():
    # >=15% in Z5 escalates to hard even when Z4 is dominant.
    assert compute_effort({"Z3": 10, "Z4": 60, "Z5": 30}) == "hard"


def test_effort_easy_when_z2_dominant():
    assert compute_effort({"Z1": 5, "Z2": 200, "Z3": 20}) == "easy"


def test_effort_fallback_to_pct_max_without_zones():
    # No zones: fall back to average %max.
    assert compute_effort(None, avg_hr=171, max_hr=190) == "hard"   # 90%
    assert compute_effort(None, avg_hr=133, max_hr=190) == "moderate"  # 70%
    assert compute_effort({}, avg_hr=114, max_hr=190) == "easy"  # 60%


def test_effort_unknown_without_any_hr():
    assert compute_effort(None, avg_hr=None, max_hr=None) is None


# --- Structure axis ---

def test_intervals_need_structure_and_variability():
    # Detected structure + high pace variability -> intervals.
    act = _run(name="Afternoon Run", distance_m=3660, moving_time_s=1200)
    c = classify_activity(act, [], time_in_zones={"Z4": 100, "Z5": 50},
                          pace_variability=30.0, has_interval_structure=True)
    assert c.structure == "intervals"


def test_steady_run_with_structure_is_not_intervals():
    # The 05-24 false-positive guard: structure detected but low pace variability.
    act = _run(name="Afternoon Run", distance_m=5480, moving_time_s=2067)
    c = classify_activity(act, [], time_in_zones={"Z4": 200},
                          pace_variability=15.2, has_interval_structure=True)
    assert c.structure == "continuous"


# --- Duration axis ---

def test_long_run_relative_below_absolute_floor():
    # 41 min run vs a history of ~20 min runs -> long, despite being under 75 min.
    history = [_run(moving_time_s=d) for d in (1260, 1260, 1260, 1300)]
    act = _run(name="Afternoon Run", distance_m=7000, moving_time_s=2460)
    assert classify_activity(act, history).duration_class == "long"


def test_long_run_ignores_non_run_history():
    # Long walks must not inflate the comparison and hide a real long run.
    history = [Activity(type="Walk", moving_time_s=3000) for _ in range(4)] + \
              [_run(moving_time_s=1260) for _ in range(4)]
    act = _run(name="Afternoon Run", distance_m=7000, moving_time_s=2460)
    assert classify_activity(act, history).duration_class == "long"


def test_duration_needs_enough_history():
    history = [_run(moving_time_s=1260), _run(moving_time_s=1260)]
    act = _run(name="Afternoon Run", distance_m=7000, moving_time_s=2460)
    assert classify_activity(act, history).duration_class == "standard"


def test_moderately_longer_run_under_floor_is_standard():
    # Longest recent run but under the 30 min sanity floor -> standard.
    history = [_run(moving_time_s=1100) for _ in range(3)]
    act = _run(name="Afternoon Run", distance_m=4500, moving_time_s=1700)
    assert classify_activity(act, history).duration_class == "standard"


def test_absolute_floor_long_run():
    act = _run(name="Sunday Run", moving_time_s=5000)  # ~83 min
    assert classify_activity(act, []).duration_class == "long"


# --- Terrain axis ---

def test_hilly_when_gain_per_km_high():
    act = _run(name="Lunch Run", distance_m=5000, elev_gain_m=150, moving_time_s=1800)
    assert classify_activity(act, []).is_hilly is True


def test_flat_when_low_gain():
    act = _run(name="Lunch Run", distance_m=5000, elev_gain_m=20, moving_time_s=1800)
    assert classify_activity(act, []).is_hilly is False


# --- Sport routing / non-run handling ---

def test_non_run_gets_effort_only():
    ride = Activity(type="Ride", distance_m=20000, moving_time_s=3600, avg_hr=150, max_hr=190)
    c = classify_activity(ride, [], time_in_zones={"Z4": 100})
    assert c.duration_class is None and c.structure is None and c.is_hilly is None
    assert c.effort == "tempo"


def test_non_run_never_intervals_even_with_structure():
    walk = Activity(type="Walk", distance_m=4000, moving_time_s=2700)
    c = classify_activity(walk, [], pace_variability=30.0, has_interval_structure=True)
    assert c.structure is None


def test_race_from_name():
    act = _run(name="Parkrun Race", moving_time_s=1200)
    assert classify_activity(act, []).is_race is True


# --- Headline composition ---

def test_headline_long_tempo():
    act = _run()
    c = Classification(effort="tempo", duration_class="long", structure="continuous", is_hilly=False)
    assert compose_headline(act, c) == "Long run (tempo)"


def test_headline_intervals_hard():
    act = _run()
    c = Classification(effort="hard", duration_class="standard", structure="intervals", is_hilly=False)
    assert compose_headline(act, c) == "Intervals (hard)"


def test_headline_hard_run():
    act = _run()
    c = Classification(effort="hard", duration_class="standard", structure="continuous", is_hilly=False)
    assert compose_headline(act, c) == "Hard run"


def test_headline_hilly_prefix():
    act = _run()
    c = Classification(effort="hard", duration_class="standard", structure="continuous", is_hilly=True)
    assert compose_headline(act, c) == "Hilly hard run"


def test_headline_non_run_sport():
    ride = Activity(type="Ride", distance_m=20000)
    c = Classification(effort="hard")
    assert compose_headline(ride, c) == "Ride (hard)"


def test_headline_falls_back_to_sport_without_classification():
    assert compose_headline(Activity(type="Walk"), None) == "Walk"


# --- Playbook selection ---

def test_playbook_key_intervals():
    c = Classification(structure="intervals", duration_class="standard", effort="hard")
    assert playbook_key(_run(), c) == "Intervals"


def test_playbook_key_long_over_effort():
    c = Classification(structure="continuous", duration_class="long", effort="tempo")
    assert playbook_key(_run(), c) == "Long Run"


def test_playbook_key_non_run_is_none():
    assert playbook_key(Activity(type="Ride"), Classification(effort="hard")) is None


# --- Training-load primitive (#186): see test_training_load_primitive.py for
# the full tier coverage; this keeps a smoke check on the legacy call shape. ---

def test_training_load_primitive_smoke():
    # No HR -> Tier C default aerobic band (zone 2): 60 min × 2 = 120.
    assert compute_training_load(3600) == 120.0

    # Summary HR against the athlete max (190): 150/190 = 0.79 -> Z3 -> 60 × 3.
    assert compute_training_load(3600, avg_hr=150, athlete_max_hr=190) == 180.0


# --- Flags ---

def test_flags_pain_reported():
    act = Activity(avg_hr=140)
    checkin = CheckIn(pain_score=5)
    flags = generate_flags(act, {"effort": "easy"}, [], checkin)
    assert "pain_reported" in flags
    assert "pain_severe" not in flags


def test_flags_intensity_mismatch_intent_vs_executed():
    # Stated easy, executed hard -> mismatch (ADR 0007).
    act = Activity(avg_hr=180, max_hr=200, user_intent="Easy Run")
    flags = generate_flags(act, {"effort": "hard"}, [], None)
    assert "intensity_mismatch" in flags


def test_flags_no_intensity_mismatch_without_stated_intent():
    # A hard run with no stated intent is just hard, not a mismatch.
    act = Activity(avg_hr=180, max_hr=200)
    flags = generate_flags(act, {"effort": "hard"}, [], None)
    assert "intensity_mismatch" not in flags


def test_flags_data_low_confidence_hr():
    act = Activity(avg_hr=None)
    flags = generate_flags(act, {"effort": None}, [], None)
    assert "data_low_confidence_hr" in flags


def test_flags_fatigue_possible():
    act = Activity(avg_hr=150)
    flags = generate_flags(act, {"hr_drift": 7.0}, [], None)
    assert "fatigue_possible" in flags


def test_flags_pace_unstable():
    act = Activity(avg_hr=150)
    flags = generate_flags(
        act, {"structure": "continuous", "effort": "tempo", "pace_variability": 18.0}, [], None
    )
    assert "pace_unstable" in flags


def test_flags_pace_stable_for_intervals():
    # Intervals are variable by design -> no pace_unstable flag.
    act = Activity(avg_hr=150)
    flags = generate_flags(
        act, {"structure": "intervals", "effort": "hard", "pace_variability": 30.0}, [], None
    )
    assert "pace_unstable" not in flags


def test_flags_pain_severe():
    act = Activity(avg_hr=140)
    checkin = CheckIn(pain_score=8)
    flags = generate_flags(act, {"effort": "easy"}, [], checkin)
    assert "pain_reported" in flags
    assert "pain_severe" in flags


def test_flags_illness_or_extreme_fatigue():
    act = Activity(avg_hr=140)
    checkin = CheckIn(rpe=9, sleep_quality=1, pain_score=6)
    flags = generate_flags(act, {"effort": "easy"}, [], checkin)
    assert "illness_or_extreme_fatigue" in flags


def test_classifier_intent_does_not_overwrite_measured_axes():
    # Intent is separate from the measured axes (ADR 0007): a run the user calls
    # "Easy" still has its true measured effort/structure.
    act = _run(name="Slow Jog", distance_m=5000, moving_time_s=1200, user_intent="Easy Run")
    c = classify_activity(act, [], time_in_zones={"Z4": 100, "Z5": 50}, pace_variability=5.0)
    assert c.effort == "hard"  # measured, not overridden by the "Easy" intent
