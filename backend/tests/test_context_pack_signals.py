"""Tests for signal detection logic."""

from app.services.ai.signals import infer_signals


class MockActivity:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_infer_signals_hr_only():
    """Case 1: avg_hr only => heart_rate available, splits missing."""
    act = MockActivity(
        avg_hr=150,
        avg_cadence=None,
        elev_gain_m=None,
        raw_summary={}
    )
    available, missing = infer_signals(act)
    
    assert "heart_rate" in available
    assert "splits" not in available
    assert "splits" in missing


def test_infer_signals_cadence_elevation():
    """Case 2: cadence + elevation_gain => cadence/elevation available."""
    act = MockActivity(
        avg_hr=None,
        avg_cadence=180,
        elev_gain_m=100.0,
        raw_summary={}
    )
    available, missing = infer_signals(act)
    
    assert "cadence" in available
    assert "elevation" in available
    assert "heart_rate" not in available
    assert "heart_rate" in missing


def test_infer_signals_streams_hr_splits():
    """Case 3: streams with time/distance/hr => splits + heart_rate available."""
    act = MockActivity(
        avg_hr=None,
        avg_cadence=None,
        elev_gain_m=None,
        raw_summary={}
    )
    # distance stream implies splits
    streams = [
        {"stream_type": "heartrate"},
        {"stream_type": "distance"},  # Enables splits
        {"stream_type": "latlng"},    # Enables gps
    ]
    available, missing = infer_signals(act, streams=streams)

    assert "heart_rate" in available
    assert "splits" in available
    assert "gps" in available
    assert "power" in missing


def test_infer_signals_gps_from_summary():
    """GPS available from map polyline in raw summary."""
    act = MockActivity(
        raw_summary={"map": {"summary_polyline": "abcdef"}}
    )
    available, _ = infer_signals(act)
    assert "gps" in available


def test_infer_signals_power_from_summary():
    """Power available from raw summary."""
    act = MockActivity(
        raw_summary={"average_watts": 200}
    )
    available, _ = infer_signals(act)
    assert "power" in available


def test_infer_signals_weather_arg():
    """Weather explicitly provided."""
    act = MockActivity(raw_summary={})
    available, _ = infer_signals(act, weather={"temp": 20})
    assert "weather" in available
