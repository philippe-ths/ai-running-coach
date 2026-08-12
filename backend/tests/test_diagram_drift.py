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
    _CHAT_NODES,
    _chat_blob,
    _chat_capture_problems,
    _chat_node_problems,
    _chat_nodes,
    _chat_surface_problems,
    _declared_baseline_sections,
    _declared_chat_surface,
    _declared_pack_key_paths,
    _diagram_captured_prompt_id,
    _env_example_prompt_id,
    _generator_signature_problems,
    _pack_shape_problems,
    _recorded_chat_surface,
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


# --- the coach-chat diagram (#855) ------------------------------------------
#
# The chat diagram made the same promise as the report one — this is what the coach
# actually gets — and nothing checked it, while `make diagram-check` stayed green over
# both. Its contents are now pinned from three directions: the declared sets the
# conversational surface is made of must match what the committed CHAT blob records,
# every declared input must be DRAWN by a hand-authored node, and the capture must be a
# real one taken under prod-parity config.
#
# Several of these are SENSITIVITY tests, for the same reason #763's and #840's are: the
# failure being fixed here is a guard that passes while checking nothing, so a guard whose
# own ability to fail is untested reproduces it.


def _real_chat_nodes():
    return _chat_nodes(_CHAT_NODES.read_text())


def _real_recorded_surface():
    return _recorded_chat_surface(_chat_blob(_CHAT_NODES.read_text()))


def test_the_chat_diagram_records_the_conversational_surface_the_code_declares():
    """The tools, skills, proposed actions, screen keys and prompt slots a thread turn is
    built from, as the code declares them today vs as the diagram last recorded them."""
    problems = _chat_surface_problems(_declared_chat_surface(), _real_recorded_surface())

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_chat_surface_check_fails_when_a_tool_ships_without_a_regeneration():
    """SENSITIVITY. A tool added to the coach but not to the diagram is the whole point of
    the check: the conversational coach reaches for data the diagram says it cannot."""
    declared = _declared_chat_surface()
    declared["tools"] = sorted(declared["tools"] + ["get_race_predictions"])

    problems = _chat_surface_problems(declared, _real_recorded_surface())

    assert problems, "the chat surface check did not notice an unrecorded tool"
    assert "get_race_predictions" in problems[0]


def test_the_chat_surface_check_reports_a_recorded_tool_the_code_dropped():
    """Drift the other way: the diagram advertises a lookup the coach can no longer make."""
    recorded = dict(_real_recorded_surface())
    recorded["tools"] = sorted(recorded["tools"] + ["get_retired_tool"])

    problems = _chat_surface_problems(_declared_chat_surface(), recorded)

    assert problems and "get_retired_tool" in problems[0]


def test_the_chat_surface_check_fails_when_a_declared_set_comes_back_empty():
    """SENSITIVITY against vacuity itself. An extractor that silently returned nothing
    would make every comparison trivially satisfiable — the exact shape of the bug this
    guard exists to prevent — so an empty declared set is a failure, never a pass."""
    declared = _declared_chat_surface()
    declared["skills"] = []

    problems = _chat_surface_problems(declared, _real_recorded_surface())

    assert problems and "EXTRACTOR BROKE" in problems[0]


def test_a_missing_chat_blob_is_reported_rather_than_skipped():
    """No record at all must fail, or deleting the blob would silence the check."""
    problems = _chat_surface_problems(_declared_chat_surface(), None)

    assert problems and "unpinned" in problems[0]


def test_every_conversational_input_the_coach_receives_has_a_node():
    """The half a blob comparison cannot see: the blob is generated and the topology is
    hand-authored, so regenerating one without the other leaves a real input undrawn."""
    problems = _chat_node_problems(
        _declared_chat_surface(), _declared_baseline_sections(), _real_chat_nodes()
    )

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_node_check_fails_when_a_new_tool_has_no_node():
    """SENSITIVITY: the blob regenerated, the topology forgotten."""
    declared = _declared_chat_surface()
    declared["tools"] = sorted(declared["tools"] + ["get_race_predictions"])

    problems = _chat_node_problems(
        declared, _declared_baseline_sections(), _real_chat_nodes()
    )

    assert problems and "get_race_predictions" in problems[0]


def test_the_node_check_fails_when_a_baseline_section_has_no_builder_node():
    """SENSITIVITY, against the REAL defect this check found on arrival: #856 started
    handing the conversational coach the runner's schedule and the diagram drew no node
    for it, so the runner's plan reached the model invisibly."""
    nodes = [n for n in _real_chat_nodes() if n["id"] != "d_schedule"]

    problems = _chat_node_problems(_declared_chat_surface(), ["memory", "schedule"], nodes)

    assert problems, "the node check did not notice an undrawn baseline section"
    assert "schedule" in problems[0] and "d_schedule" in problems[0]


def test_the_node_check_fails_when_a_prompt_slot_has_no_node():
    """SENSITIVITY: a slot added to THREAD_SYSTEM_TEMPLATE is a new block of prose the
    coach reads, and the diagram numbers its slot nodes off that template."""
    declared = _declared_chat_surface()
    declared["prompt_slots"] = declared["prompt_slots"] + ["races_block"]

    problems = _chat_node_problems(
        declared, _declared_baseline_sections(), _real_chat_nodes()
    )

    assert problems and "{races_block}" in problems[0]


def test_the_node_check_fails_loudly_when_the_node_parser_returns_almost_nothing():
    """A parser that quietly stopped matching would turn node coverage into a check that
    cannot fail. Same fail-loud posture as the report guard's parsers."""
    problems = _chat_node_problems(_declared_chat_surface(), ["memory"], [])

    assert problems and "PARSER BROKE" in problems[0]


def test_the_baseline_sections_are_read_off_the_builder_that_assembles_them():
    """There is no constant to import — the keys are string literals in
    thread_turn._build_baseline_sections — so this pins that the static read finds them,
    including #856's schedule."""
    assert set(_declared_baseline_sections()) == {"memory", "readiness", "schedule"}


def test_the_chat_capture_was_taken_under_the_documented_prod_config():
    problems = _chat_capture_problems(_chat_blob(_CHAT_NODES.read_text()))

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_chat_capture_check_fails_when_the_capture_prompt_is_not_the_prod_one():
    """The chat generator pins its prompt with a LITERAL whose own comment says it goes
    stale silently — this is the check that makes that loud, as #841 did for the sibling."""
    blob = _chat_blob(_CHAT_NODES.read_text())
    blob["meta"] = dict(blob["meta"], coach_prompt_id="coach_message_lean_grouped_v5")

    problems = _chat_capture_problems(blob)

    assert problems and "coach_message_lean_grouped_v5" in problems[0]


def test_the_chat_capture_check_fails_on_a_template_only_capture():
    """A regeneration run with no seeded DB drops every real value the diagram promises to
    show, leaving every node rendering 'no capture'. That must not commit silently."""
    blob = _chat_blob(_CHAT_NODES.read_text())
    blob["assembled"] = {"ok": False, "reason": "CHAT_NO_DB set"}

    problems = _chat_capture_problems(blob)

    assert problems and "TEMPLATE-ONLY" in problems[0]
