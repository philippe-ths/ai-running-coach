"""Unit tests for the typed analysis-composition intermediates (#701).

Covers the two new typed surfaces directly, independent of the orchestrator:
the single interval-session gate (`IntervalSession`) and the typed accumulator
(`DerivedMetricFields`) whose `to_columns()` must reproduce the exact upsert
dict.
"""

from dataclasses import fields

from app.models import DerivedMetric
from app.services.analysis.composition import DerivedMetricFields, IntervalSession


_PROBE = {"source": "recorded_laps", "summary": {"total_work_time_s": 600}}


# --- IntervalSession: the single "is this an interval session" owner ---

def test_gate_open_keeps_probed_structure_when_axis_is_intervals():
    session = IntervalSession.gate("intervals", _PROBE)
    assert session.is_session is True
    assert session.structure is _PROBE


def test_gate_closed_drops_structure_when_axis_not_intervals():
    session = IntervalSession.gate("continuous", _PROBE)
    assert session.is_session is False
    assert session.structure is None


def test_gate_closed_when_axis_intervals_but_no_probe():
    # Defensive: the classifier only resolves "intervals" off a probed
    # structure, but if it ever did without one, the gate stays closed.
    session = IntervalSession.gate("intervals", None)
    assert session.is_session is False
    assert session.structure is None


def test_gate_closed_for_none_axis():
    session = IntervalSession.gate(None, _PROBE)
    assert session.is_session is False


# --- DerivedMetricFields: the typed accumulator ---

def test_from_base_metrics_seeds_and_defaults():
    base = {
        "effort_score": 42.0,
        "pace_variability": 3.1,
        "hr_drift": 5.0,
        "time_in_zones": {"Z2": 100},
        "stops_analysis": None,
        "efficiency_analysis": None,
    }
    state = DerivedMetricFields.from_base_metrics(base)

    assert state.effort_score == 42.0
    assert state.time_in_zones == {"Z2": 100}
    # Later-stage fields start at their column defaults.
    assert state.effort is None
    assert state.structure is None
    assert state.flags == []
    assert state.confidence_reasons == []
    assert state.interval_kpis is None


def test_to_columns_preserves_object_identity():
    """to_columns() must be shallow: the JSON columns receive the very objects
    the stages produced (matching the pre-refactor **metrics_data upsert)."""
    zones = {"Z2": 100}
    structure = dict(_PROBE)
    state = DerivedMetricFields.from_base_metrics({"effort_score": 1.0})
    state.time_in_zones = zones
    state.interval_structure = structure

    cols = state.to_columns()

    assert cols["time_in_zones"] is zones
    assert cols["interval_structure"] is structure


def test_to_columns_keys_are_all_derivedmetric_columns():
    """Every accumulator field must map to a real DerivedMetric column, so
    `DerivedMetric(**state.to_columns())` stays valid."""
    state = DerivedMetricFields.from_base_metrics({"effort_score": 1.0})
    cols = set(state.to_columns())

    dm_columns = {c.name for c in DerivedMetric.__table__.columns}
    missing = cols - dm_columns
    assert not missing, f"accumulator fields not backed by a column: {missing}"


def test_field_names_match_accumulator_columns_roundtrip():
    # Guard against a field being renamed out of sync with to_columns().
    state = DerivedMetricFields.from_base_metrics({"effort_score": 1.0})
    assert set(state.to_columns()) == {f.name for f in fields(DerivedMetricFields)}
