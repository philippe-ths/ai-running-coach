"""Contract tests for the analysis stage registry (#805).

The stage SEQUENCE is data (`stages.ANALYSIS_STAGES`), not placement in a
function body. These tests pin the three properties that make it load-bearing:

  1. Ordering is enforced AT IMPORT: a stage declaring a read that no earlier
     stage (and no load-phase input) provides raises rather than producing a
     quietly wrong row at runtime.
  2. The declared writes cover exactly the analytical `DerivedMetric` columns,
     derived from the model — so a new column cannot be silently unwritten and
     the golden snapshot cannot silently omit one.
  3. Every stage is interceptable through one seam (the `_orchestrator` module
     namespace), including the four that used to be imported lazily inside the
     function body.
"""

import dataclasses

import pytest

from app.models import DerivedMetric
from app.services.analysis import analyze
from app.services.analysis import stages as stages_mod
from app.services.analysis import _orchestrator
from app.services.analysis.composition import DerivedMetricFields
from app.services.analysis.stages import (
    ANALYSIS_STAGES,
    PRELOADED_INPUTS,
    SCRATCH_NAMES,
    Stage,
    assert_stage_contract,
)


# --- 1. Import-time ordering enforcement ------------------------------------

def test_registry_as_shipped_satisfies_its_own_contract():
    """The shipped sequence passes the check that runs at import."""
    assert_stage_contract(ANALYSIS_STAGES)


def test_stage_reading_a_field_no_earlier_stage_writes_is_rejected():
    """The acceptance criterion: an ordering violation fails at the check, not
    by handing a later stage a default value it cannot tell from a real one."""
    bad = (
        Stage(
            name="reads_too_early",
            adapter="_stage_flags",
            reads=("activity", "risk_level"),  # risk_level is written LATER
            writes=("flags",),
        ),
        Stage(
            name="risk",
            adapter="_stage_risk",
            reads=("flags",),
            writes=("risk_level", "risk_score", "risk_reasons"),
        ),
    )

    with pytest.raises(RuntimeError, match="reads 'risk_level'"):
        assert_stage_contract(bad, require_full_column_cover=False)


def test_reordering_two_real_stages_is_rejected():
    """Swapping the probe and the classifier — invariant 1 — is caught, because
    the classifier declares it reads the probe's output."""
    by_name = {s.name: s for s in ANALYSIS_STAGES}
    order = [s.name for s in ANALYSIS_STAGES]
    i, j = order.index("interval_probe"), order.index("classification")
    order[i], order[j] = order[j], order[i]
    swapped = tuple(by_name[n] for n in order)

    with pytest.raises(RuntimeError, match="probed_structure"):
        assert_stage_contract(swapped, require_full_column_cover=False)


def test_gate_before_classification_is_rejected():
    """Invariant 2: the interval gate reads the classifier's structure axis, so
    hoisting it above classification is an ordering violation."""
    by_name = {s.name: s for s in ANALYSIS_STAGES}
    order = [s.name for s in ANALYSIS_STAGES]
    order.remove("interval_gate")
    order.insert(order.index("classification"), "interval_gate")
    hoisted = tuple(by_name[n] for n in order)

    with pytest.raises(RuntimeError, match="structure"):
        assert_stage_contract(hoisted, require_full_column_cover=False)


def test_unknown_read_name_is_rejected():
    bad = (
        Stage(name="s", adapter="_stage_flags", reads=("not_a_thing",), writes=("flags",)),
    )
    with pytest.raises(RuntimeError, match="not_a_thing"):
        assert_stage_contract(bad, require_full_column_cover=False)


def test_unknown_write_name_is_rejected():
    bad = (
        Stage(name="s", adapter="_stage_flags", reads=(), writes=("not_a_column",)),
    )
    with pytest.raises(RuntimeError, match="not_a_column"):
        assert_stage_contract(bad, require_full_column_cover=False)


def test_two_stages_writing_the_same_field_is_rejected():
    bad = (
        Stage(name="a", adapter="_stage_flags", reads=(), writes=("flags",)),
        Stage(name="b", adapter="_stage_flags", reads=(), writes=("flags",)),
    )
    with pytest.raises(RuntimeError, match="written by more than one stage"):
        assert_stage_contract(bad, require_full_column_cover=False)


def test_duplicate_stage_name_is_rejected():
    bad = (
        Stage(name="a", adapter="_stage_flags", reads=(), writes=("flags",)),
        Stage(name="a", adapter="_stage_risk", reads=(), writes=("risk_level",)),
    )
    with pytest.raises(RuntimeError, match="duplicate"):
        assert_stage_contract(bad, require_full_column_cover=False)


def test_missing_adapter_is_rejected():
    bad = (
        Stage(name="a", adapter="_stage_does_not_exist", reads=(), writes=("flags",)),
    )
    with pytest.raises(RuntimeError, match="_stage_does_not_exist"):
        assert_stage_contract(
            bad, require_full_column_cover=False, namespace=_orchestrator
        )


