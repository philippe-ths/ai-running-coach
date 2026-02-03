from datetime import datetime
from app.models import Activity, CheckIn
from app.services.analysis.classifier import classify_activity
from app.services.analysis.metrics import calculate_effort_score
from app.services.analysis.flags import generate_flags

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
    # Ratio = 0.75. Score = 60 * 0.75^3 * 10 = 60 * 0.4218 * 10 = ~253
    # Wait, my math was: 60 * (0.75^3) * 10 = 60 * 0.421875 * 10 = 253.1
    # Actually 150/200 = 0.75
    # 0.75^3 = 0.421875
    # 60 * 0.421875 = 25.3125
    # * 10 = 253.1
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
    assert "intensity_too_high_for_easy" in flags

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
