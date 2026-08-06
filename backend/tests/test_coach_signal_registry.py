"""#800: the coach signal registry is the single declaration every registry derives from.

These tests pin the two things that make the consolidation worth anything:

  1. The DERIVED registries still produce byte-for-byte what they produced when they
     were hand-kept lists — the flat serialization order, the group relocation map,
     the drop descriptors, the grouped-prompt id set. A captured oracle, not a
     restatement of the code.
  2. The registry cannot be edited into disagreement without failing: at import for a
     structural mismatch, and behaviourally for a kill switch that is declared but
     never applied.
"""

from __future__ import annotations

import dataclasses
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings, settings
from app.models import Activity, DerivedMetric, User
from app.models.runner_memory import RunnerMemory
from app.schemas import coach_context as pack_schema
from app.services.coach import context as coach_ctx
from app.services.coach import prompts
from app.services.coach.prompt_features import PROMPT_FEATURES, PromptFeature
from app.services.coach.read_time_signals import ReadTimeSignal, build_signals, gather
from app.services.coach.signal_registry import (
    COACH_SIGNALS,
    READ_TIME_ADAPTERS,
    SIGNALS_BY_FIELD,
    CoachSignal,
    KillSwitch,
    ReadTimeAdapter,
    _assert_registry_consistent,
)


# --- Captured oracles -------------------------------------------------------
# Taken from `main` before the consolidation. If a change here is intended, it is a
# change to the bytes the coach receives and must be argued for, not absorbed.

FLAT_ORDER_ORACLE = (
    "activity", "metrics", "check_in", "profile", "recent_training_summary",
    "longitudinal", "perceived_effort", "adherence", "calibration", "believed_facts",
    "preference_profile", "narrative", "salience", "continuity", "block", "corpus",
    "stance", "training_load", "training_volume", "stream_view", "recent_training",
    "readiness", "recent_weeks", "training_history", "memory", "intensity",
    "intensity_read", "referral", "intensity_mix", "safety_rules",
)

SECTION_GROUP_ORACLE = {
    "activity": "this_run", "metrics": "this_run", "check_in": "this_run",
    "perceived_effort": "this_run", "calibration": "this_run",
    "stream_view": "this_run", "block": "this_run", "intensity": "this_run",
    "intensity_read": "this_run", "referral": "this_run",
    "training_load": "right_now", "training_volume": "right_now",
    "recent_training": "right_now", "readiness": "right_now",
    "recent_weeks": "right_now", "intensity_mix": "right_now",
    "profile": "the_runner", "memory": "the_runner", "training_history": "the_runner",
    "adherence": "our_thread", "continuity": "our_thread", "longitudinal": "our_thread",
    "corpus": "how_to_coach", "stance": "how_to_coach",
}

# field -> gate feature, for every descriptor that was in the hand-listed PACK_SECTIONS.
PACK_SECTION_ORACLE = {
    "block": None, "profile": PromptFeature.BODY, "corpus": PromptFeature.CORPUS,
    "stance": PromptFeature.STANCE, "training_load": PromptFeature.TRAINING_LOAD,
    "training_volume": PromptFeature.VOLUME, "stream_view": PromptFeature.STREAM_VIEW,
    "recent_training": PromptFeature.RECENT_TRAINING,
    "readiness": PromptFeature.READINESS, "recent_weeks": PromptFeature.RECENT_WEEKS,
    "training_history": PromptFeature.TRAINING_HISTORY, "memory": PromptFeature.MEMORY,
    "intensity": PromptFeature.INTENSITY,
    "intensity_read": PromptFeature.INTENSITY_READ,
    "referral": PromptFeature.INTENSITY_READ,
    "intensity_mix": PromptFeature.INTENSITY_MIX,
    "perceived_effort": None, "calibration": None, "recent_training_summary": None,
    "believed_facts": None, "preference_profile": None, "narrative": None,
    "longitudinal": None, "salience": None, "continuity": None,
}

# The hand-maintained set as it stood before #800 relocated it into the manifest. A
# SUBSET oracle, deliberately: the claim it exists to make is that the relocation lost
# nothing, and pinning it as an equality would additionally claim no grouped version
# will ever be added -- the forward-looking membership that #803 removed everywhere else,
# because it makes every new version edit the tests of the ones before it.
GROUPED_PACK_IDS_BEFORE_RELOCATION = frozenset(
    f"coach_message_lean_grouped_v{n}" for n in range(1, 9)
)

