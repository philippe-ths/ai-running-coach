"""CI guard: the ai-flow-graph data-flow diagram must not silently drift from the code.

The diagram topology (docs/diagrams/flow-nodes.js) is hand-authored, so it desyncs as the
context pack and the DerivedMetric model evolve. Twice a real data path reached the LLM with
no node in the graph (stream_view, then block / user_materials / efficiency_analysis), caught
only by eye. This test runs the deterministic drift guard so the build — not a human — catches
the next desync.

The guard (docs/diagrams/check_diagram_drift.py) checks the high-value, mechanisable drift
classes: every CoachContextPack section must have a p_* node, every nested pack key must be in
the recorded pack shape (#763), every DerivedMetric column must be covered by the generator's
_DM_FIELDS and the FATE_DERIVED map, and every generator call into backend/app must still bind
against the callee's real signature (#840). It does NOT check edge correctness (which still
relies on the periodic audit).

Two of these tests are SENSITIVITY tests. The bug behind #840 and #763 was in both cases a
check that could not fail, so a guard whose own ability to fail is untested repeats it: those
tests feed the pure comparison functions a deliberately broken input and assert the guard
reports it.
"""
import sys
from pathlib import Path

_DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
sys.path.insert(0, str(_DIAGRAMS))

from check_diagram_drift import (  # noqa: E402
    _declared_pack_key_paths,
    _diagram_captured_prompt_id,
    _env_example_prompt_id,
    _generator_signature_problems,
    _pack_shape_problems,
    _recorded_pack_key_paths,
    check_drift,
)


def test_flow_graph_diagram_in_sync_with_code():
    problems = check_drift()
    assert not problems, (
        "ai-flow-graph diagram has drifted from the code. Update flow-nodes.js / "
        "generate_flow_nodes_data.py (or the guard's allowlist if intentional):\n\n"
        + "\n".join(f"  - {p}" for p in problems)
    )


def test_the_recorded_pack_shape_covers_every_key_the_coach_can_now_receive():
    """#763: the same failure check_drift reports, named so the diff is legible.

    A field nested inside an existing pack section (#742's profile.body) reaches the coach
    without changing the section list, so every root-level check stayed green while a new
    coach input shipped undrawn."""
    problems = _pack_shape_problems(
        _declared_pack_key_paths(), _recorded_pack_key_paths()
    )
    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_nested_pack_key_check_fails_when_a_field_is_added_inside_a_section():
    """SENSITIVITY. Drop one nested path from the recorded shape to simulate a field that
    was added to a pack model after the diagram was last regenerated, and prove the guard
    reports it. Without this, check 4 could silently degrade into a check that never fails —
    which is the bug #763 is about."""
    recorded = _recorded_pack_key_paths()
    assert recorded is not None
    declared = _declared_pack_key_paths()
    # profile.body.weight_kg is the real #742 field, and it is nested two levels down —
    # exactly the depth the old guard could not see.
    added = "profile.body.weight_kg"
    assert added in declared, "the #742 field moved; pick another nested path"

    problems = _pack_shape_problems(declared, [p for p in recorded if p != added])

    assert problems, "the nested-key guard did not notice an unrecorded pack key"
    assert added in problems[0]


def test_the_nested_pack_key_check_reports_a_key_the_pack_no_longer_declares():
    """The converse direction: a recorded key the code has dropped means the diagram depicts
    data the coach can no longer receive, which is drift the other way."""
    declared = _declared_pack_key_paths()

    problems = _pack_shape_problems(declared, declared + ["profile.retired_field"])

    assert problems and "profile.retired_field" in problems[0]


def test_the_diagram_generators_still_bind_against_the_functions_they_call():
    """#840: generate_flow_nodes_data.py raised TypeError for four days because it passed a
    `voice=` argument #822 had removed from build_system_prompt, and `make diagram-check`
    was green throughout — it only ever inspected flow-nodes.js. The generators need a
    seeded DB so CI cannot run them, but the failure was a signature mismatch, which needs
    no data to detect."""
    from check_diagram_drift import _GENERATORS

    problems = [
        p
        for generator in _GENERATORS
        for p in _generator_signature_problems(generator.read_text(), generator.name)
    ]
    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_signature_check_fails_on_a_keyword_the_callee_no_longer_accepts():
    """SENSITIVITY, against the REAL historical defect: the exact `voice=` call that broke
    the generator, fed to the checker as source text so no file has to be damaged."""
    source = (
        "from app.services.coach.prompts import build_system_prompt\n"
        "build_system_prompt(PROMPT_ID, key, voice=voice)\n"
    )

    problems = _generator_signature_problems(source, "synthetic_generator.py")

    assert problems, "the signature check did not notice a removed keyword argument"
    assert "voice" in problems[0] and "build_system_prompt" in problems[0]


def test_the_signature_check_fails_when_an_imported_name_no_longer_exists():
    """The other shape a dead generator takes: the callee was renamed or deleted outright,
    so the generator dies at import before any call site is reached."""
    source = "from app.services.coach.prompts import build_system_prompt_that_was_deleted\n"

    problems = _generator_signature_problems(source, "synthetic_generator.py")

    assert problems and "no longer resolves" in problems[0]


def test_the_signature_check_accepts_a_call_that_still_matches_its_callee():
    """The guard must not fire on a healthy call site, or it becomes noise people route
    around. Pairs with the sensitivity tests above: this pins the other side of the line."""
    source = (
        "from app.services.coach.prompts import build_system_prompt\n"
        "build_system_prompt(PROMPT_ID, key, mode='opener')\n"
    )

    assert _generator_signature_problems(source, "synthetic_generator.py") == []


# --- prompt parity (#841) ---------------------------------------------------
#
# The kill switches were guarded and the prompt id was not, yet a prompt id gates
# whole pack sections: a capture taken under the wrong one misdraws the coach's
# input exactly as a wrong switch does. The generator's own default had drifted to
# grouped_v5 while prod ran grouped_v9, so regenerating without pinning PROMPT_ID
# would have silently downgraded the capture.


def test_the_capture_records_the_prompt_it_was_taken_under():
    flow_src = (Path(__file__).resolve().parents[2] / "docs/diagrams/flow-nodes.js").read_text()

    assert _diagram_captured_prompt_id(flow_src), (
        "the diagram records no prompt id, so which pack it depicts cannot be checked"
    )


def test_the_captured_prompt_matches_the_documented_prod_prompt():
    flow_src = (Path(__file__).resolve().parents[2] / "docs/diagrams/flow-nodes.js").read_text()

    assert _diagram_captured_prompt_id(flow_src) == _env_example_prompt_id()


def test_the_generator_does_not_hardcode_a_prompt_id_that_can_rot():
    """It defaults to the CONFIGURED prompt. A literal here goes stale silently,
    which is exactly what happened between grouped_v5 and grouped_v9."""
    src = (Path(__file__).resolve().parents[2] / "docs/diagrams/generate_flow_nodes_data.py").read_text()

    assert "settings.COACH_PROMPT_ID" in src
    assert 'os.environ.get("PROMPT_ID", "coach_message' not in src
