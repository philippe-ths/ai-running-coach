"""Tests for the ContextPack contract."""

import json
from pathlib import Path

import pytest

from app.services.ai.context_pack import ContextPack

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


class TestContextPackContract:
    """Validate ContextPack Pydantic model against fixtures."""

    def test_minimal_fixture_validates(self):
        data = _load_fixture("context_pack_minimal.json")
        pack = ContextPack(**data)
        assert pack.activity.id == "a1b2c3d4"
        assert pack.activity.distance_m == 5012.0
        assert pack.athlete.goal == "10k"
        assert len(pack.derived_metrics) == 1
        assert pack.derived_metrics[0].key == "effort_score"
        assert len(pack.flags) == 1
        assert pack.flags[0].severity == "warn"
        assert "heart_rate" in pack.available_signals
        assert "power" in pack.missing_signals

    def test_to_prompt_json_is_deterministic(self):
        data = _load_fixture("context_pack_minimal.json")
        pack = ContextPack(**data)
        d1 = pack.to_prompt_json()
        d2 = pack.to_prompt_json()
        assert d1 == d2
        # Keys must be sorted at top level
        assert list(d1.keys()) == sorted(d1.keys())

    def test_to_prompt_text_is_valid_json(self):
        data = _load_fixture("context_pack_minimal.json")
        pack = ContextPack(**data)
        text = pack.to_prompt_text()
        parsed = json.loads(text)
        assert parsed["activity"]["id"] == "a1b2c3d4"

    def test_defaults_for_optional_sections(self):
        """ContextPack with only the required activity field."""
        pack = ContextPack(
            activity={
                "id": "x",
                "start_time": "2026-01-01T00:00:00Z",
                "type": "Run",
                "distance_m": 1000,
                "moving_time_s": 600,
            }
        )
        assert pack.athlete.goal is None
        assert pack.derived_metrics == []
        assert pack.flags == []
        assert pack.check_in.rpe_0_10 is None
        assert pack.available_signals == []
        assert pack.missing_signals == []

    def test_confidence_range_validation(self):
        """Confidence must be between 0 and 1."""
        with pytest.raises(Exception):
            ContextPack(
                activity={
                    "id": "x",
                    "start_time": "2026-01-01T00:00:00Z",
                    "type": "Run",
                    "distance_m": 1000,
                    "moving_time_s": 600,
                },
                derived_metrics=[
                    {
                        "key": "bad",
                        "value": 1.0,
                        "confidence": 1.5,  # out of range
                        "evidence": "n/a",
                    }
                ],
            )

    def test_flag_severity_validation(self):
        """Severity must be info, warn, or risk."""
        with pytest.raises(Exception):
            ContextPack(
                activity={
                    "id": "x",
                    "start_time": "2026-01-01T00:00:00Z",
                    "type": "Run",
                    "distance_m": 1000,
                    "moving_time_s": 600,
                },
                flags=[
                    {
                        "code": "test",
                        "severity": "critical",  # invalid
                        "message": "bad",
                        "evidence": "n/a",
                    }
                ],
            )