# The read-time adapters, with the gate and the kill switch the seam applies.
ADAPTER_ORACLE = {
    "calibration": (None, None),
    "adherence": (None, None),
    "training_load": (PromptFeature.TRAINING_LOAD, None),
    "training_volume": (PromptFeature.VOLUME, None),
    "recent_training": (PromptFeature.RECENT_TRAINING, None),
    "readiness": (PromptFeature.READINESS, None),
    "recent_weeks": (PromptFeature.RECENT_WEEKS, None),
    "training_history": (
        PromptFeature.TRAINING_HISTORY, "COACH_TRAINING_HISTORY_ENABLED"
    ),
    "training_history_2wk": (
        PromptFeature.TRAINING_HISTORY_2WK, "COACH_TRAINING_HISTORY_ENABLED"
    ),
    "memory": (PromptFeature.MEMORY, "COACH_MEMORY_ENABLED"),
    "intensity": (PromptFeature.INTENSITY, None),
}


# --- The derived registries reproduce the hand-kept ones ---------------------


def test_flat_order_derives_from_the_registry_unchanged():
    """The flat serialization order IS the declaration order. It is load-bearing twice:
    the serialized dict's key order, and the drop loop's order (which the one
    cross-section trim depends on)."""
    assert pack_schema._FLAT_ORDER == FLAT_ORDER_ORACLE
    assert tuple(s.field for s in COACH_SIGNALS) == FLAT_ORDER_ORACLE


def test_section_group_derives_from_the_registry_unchanged():
    assert pack_schema._SECTION_GROUP == SECTION_GROUP_ORACLE


def test_pack_sections_derive_from_the_registry_unchanged():
    """Same descriptors, same gates, same nested trims as the hand-listed tuple."""
    derived = {s.field: s.gate_feature for s in pack_schema.PACK_SECTIONS}
    assert derived == PACK_SECTION_ORACLE
    # And each descriptor's fields come straight off its declaration.
    for descriptor in pack_schema.PACK_SECTIONS:
        signal = SIGNALS_BY_FIELD[descriptor.field]
        assert signal.droppable
        assert descriptor.gate_feature is signal.gate_feature
        assert descriptor.nested_drop is signal.nested_drop


def test_pack_section_order_preserves_the_one_cross_section_trim():
    """`training_volume`'s rolling_7d fold reads `recent_training`, which the loop has
    not popped yet only because training_volume is declared FIRST. Pin the ordering so
    a reshuffle of the registry cannot silently un-fold the duplicate numbers."""
    order = [s.field for s in pack_schema.PACK_SECTIONS]
    assert order.index("training_volume") < order.index("recent_training")


def test_grouped_pack_prompt_ids_derive_from_the_manifest():
    """The last hand-maintained prompt-id set now derives like its siblings, and the
    relocation kept every id that was in it."""
    assert GROUPED_PACK_IDS_BEFORE_RELOCATION <= prompts.GROUPED_PACK_PROMPT_IDS
    for pid in GROUPED_PACK_IDS_BEFORE_RELOCATION:
        assert PromptFeature.GROUPED_PACK in PROMPT_FEATURES[pid]
    # The flag and the view agree in both directions, for every id the manifest knows.
    for pid, feats in PROMPT_FEATURES.items():
        assert (PromptFeature.GROUPED_PACK in feats) == (
            pid in prompts.GROUPED_PACK_PROMPT_IDS
        )
    # Nothing outside the grouped lineage picked the flag up.
    assert all(
        pid.startswith("coach_message_lean_grouped_v")
        for pid in prompts.GROUPED_PACK_PROMPT_IDS
    )


def test_read_time_seam_derives_its_gates_and_kill_switches():
    """`context.py` registers only the compute; the gate and the drop-effect kill
    switch come from the declaration, so the seam and the registry cannot disagree."""
    assert {a.name: (a.gate_feature, a.kill_switch) for a in READ_TIME_ADAPTERS} == (
        ADAPTER_ORACLE
    )
    for name, (gate, switch) in ADAPTER_ORACLE.items():
        signal = coach_ctx._SIGNALS[name]
        assert signal.gate_feature is gate
        assert signal.kill_switch == switch


# --- The registry cannot be edited into disagreement ------------------------


def _reregister(monkeypatch, signals):
    monkeypatch.setattr(
        "app.services.coach.signal_registry.COACH_SIGNALS", tuple(signals)
    )


def test_a_signal_must_state_its_gate(monkeypatch):
    """A signal with neither a kill switch nor a recorded reason for having none fails
    at import rather than shipping ungated and unremarked."""
    _reregister(monkeypatch, [CoachSignal(field="whatever", group="this_run")])
    with pytest.raises(RuntimeError, match="neither a kill switch nor an ungated_reason"):
        _assert_registry_consistent()


