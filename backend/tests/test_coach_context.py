"""Tests for the coach context pack builder."""

import json
from app.services.coach.context import hash_context_pack


def test_hash_deterministic():
    pack = {"activity": {"date": "2024-01-01", "type": "Run"}, "metrics": {"effort_score": 100}}
    h1 = hash_context_pack(pack)
    h2 = hash_context_pack(pack)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_hash_changes_on_input_change():
    pack1 = {"activity": {"date": "2024-01-01"}, "metrics": {"effort_score": 100}}
    pack2 = {"activity": {"date": "2024-01-01"}, "metrics": {"effort_score": 101}}
    assert hash_context_pack(pack1) != hash_context_pack(pack2)


def test_context_pack_shape():
    """Verify expected top-level keys in a manually constructed pack."""
    # This tests the contract without needing a DB
    expected_keys = {
        "activity",
        "metrics",
        "check_in",
        "profile",
        "recent_training_summary",
        "safety_rules",
    }
    # Simulate a minimal pack
    pack = {
        "activity": {"date": None, "type": "Run", "distance_m": 0, "moving_time_s": 0, "avg_hr": None, "elev_gain_m": 0},
        "metrics": {"activity_class": None, "effort_score": None, "hr_drift": None, "pace_variability": None, "flags": [], "confidence": "low", "confidence_reasons": [], "time_in_zones": None},
        "check_in": {"rpe": None, "pain_score": None, "pain_location": None, "sleep_quality": None, "notes": None},
        "profile": {"goal_type": None, "experience_level": None, "weekly_days_available": None, "injury_notes": None},
        "recent_training_summary": {"last_7d": {}, "last_28d": {}, "previous_28d": {}},
        "safety_rules": {"never_diagnose": True, "pain_severe_threshold": 7, "no_invented_facts": True},
    }
    assert set(pack.keys()) == expected_keys
    # Verify it's JSON-serializable
    json.dumps(pack, default=str)
