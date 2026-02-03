import sys
import os
from app.services.ai.verdict_v3.generators import generate_scorecard
from app.services.ai.client import ai_client

# Mock Env
os.environ["AI_ENABLED"] = "True"
os.environ["AI_PROVIDER"] = "openai"
from dotenv import load_dotenv
load_dotenv()

def test_empty_metrics():
    print("Testing Scorecard Generation with EMPTY metrics...")

    # Mimic a manual activity with no streams
    cp_dict = {
        "activity": {
            "id": "mock-uuid",
            "type": "Run",
            "name": "Manual Run",
            "distance_m": 5000,
            "moving_time_s": 1800,
            "start_time": "2023-01-01T10:00:00",
            # No HR, No Cadence
            "avg_pace_s_per_km": 360,
        },
        "athlete": {
            "goal": "fitness",
            "experience_level": "intermediate"
        },
        "derived_metrics": [], # EMPTY
        "flags": [],
        "check_in": {},
        "available_signals": [],
        "missing_signals": ["heartrate", "cadence", "watts"]
    }

    try:
        scorecard = generate_scorecard(cp_dict, ai_client)
        print("SUCCESS: Generated for empty metrics.")
        print(scorecard.model_dump_json(indent=2))
    except Exception as e:
        print(f"FAILURE: {e}")

if __name__ == "__main__":
    test_empty_metrics()