def test_a_kill_switch_must_name_a_real_setting(monkeypatch):
    _reregister(monkeypatch, [
        CoachSignal(
            field="whatever",
            group="this_run",
            kill_switches=(KillSwitch("COACH_NOT_A_REAL_SETTING", "drop", "nowhere"),),
        )
    ])
    with pytest.raises(RuntimeError, match="not a field on Settings"):
        _assert_registry_consistent()


def test_a_kill_switch_effect_must_be_one_of_the_three(monkeypatch):
    _reregister(monkeypatch, [
        CoachSignal(
            field="whatever",
            group="this_run",
            kill_switches=(KillSwitch("COACH_MEMORY_ENABLED", "maybe", "nowhere"),),
        )
    ])
    with pytest.raises(RuntimeError, match="declares effect"):
        _assert_registry_consistent()


def test_a_seam_applied_switch_must_be_declared_as_a_drop(monkeypatch):
    """An adapter cannot quietly apply a switch the signal calls a trim: the seam
    DROPS the whole section, so the declared effect must say so."""
    _reregister(monkeypatch, [
        CoachSignal(
            field="whatever",
            group="this_run",
            adapters=(ReadTimeAdapter("w", None, "COACH_MEMORY_ENABLED"),),
            kill_switches=(
                KillSwitch("COACH_MEMORY_ENABLED", "trim", "somewhere"),
            ),
        )
    ])
    with pytest.raises(RuntimeError, match="does not declare as a drop-effect"):
        _assert_registry_consistent()


def test_a_signal_declaring_a_group_that_does_not_exist_fails(monkeypatch):
    _reregister(monkeypatch, [
        CoachSignal(field="whatever", group="somewhere_else", ungated_reason="x")
    ])
    with pytest.raises(RuntimeError, match="not an ADR 0026 group"):
        _assert_registry_consistent()


def test_a_duplicate_signal_fails(monkeypatch):
    row = CoachSignal(field="block", group="this_run", ungated_reason="x")
    _reregister(monkeypatch, [row, row])
    with pytest.raises(RuntimeError, match="duplicate signal"):
        _assert_registry_consistent()


def test_a_pack_field_with_no_declaration_fails(monkeypatch):
    """The check that matters when a signal is ADDED: a field wired onto a group model
    but never declared would serialise gated by nothing and dropped by nothing."""
    monkeypatch.setattr(
        pack_schema, "COACH_SIGNALS", tuple(s for s in COACH_SIGNALS if s.field != "memory")
    )
    with pytest.raises(RuntimeError, match=r"\['memory'\] reach the coach with no"):
        pack_schema._assert_descriptors_match_fields()


def test_a_declaration_for_a_field_that_does_not_exist_fails(monkeypatch):
    monkeypatch.setattr(
        pack_schema,
        "COACH_SIGNALS",
        COACH_SIGNALS + (CoachSignal(field="not_a_pack_field", ungated_reason="x"),),
    )
    with pytest.raises(RuntimeError, match="does not exist on CoachContextPack"):
        pack_schema._assert_descriptors_match_fields()


def test_build_signals_rejects_a_declared_adapter_with_no_compute():
    computes = {a.name: (lambda db, act, as_of: None) for a in READ_TIME_ADAPTERS}
    computes.pop("memory")
    with pytest.raises(RuntimeError, match=r"\['memory'\] have no compute"):
        build_signals(computes)


def test_build_signals_rejects_a_compute_with_no_declaration():
    computes = {a.name: (lambda db, act, as_of: None) for a in READ_TIME_ADAPTERS}
    computes["invented"] = lambda db, act, as_of: None
    with pytest.raises(RuntimeError, match=r"\['invented'\] are registered with no"):
        build_signals(computes)


# --- Kill switches are declared AND applied ---------------------------------


def test_gather_applies_the_declared_kill_switch_before_the_compute(monkeypatch):
    """The seam drops the section without running the scan, which is what lets the
    builder stop re-checking the flag."""
    ran = []

    def compute(db, activity, as_of):
        ran.append(1)
        return "section"

    signal = ReadTimeSignal("x", compute, None, "COACH_MEMORY_ENABLED")
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", False)
    assert gather(signal, object(), object(), "coach_message_v13", datetime.now()) is None
    assert ran == []
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", True)
    assert gather(signal, object(), object(), "coach_message_v13", datetime.now()) == (
        "section"
    )
    assert ran == [1]