def test_shipped_registry_resolves_every_adapter_on_the_orchestrator():
    """The half of the check the orchestrator completes once its adapters exist:
    a renamed adapter is a startup failure, not an AttributeError mid-analysis."""
    assert_stage_contract(ANALYSIS_STAGES, namespace=_orchestrator)


# --- 2. Column cover is derived, not restated -------------------------------

def _analytical_columns() -> set[str]:
    """The analytical columns of DerivedMetric, derived from the MODEL."""
    return {
        c.key
        for c in DerivedMetric.__mapper__.column_attrs
        if c.key not in {"id", "activity_id", "created_at", "updated_at"}
    }


def test_accumulator_fields_match_the_model_columns():
    """DerivedMetricFields is the row's shape; if a column is added to the model
    without a field here, to_columns() would silently stop covering the row."""
    fields = {f.name for f in dataclasses.fields(DerivedMetricFields)}
    assert fields == _analytical_columns()


def test_declared_writes_cover_every_analytical_column():
    """Every column the upsert writes is claimed by exactly one stage. A new
    column with no stage writing it fails here rather than shipping as a null."""
    written = set()
    for stage in ANALYSIS_STAGES:
        written |= set(stage.writes)
    assert written - SCRATCH_NAMES == _analytical_columns()


def test_scratch_names_are_not_columns():
    """A scratch name is an intra-run intermediate; it must never be mistaken
    for a persisted column."""
    assert SCRATCH_NAMES.isdisjoint(_analytical_columns())


def test_preloaded_inputs_are_not_columns():
    assert PRELOADED_INPUTS.isdisjoint(_analytical_columns())


# --- 3. One seam, every stage interceptable ---------------------------------

def test_every_stage_adapter_resolves_on_the_orchestrator_seam():
    for stage in ANALYSIS_STAGES:
        assert callable(getattr(_orchestrator, stage.adapter)), stage.name


# The four stage functions that used to be imported lazily INSIDE analyze(), so
# no test could reach them by patching the orchestrator module namespace. They
# are now module-level names on the seam.
_FORMERLY_LAZY = (
    "build_training_context",
    "compute_discount_signals",
    "build_stream_view",
    "recompute_runner_baseline",
)


@pytest.mark.parametrize("name", _FORMERLY_LAZY)
def test_formerly_lazy_stage_is_on_the_seam(name):
    assert callable(getattr(_orchestrator, name)), name


def test_stage_is_frozen():
    """Declarations are data; a caller must not be able to mutate one in place."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        ANALYSIS_STAGES[0].reads = ()


def test_registry_module_runs_its_check_at_import():
    """The check is not merely available — it runs when the module is imported."""
    assert stages_mod._CONTRACT_CHECKED is True


# --- 4. A stage receives what it declared, not the whole accumulator ---------

def test_projection_raises_on_an_undeclared_key(db):
    """The projection is not a plain dict. `flags.py` reads with `.get()`, so a
    plain dict would hand back None for a key the projection does not carry —
    the silent degradation this change exists to remove, reintroduced one layer
    down and in a different file from the declaration."""
    proj = stages_mod.DeclaredProjection({"effort": "easy"})
    assert proj.get("effort") == "easy"
    with pytest.raises(RuntimeError, match="read but not declared"):
        proj.get("hr_drift")


class _RecordingDict(stages_mod.DeclaredProjection):
    """A projection that remembers which keys were read through .get()."""

    __slots__ = ("accessed",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.accessed: set[str] = set()

    def get(self, key, default=None):
        self.accessed.add(key)
        return super().get(key, default)


def test_flags_receives_only_its_declared_reads(db, monkeypatch):
    """The flags stage used to be handed `state.to_columns()` — all 23 fields,
    eight of them still at the defaults of stages that had not run. It now gets a
    projection of the five it declares, and reads nothing outside them."""
    from tests.test_analysis_composition import _seed_run

    activity = _seed_run(db)

    declared = set(_orchestrator._FLAGS_METRIC_READS)
    seen = {}
    real_generate = _orchestrator.generate_flags

    def spy_generate(act, metric_data, history, check_in=None, history_metrics=None):
        recording = _RecordingDict(metric_data)
        seen["keys"] = set(metric_data)
        result = real_generate(
            act, recording, history, check_in, history_metrics=history_metrics
        )
        seen["accessed"] = recording.accessed
        return result

    monkeypatch.setattr(_orchestrator, "generate_flags", spy_generate)

    analyze(db, activity.id)

    # The projection is exactly the declaration — not the whole accumulator.
    assert seen["keys"] == declared
    # And flags.py reads nothing the declaration does not carry, so no read can
    # silently resolve to a default the stage never promised.
    assert seen["accessed"] <= declared


def test_flags_declaration_is_a_strict_subset_of_the_accumulator():
    """The narrowing is real: five declared fields against twenty-three."""
    declared = set(_orchestrator._FLAGS_METRIC_READS)
    assert declared < _analytical_columns()
    assert len(declared) == 5


# --- 5. An undeclared write raises rather than persisting -------------------

def _bare_context():
    return stages_mod.StageContext(
        db=None, activity=None, history=[], streams_dict={}, check_in=None,
        profile=None, max_hr=190, zone_boundaries=None,
        state=DerivedMetricFields(effort_score=0.0),
    )


def test_stage_writing_an_undeclared_field_raises():
    class _NS:
        __name__ = "fake"

        @staticmethod
        def _stage_bad(ctx):
            ctx.set("flags", ["ok"])
            ctx.set("risk_level", "high")  # NOT declared

    spec = (Stage(name="bad", adapter="_stage_bad", reads=(), writes=("flags",)),)

    with pytest.raises(RuntimeError, match="undeclared field"):
        stages_mod.run_stages(_NS, _bare_context(), spec)


def test_stage_reading_an_undeclared_name_raises():
    """What keeps `reads` from being a comment in a tuple. If a stage could quietly
    read a name it did not declare, the import-time ordering check — which is
    computed from those declarations — would be enforcing a fiction."""
    class _NS:
        __name__ = "fake"

        @staticmethod
        def _stage_sneaky(ctx):
            ctx.get("risk_level")  # never declared

    spec = (Stage(name="s", adapter="_stage_sneaky", reads=("activity",), writes=()),)

    with pytest.raises(RuntimeError, match="did not declare"):
        stages_mod.run_stages(_NS, _bare_context(), spec)


def test_every_real_stage_reads_only_what_it_declared(db, monkeypatch):
    """The same rule proved on the real pipeline: a full `analyze` over a seeded
    run completes, which it cannot do if any adapter reaches outside its
    declaration (the accessor raises)."""
    from tests.test_analysis_composition import _seed_run

    activity = _seed_run(db)
    assert analyze(db, activity.id) is not None


def test_stage_mutating_its_own_value_in_place_is_allowed():
    """The check is by IDENTITY, so a stage mutating the JSON object it owns is
    fine; only rebinding someone else's field is a violation."""
    ctx = _bare_context()
    ctx.state.time_in_zones = {"z1": 1}

    class _NS:
        __name__ = "fake"

        @staticmethod
        def _stage_ok(c):
            c.state.time_in_zones["z2"] = 2  # in-place, same object
            c.set("flags", [])

    spec = (Stage(name="ok", adapter="_stage_ok", reads=(), writes=("flags",)),)
    stages_mod.run_stages(_NS, ctx, spec)
    assert ctx.state.time_in_zones == {"z1": 1, "z2": 2}


