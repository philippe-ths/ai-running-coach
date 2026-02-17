from datetime import datetime
from app.models import Activity, CheckIn
from app.services.processing.classifier import classify_activity
from app.services.processing.metrics import calculate_effort_score
from app.services.processing.flags import generate_flags

def test_classifier_long_run():
    # Long run > 75 mins
    act = Activity(name="Sunday Run", moving_time_s=5000) # ~83 mins
    classification = classify_activity(act, [])
    assert classification == "Long Run"

def test_classifier_intervals_by_name():
    act = Activity(name="Morning Intervals 8x400", moving_time_s=1800)
    classification = classify_activity(act, [])
    assert classification == "Intervals"

def test_classifier_default_easy():
    act = Activity(name="Morning Jog", moving_time_s=1800, distance_m=5000, elev_gain_m=0)
    classification = classify_activity(act, [])
    assert classification == "Easy Run"

def test_effort_score_calculation():
    # No HR
    act = Activity(moving_time_s=3600, avg_hr=None)
    score = calculate_effort_score(act)
    assert score == 60.0 # 60 mins

    # With HR
    act_hr = Activity(moving_time_s=3600, avg_hr=150, max_hr=200)
    score_hr = calculate_effort_score(act_hr)
    assert score_hr > 200

def test_flags_pain_reported():
    act = Activity(avg_hr=140)
    checkin = CheckIn(pain_score=5)
    flags = generate_flags(act, {"activity_class": "Easy Run"}, [], checkin)
    assert "pain_reported" in flags
    assert "pain_severe" not in flags

def test_flags_intensity_mismatch():
    # Easy run but HR is high (90% max)
    act = Activity(avg_hr=180, max_hr=200)
    flags = generate_flags(act, {"activity_class": "Easy Run"}, [], None)
    assert "intensity_mismatch" in flags

def test_flags_data_low_confidence_hr():
    # No HR data
    act = Activity(avg_hr=None)
    flags = generate_flags(act, {"activity_class": "Easy Run"}, [], None)
    assert "data_low_confidence_hr" in flags

def test_flags_fatigue_possible():
    act = Activity(avg_hr=150)
    flags = generate_flags(act, {"activity_class": "Tempo", "hr_drift": 7.0}, [], None)
    assert "fatigue_possible" in flags

def test_flags_pace_unstable():
    act = Activity(avg_hr=150)
    flags = generate_flags(act, {"activity_class": "Tempo", "pace_variability": 18.0}, [], None)
    assert "pace_unstable" in flags

def test_flags_pain_severe():
    act = Activity(avg_hr=140)
    checkin = CheckIn(pain_score=8)
    flags = generate_flags(act, {"activity_class": "Easy Run"}, [], checkin)
    assert "pain_reported" in flags
    assert "pain_severe" in flags

def test_flags_illness_or_extreme_fatigue():
    act = Activity(avg_hr=140)
    checkin = CheckIn(rpe=9, sleep_quality=1, pain_score=6)
    flags = generate_flags(act, {"activity_class": "Easy Run"}, [], checkin)
    assert "illness_or_extreme_fatigue" in flags

def test_classifier_hills():
    # 5km run with 150m gain = 30m/km -> Hills
    act = Activity(name="Lunch Run", distance_m=5000, elev_gain_m=150, moving_time_s=1800)
    classification = classify_activity(act, [])
    assert classification == "Hills"

def test_classifier_hills_with_hr():
    # 5km run with 80m gain = 16m/km (borderline) + High HR
    act = Activity(
        name="Lunch Run",
        distance_m=5000,
        elev_gain_m=80,
        moving_time_s=1800,
        avg_hr=165 # High effort
    )
    classification = classify_activity(act, [])
    assert classification == "Hills"

def test_classifier_intent_override():
    # Data looks like Easy Run, but User says 'Tempo'
    act = Activity(
        name="Slow Jog",
        distance_m=5000,
        elev_gain_m=10,
        moving_time_s=2500,
        user_intent="Tempo"
    )
    classification = classify_activity(act, [])
    assert classification == "Tempo"