def test_every_declared_kill_switch_is_actually_read_somewhere():
    """A declaration that names a switch nothing applies is worse than no declaration:
    it reads as coverage. Every declared setting must be read in `app/` outside the
    config definition and this registry."""
    app_root = pathlib.Path(__file__).resolve().parents[1] / "app"
    sources = {
        p: p.read_text()
        for p in app_root.rglob("*.py")
        if p.name not in {"config.py", "signal_registry.py"}
    }
    declared = {
        ks.setting for s in COACH_SIGNALS for ks in s.kill_switches
    } | {a.kill_switch for a in READ_TIME_ADAPTERS if a.kill_switch}
    for name in sorted(declared):
        assert any(name in text for text in sources.values()), (
            f"{name} is declared on a coach signal but read nowhere in app/."
        )


def test_ungated_signals_are_an_explicit_recorded_list():
    """Nineteen of the thirty signals genuinely have no kill switch. Pinning the list
    makes adding a twentieth a decision someone takes, not a default someone
    inherits."""
    ungated = sorted(s.field for s in COACH_SIGNALS if not s.kill_switches)
    assert ungated == [
        "activity", "believed_facts", "block", "calibration", "intensity",
        "intensity_mix", "intensity_read", "narrative", "perceived_effort",
        "preference_profile", "profile", "readiness", "recent_training_summary",
        "recent_weeks", "referral", "safety_rules", "stream_view", "training_load",
        "training_volume",
    ]
    for field in ungated:
        assert SIGNALS_BY_FIELD[field].ungated_reason.strip(), field


def test_every_declared_setting_exists_on_settings():
    names = set(Settings.model_fields)
    for s in COACH_SIGNALS:
        for ks in s.kill_switches:
            assert ks.setting in names


# --- The moved switches still drop, end to end ------------------------------


_BASE = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
V13 = "coach_message_v13"


def _run_with_memory(db):
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"sr_{uid}@example.com"))
    db.flush()
    act = Activity(
        id=uuid.uuid4(), user_id=uid,
        strava_activity_id=abs(hash(str(uuid.uuid4()))) % 10**9,
        name="Run", type="Run", start_date=_BASE,
        distance_m=10000, moving_time_s=3600, elapsed_time_s=3700,
        avg_hr=150.0, max_hr=175.0, average_speed_mps=2.78, raw_summary={},
    )
    db.add(act)
    db.flush()
    db.add(DerivedMetric(
        id=uuid.uuid4(), activity_id=act.id, effort="easy", duration_class="standard",
        structure="continuous", is_hilly=False, is_race=False, effort_score=5.0,
        confidence="high", confidence_reasons=[], flags=[],
    ))
    db.add(RunnerMemory(
        id=uuid.uuid4(), user_id=uid,
        profile={"who_you_are": ["Runs before work."], "limits_and_constraints": [],
                 "goals_and_plans": [], "what_works_for_you": [], "lately": []},
        model_id="claude-haiku-4-5", source_report_count=3,
        grounded_through=_BASE - timedelta(days=1),
    ))
    db.flush()
    return act


def test_memory_switch_still_drops_the_section_now_that_the_seam_applies_it(
    db, monkeypatch
):
    """#800 moved COACH_MEMORY_ENABLED out of the builder and into the declaration the
    seam reads. The observable behaviour is unchanged: on => section, off => no key."""
    act = _run_with_memory(db)
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", True)
    on = coach_ctx.build_context_pack(db, act, prompt_id=V13).to_serializable_dict()
    assert "memory" in on
    monkeypatch.setattr(settings, "COACH_MEMORY_ENABLED", False)
    off = coach_ctx.build_context_pack(db, act, prompt_id=V13).to_serializable_dict()
    assert "memory" not in off
    # Byte-stable drop: the ONLY difference is the absent key.
    assert {k: v for k, v in on.items() if k != "memory"} == off


def test_training_history_switch_still_drops_before_the_scan(db, monkeypatch):
    """The other moved switch. Proven at the seam (the compute never runs), because a
    ladder needs years of history that this unit fixture has no business building."""
    ran = []

    def spy(dbx, activity, as_of):
        ran.append(1)
        return "ladder"

    for name in ("training_history", "training_history_2wk"):
        signal = dataclasses.replace(coach_ctx._SIGNALS[name], compute=spy)
        assert signal.kill_switch == "COACH_TRAINING_HISTORY_ENABLED"
        monkeypatch.setattr(settings, "COACH_TRAINING_HISTORY_ENABLED", False)
        gate = signal.gate_feature
        pid = next(p for p, f in PROMPT_FEATURES.items() if gate in f)
        assert gather(signal, object(), object(), pid, _BASE) is None
        assert ran == []
        monkeypatch.setattr(settings, "COACH_TRAINING_HISTORY_ENABLED", True)
        assert gather(signal, object(), object(), pid, _BASE) == "ladder"
        ran.clear()