# --- 6. The dormant confidence branch, recorded ------------------------------
#
# `_extract_planned_workout` is a documented placeholder returning None until
# planned-workout capture exists. These pin the consequence so it is a recorded
# dormancy rather than an accident nobody notices.

def test_planned_workout_capture_is_still_a_placeholder(db):
    """The blocking placeholder. When this starts returning a plan, the two pins
    below fail and the dormant branch must be revisited deliberately."""
    from app.models import CheckIn

    assert _orchestrator._extract_planned_workout(None) is None
    assert _orchestrator._extract_planned_workout(
        CheckIn(rpe=7, pain_score=1, sleep_quality=3, notes="8x400 planned")
    ) is None


def test_interval_structure_mismatch_cannot_fire_through_analyze(db, monkeypatch):
    """The dormant branch: `interval_structure_mismatch` is a CRITICAL confidence
    reason that can force a run to "low", but it needs match_score < 0.7, which
    needs a planned workout. With the placeholder in place, match_score is only
    ever None (stream-derived) or 1.0 (recorded laps, a perfect de-facto plan),
    so no run reaching `analyze` can ever trip it."""
    from tests.test_analysis_composition import _LAP_STRUCTURE, _seed_run

    activity = _seed_run(db)
    monkeypatch.setattr(
        _orchestrator, "detect_intervals_from_laps", lambda *a, **k: _LAP_STRUCTURE
    )
    monkeypatch.setattr(_orchestrator, "detect_intervals", lambda *a, **k: None)

    real_classify = _orchestrator.classify_activity

    def force_intervals(*args, **kwargs):
        c = real_classify(*args, **kwargs)
        c.structure = "intervals"
        return c

    monkeypatch.setattr(_orchestrator, "classify_activity", force_intervals)

    dm = analyze(db, activity.id)

    assert dm.interval_structure is not None, "the gate must be open for this pin to mean anything"
    score = (dm.workout_match or {}).get("match_score")
    assert score is None or score >= 0.7
    assert "interval_structure_mismatch" not in (dm.confidence_reasons or [])


def test_the_dormant_branch_is_dormant_not_dead():
    """It is unreachable through `analyze`, not unreachable in principle: given a
    match_score below the threshold it still fires. That is why it is recorded as
    dormant rather than deleted."""
    from app.services.analysis.composition import IntervalSession

    class _Activity:
        avg_hr = 150.0

    level, reasons = _orchestrator.compute_confidence(
        _Activity(),
        {"latlng": [[0, 0]], "heartrate": [150]},
        object(),  # a check-in exists
        interval_session=IntervalSession(structure={"source": "recorded_laps"}),
        workout_match={"match_score": 0.4, "confidence_reasons": []},
    )
    assert "interval_structure_mismatch" in reasons
    assert level in ("low", "medium")
