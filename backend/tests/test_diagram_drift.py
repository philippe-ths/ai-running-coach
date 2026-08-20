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
import copy
import sys
from pathlib import Path

_DIAGRAMS = Path(__file__).resolve().parents[2] / "docs" / "diagrams"
sys.path.insert(0, str(_DIAGRAMS))

import check_diagram_drift  # noqa: E402
from check_diagram_drift import (  # noqa: E402
    _CHAT_NODES,
    _baseline_extractor_problems,
    _chat_blob,
    _chat_capture_problems,
    _chat_content_problems,
    _chat_node_problems,
    _chat_nodes,
    _chat_surface_problems,
    _declared_baseline_sections,
    _declared_screen_builders,
    _screen_builder_extractor_problems,
    _declared_chat_content,
    _declared_chat_surface,
    _recorded_chat_content,
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
        _declared_chat_surface(),
        _declared_baseline_sections(),
        _declared_screen_builders(),
        _real_chat_nodes(),
    )

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_node_check_fails_when_a_new_tool_has_no_node():
    """SENSITIVITY: the blob regenerated, the topology forgotten."""
    declared = _declared_chat_surface()
    declared["tools"] = sorted(declared["tools"] + ["get_race_predictions"])

    problems = _chat_node_problems(
        declared, _declared_baseline_sections(), _declared_screen_builders(), _real_chat_nodes()
    )

    assert problems and "get_race_predictions" in problems[0]


def test_the_node_check_fails_when_a_baseline_section_has_no_builder_node():
    """SENSITIVITY, against the REAL defect this check found on arrival: #856 started
    handing the conversational coach the runner's schedule and the diagram drew no node
    for it, so the runner's plan reached the model invisibly."""
    sections = _declared_baseline_sections()
    assert "schedule" in sections, "the #856 section moved; pick another"
    # the diagram as it was before this guard existed: the node simply not there
    nodes = [n for n in _real_chat_nodes() if n["id"] != "d_schedule"]

    problems = _chat_node_problems(
        _declared_chat_surface(), sections, _declared_screen_builders(), nodes
    )

    assert problems, "the node check did not notice an undrawn baseline section"
    assert "['schedule']" in problems[0]


def test_the_node_check_fails_when_a_drawn_section_has_no_downstream_edge():
    """SENSITIVITY, against the REAL defect this check was written for (#859).

    A drawn node is not a wired one. `d_running_norm` was drawn, its section was
    declared, node coverage passed in both directions, the drift guard passed and the
    suite passed — and the rendered page said "END OF CHAIN — nothing downstream
    consumes this" about a section the coach reads on every turn. Nothing in CI looked
    at whether an edge existed. This is that missing question.
    """
    nodes = _real_chat_nodes()
    wired = [n for n in nodes if n["section"]]
    assert wired, "no section-bound builder nodes; pick another fixture"
    target = wired[0]["id"]
    # the diagram as it was on arrival: the node present, nothing consuming it
    orphaned = [
        {**n, "from": [src for src in n["from"] if src != target]} for n in nodes
    ]

    problems = _chat_node_problems(
        _declared_chat_surface(),
        _declared_baseline_sections(),
        _declared_screen_builders(),
        orphaned,
    )

    assert problems, "the edge check did not notice a section drawn as a dead end"
    assert any(target in p and "no other node consumes" in p for p in problems)


def test_the_edge_check_fails_loudly_when_no_edge_can_be_parsed():
    """The check reads `from:[...]` out of the topology by regex. A parser that stopped
    matching would make every node look consumed by nothing — or, worse, make the
    orphan set empty and the check unfailable. Fail loud instead."""
    nodes = [{**n, "from": []} for n in _real_chat_nodes()]

    problems = _chat_node_problems(
        _declared_chat_surface(),
        _declared_baseline_sections(),
        _declared_screen_builders(),
        nodes,
    )

    assert any("PARSER BROKE" in p and "from:[...]" in p for p in problems)


def test_the_node_check_fails_when_a_prompt_slot_has_no_node():
    """SENSITIVITY: a slot added to THREAD_SYSTEM_TEMPLATE is a new block of prose the
    coach reads, and the diagram numbers its slot nodes off that template."""
    declared = _declared_chat_surface()
    declared["prompt_slots"] = declared["prompt_slots"] + ["races_block"]

    problems = _chat_node_problems(
        declared, _declared_baseline_sections(), _declared_screen_builders(), _real_chat_nodes()
    )

    assert problems and "{races_block}" in problems[0]


def test_the_node_check_fails_loudly_when_the_node_parser_returns_almost_nothing():
    """A parser that quietly stopped matching would turn node coverage into a check that
    cannot fail. Same fail-loud posture as the report guard's parsers."""
    problems = _chat_node_problems(
        _declared_chat_surface(), ["memory"], _declared_screen_builders(), []
    )

    assert problems and "PARSER BROKE" in problems[0]


def test_the_baseline_sections_are_read_off_the_builder_that_assembles_them():
    """There is no constant to import — the keys are string literals in
    thread_turn._build_baseline_sections — so this pins that the static read finds them,
    including #856's schedule."""
    assert set(_declared_baseline_sections()) == {
        "memory",
        "readiness",
        "schedule",
        "running_norm",
    }


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


# --- the content half: names are not the whole of what the coach reads ------


def test_the_chat_diagram_reproduces_the_prompt_and_tool_text_the_code_holds():
    blob = _chat_blob(_CHAT_NODES.read_text())

    problems = _chat_content_problems(_declared_chat_content(), _recorded_chat_content(blob))

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_content_check_fails_when_a_safety_rule_is_reworded_in_the_prompt():
    """SENSITIVITY, and the sharpest case for checking content rather than only names: the
    conversation's medical-scope rule could be inverted in the code while the diagram went
    on showing the old wording, with every name-level set still matching."""
    declared = _declared_chat_content()
    label = "the system prompt template"
    assert "NEVER diagnose" in declared[label]
    declared[label] = declared[label].replace("NEVER diagnose", "feel free to diagnose")

    problems = _chat_content_problems(
        declared, _recorded_chat_content(_chat_blob(_CHAT_NODES.read_text()))
    )

    assert problems, "the content check did not notice a reworded safety rule"
    assert "system prompt template" in problems[0]


def test_the_content_check_fails_when_a_tool_description_is_rewritten():
    """A tool's description is what tells the model when to reach for it, so rewriting one
    changes the coach's behaviour as surely as adding a tool does."""
    declared = _declared_chat_content()
    label = "the tool definitions (description + input schema)"
    declared[label] = [dict(t) for t in declared[label]]
    declared[label][0]["description"] = "something else entirely"

    problems = _chat_content_problems(
        declared, _recorded_chat_content(_chat_blob(_CHAT_NODES.read_text()))
    )

    assert problems and "tool definitions" in problems[0]


def test_the_content_check_fails_when_a_content_extractor_reads_back_empty():
    declared = _declared_chat_content()
    declared["the coaching skill procedures"] = []

    problems = _chat_content_problems(
        declared, _recorded_chat_content(_chat_blob(_CHAT_NODES.read_text()))
    )

    assert problems and "EXTRACTOR BROKE" in problems[0]


# --- both directions, and an extractor that refuses what it cannot read -----


def test_the_node_check_fails_when_a_node_draws_a_section_the_coach_lost():
    """Drift the other way round from #856: a baseline section removed from the builder
    leaves its node drawing an input the conversational coach no longer receives. Checked
    through the node's explicit `section:` binding, because `d_*` is the id prefix for every
    baseline builder and only some of those correspond to a section."""
    problems = _chat_node_problems(
        _declared_chat_surface(),
        ["memory", "readiness"],
        _declared_screen_builders(),
        _real_chat_nodes(),
    )

    assert problems and "schedule" in problems[0]


# --- screens that resolve a view (#871) --------------------------------------------


def test_every_screen_that_resolves_a_view_has_a_resolver_node():
    """The gap #871 names. `screens` is pinned as NAMES, so a screen key moving from
    identity-only to view-resolving changes what the coach is served on that screen while
    the key set, the tools, the skills, the actions and the slots all still match."""
    problems = _chat_node_problems(
        _declared_chat_surface(),
        _declared_baseline_sections(),
        _declared_screen_builders(),
        _real_chat_nodes(),
    )

    assert not problems, "\n".join(f"  - {p}" for p in problems)


def test_the_screens_that_resolve_a_view_are_read_off_the_resolver_itself():
    """There is no registry to import — the fact lives in `resolve_screen_view`'s
    branches. Pinned against the split as it stands so a reader that quietly stopped
    matching shows up as a changed set rather than as an empty one."""
    assert _declared_screen_builders() == ["activity", "trends"]


def test_the_node_check_fails_when_a_screen_starts_resolving_a_view_undrawn():
    """SENSITIVITY, on the exact shape the issue describes: a builder added for a key that
    already exists. Every other pinned set is untouched by this change."""
    builders = sorted(_declared_screen_builders() + ["load"])

    problems = _chat_node_problems(
        _declared_chat_surface(),
        _declared_baseline_sections(),
        builders,
        _real_chat_nodes(),
    )

    assert problems, "a newly view-resolving screen went unnoticed"
    assert any("['load']" in p for p in problems), problems


def test_the_node_check_fails_when_a_screen_stops_resolving_a_view():
    """Drift the other way: the diagram still draws that screen's contents reaching the
    coach after the builder was removed."""
    problems = _chat_node_problems(
        _declared_chat_surface(),
        _declared_baseline_sections(),
        ["activity"],
        _real_chat_nodes(),
    )

    assert problems and any("trends" in p for p in problems), problems


def test_the_node_check_fails_loudly_when_no_screen_builder_is_read():
    """An empty declared set is a broken extractor, not a coach that resolves nothing."""
    problems = _chat_node_problems(
        _declared_chat_surface(), _declared_baseline_sections(), [], _real_chat_nodes()
    )

    assert any("EXTRACTOR BROKE" in p for p in problems), problems


def test_the_screen_builder_reader_refuses_a_dispatch_form_it_cannot_read(
    tmp_path, monkeypatch
):
    """SENSITIVITY against PARTIAL blindness, the same posture the baseline reader takes.

    A resolver rewritten as a `match` or as a builder-dict lookup would read as ZERO
    builders through the equality-comparison reader, and the empty-set check would report
    a broken extractor rather than let it pass — but a resolver that keeps one `==` branch
    and adds a `match` for the rest would report a set that is real and incomplete, which
    is the failure the empty-set check cannot see."""
    probe = tmp_path / "screen_context.py"
    probe.write_text(
        "def resolve_screen_view(db, owner_user_id, pointer):\n"
        "    if pointer.screen == 'activity':\n"
        "        return detail()\n"
        "    match pointer.screen:\n"
        "        case 'trends':\n"
        "            return report()\n"
        "    return None\n"
    )
    monkeypatch.setattr(check_diagram_drift, "_SCREEN_CONTEXT", probe)

    problems = _screen_builder_extractor_problems()

    assert problems and "EXTRACTOR BROKE" in problems[0]
    assert "match" in problems[0]
    # and it is genuinely partial: the reader still sees the one branch it understands
    assert _declared_screen_builders() == ["activity"]


def test_the_screen_builder_reader_refuses_a_comparison_against_a_name(
    tmp_path, monkeypatch
):
    """The other unreadable form: a branch whose key is not a literal."""
    probe = tmp_path / "screen_context.py"
    probe.write_text(
        "def resolve_screen_view(db, owner_user_id, pointer):\n"
        "    if pointer.screen == VIEW_RESOLVING_SCREEN:\n"
        "        return detail()\n"
        "    return None\n"
    )
    monkeypatch.setattr(check_diagram_drift, "_SCREEN_CONTEXT", probe)

    assert any("EXTRACTOR BROKE" in p for p in _screen_builder_extractor_problems())


def test_the_screen_builder_reader_reads_a_membership_branch(tmp_path, monkeypatch):
    """The second form it does understand, so grouping two screens onto one builder is
    not mistaken for removing them."""
    probe = tmp_path / "screen_context.py"
    probe.write_text(
        "def resolve_screen_view(db, owner_user_id, pointer):\n"
        "    if pointer.screen in ('trends', 'load'):\n"
        "        return report()\n"
        "    return None\n"
    )
    monkeypatch.setattr(check_diagram_drift, "_SCREEN_CONTEXT", probe)

    assert _declared_screen_builders() == ["load", "trends"]
    assert _screen_builder_extractor_problems() == []


def test_the_screen_builder_reader_reports_a_resolver_that_was_renamed_away(
    tmp_path, monkeypatch
):
    probe = tmp_path / "screen_context.py"
    probe.write_text("def resolve(db, owner_user_id, pointer):\n    return None\n")
    monkeypatch.setattr(check_diagram_drift, "_SCREEN_CONTEXT", probe)

    assert any("no longer exists" in p for p in _screen_builder_extractor_problems())


def test_check_drift_actually_runs_the_screen_builder_checks(monkeypatch):
    """A check that is written but not called is no check. Make the LIVE declaration
    disagree with the diagram and assert the top-level guard reports it."""
    monkeypatch.setattr(
        check_diagram_drift,
        "_declared_screen_builders",
        lambda: sorted(_declared_screen_builders() + ["profile"]),
    )

    problems = check_drift()

    assert any("['profile']" in p for p in problems), problems


def test_check_drift_actually_runs_the_screen_builder_extractor_check(monkeypatch):
    monkeypatch.setattr(
        check_diagram_drift,
        "_screen_builder_extractor_problems",
        lambda: ["EXTRACTOR BROKE: screen probe"],
    )

    assert "EXTRACTOR BROKE: screen probe" in check_drift()


def test_the_baseline_section_reader_refuses_a_write_form_it_cannot_read(tmp_path, monkeypatch):
    """SENSITIVITY against PARTIAL blindness, which is worse than total: an extractor that
    understands `sections["x"] = ...` and silently ignores `sections.update(...)` would let
    a section through while the empty-set check stayed quiet. Feeds the reader a builder
    written the other way and asserts it refuses rather than under-reporting."""
    source = (
        "def _build_baseline_sections(db, user):\n"
        "    sections: dict = {}\n"
        '    sections["memory"] = 1\n'
        '    sections.update({"races": 2})\n'
        "    return sections\n"
    )
    probe = tmp_path / "thread_turn_probe.py"
    probe.write_text(source)
    monkeypatch.setattr(check_diagram_drift, "_THREAD_TURN", probe)

    problems = _baseline_extractor_problems()
    keys = _declared_baseline_sections()

    assert keys == ["memory"], "the probe's second section is invisible, as expected"
    assert problems and "EXTRACTOR BROKE" in problems[0] and "update" in problems[0]


def test_the_live_baseline_builder_is_written_in_a_form_the_reader_understands():
    assert _baseline_extractor_problems() == []


# --- the wiring itself: a check that is written but not called is no check ---
#
# Every test above calls the pure comparison functions directly, so all of them would stay
# green if check_drift() simply stopped calling them — and the script's success message
# would go on claiming coverage it was not performing. That is the same shape as the bug
# #855 is about, one level up, so the wiring gets its own tests.


def test_check_drift_actually_runs_the_chat_surface_and_node_checks(monkeypatch):
    """Make the LIVE conversational surface disagree with the diagram and assert the
    top-level guard reports it, which it can only do if it calls the checks."""
    surface = _declared_chat_surface()
    surface["tools"] = sorted(surface["tools"] + ["get_race_predictions"])
    monkeypatch.setattr(check_diagram_drift, "_declared_chat_surface", lambda: surface)

    problems = check_drift()

    assert any("get_race_predictions" in p for p in problems), problems
    assert any("NO node" in p for p in problems), problems


def test_check_drift_actually_runs_the_chat_content_and_capture_checks(monkeypatch):
    content = _declared_chat_content()
    content["the system prompt template"] = content["the system prompt template"] + " drift"
    monkeypatch.setattr(check_diagram_drift, "_declared_chat_content", lambda: content)
    monkeypatch.setattr(
        check_diagram_drift,
        "_chat_capture_problems",
        lambda blob: ["capture check ran"],
    )

    problems = check_drift()

    assert any("system prompt template" in p for p in problems), problems
    assert "capture check ran" in problems


def test_check_drift_actually_runs_the_baseline_extractor_check(monkeypatch):
    monkeypatch.setattr(
        check_diagram_drift,
        "_baseline_extractor_problems",
        lambda: ["EXTRACTOR BROKE: probe"],
    )

    assert "EXTRACTOR BROKE: probe" in check_drift()


# --- #793 / #871: the capture's config and the sixth pinned surface set -----------
# Every one of these is a SENSITIVITY test: the checks below all pass on the real
# committed diagram, and a check seen only passing is not evidence.


def _real_chat_blob() -> dict:
    from check_diagram_drift import _chat_blob

    blob = _chat_blob(_CHAT_NODES.read_text(encoding="utf-8"))
    assert blob is not None, "the committed chat diagram carries no readable CHAT blob"
    return blob


def test_the_capture_records_every_documented_coach_flag():
    """#793: `capture_config: "prod-pinned"` used to pin the prompt id and three
    flags out of seventeen, and label the result prod-pinned anyway."""
    from check_diagram_drift import _env_example_flags

    recorded = _real_chat_blob()["meta"].get("coach_flags") or {}
    documented = _env_example_flags()
    assert documented, "the .env.example flag reader came back empty"
    missing = sorted(set(documented) - set(recorded))
    assert not missing, (
        f"the capture does not record {missing}, which .env.example documents as "
        "part of the prod-parity contract"
    )


def test_the_env_example_reader_sees_a_flag_with_a_digit_in_its_name():
    """The reader's character class was `[A-Z_]+` until #793, which silently
    skipped COACH_PREVIOUS_30D_ENABLED -- a flag the guard therefore never
    checked and never said it was not checking."""
    from check_diagram_drift import _env_example_flags

    assert "COACH_PREVIOUS_30D_ENABLED" in _env_example_flags()


def test_the_capture_check_fails_when_a_flag_disagrees_with_the_contract():
    """SENSITIVITY: a capture taken with a kill switch flipped the other way."""
    from check_diagram_drift import _chat_capture_problems, _env_example_flags

    blob = copy.deepcopy(_real_chat_blob())
    flag, want = next(iter(_env_example_flags().items()))
    blob["meta"]["coach_flags"][flag] = not want

    problems = _chat_capture_problems(blob)

    assert problems and any(flag in p for p in problems), problems


def test_the_capture_check_fails_when_the_flag_map_is_missing():
    """SENSITIVITY: a generator that stops recording the map takes the label's
    only evidence with it, which must not read as 'nothing to check'."""
    from check_diagram_drift import _chat_capture_problems

    blob = copy.deepcopy(_real_chat_blob())
    blob["meta"].pop("coach_flags", None)

    problems = _chat_capture_problems(blob)

    assert problems and any("coach_flags" in p for p in problems), problems


def test_the_capture_check_fails_when_a_documented_flag_is_unrecorded():
    """SENSITIVITY: the generator stops pinning one flag the contract names."""
    from check_diagram_drift import _chat_capture_problems, _env_example_flags

    blob = copy.deepcopy(_real_chat_blob())
    flag = next(iter(_env_example_flags()))
    blob["meta"]["coach_flags"].pop(flag)

    problems = _chat_capture_problems(blob)

    assert problems and any(flag in p for p in problems), problems


def test_the_capture_check_fails_when_two_records_of_one_flag_disagree():
    """SENSITIVITY: the legacy meta boolean and the new map must not drift apart,
    or the diagram carries two answers to the same question."""
    from check_diagram_drift import _chat_capture_problems

    blob = copy.deepcopy(_real_chat_blob())
    blob["meta"]["voice_block_enabled"] = not blob["meta"]["coach_flags"][
        "COACH_VOICE_BLOCK_ENABLED"
    ]
    blob["meta"]["coach_flags"]["COACH_VOICE_BLOCK_ENABLED"] = not blob["meta"][
        "voice_block_enabled"
    ]

    problems = _chat_capture_problems(blob)

    assert problems and any("disagreeing" in p for p in problems), problems


def test_the_screen_builders_are_pinned_in_the_blob():
    """#871's other half: the set is recorded where every other closed set is."""
    assert _real_chat_blob().get("screen_builders") == sorted(
        _declared_screen_builders()
    )


def test_the_surface_check_fails_when_a_screen_starts_resolving_a_view():
    """SENSITIVITY, the fact this set exists for: moving a screen from
    identity-only to view-resolving changes what the coach is served."""
    declared = _declared_chat_surface()
    declared["screen_builders"] = sorted(declared["screen_builders"] + ["schedule"])

    problems = _chat_surface_problems(declared, _recorded_chat_surface(_real_chat_blob()))

    assert problems and any("resolve a real view" in p for p in problems), problems


def test_the_surface_check_fails_when_the_recorded_builder_set_is_lost():
    """SENSITIVITY: a blob regenerated by a generator that dropped the key."""
    blob = copy.deepcopy(_real_chat_blob())
    blob.pop("screen_builders", None)

    problems = _chat_surface_problems(_declared_chat_surface(), _recorded_chat_surface(blob))

    assert problems and any("resolve a real view" in p for p in problems), problems


# ---------------------------------------------------------------------------
# #909: the two guard blind spots that were green while both defects were live.
# ---------------------------------------------------------------------------


def _real_flow_src():
    from check_diagram_drift import _FLOW_NODES

    return _FLOW_NODES.read_text()


def test_the_captured_flag_reader_sees_a_flag_with_a_digit_in_its_name():
    """The report half's reader stayed `[A-Z_]+` when #793 widened the .env.example
    half, so COACH_PREVIOUS_30D_ENABLED -- the one flag whose name carries a digit
    -- was dropped from an INTERSECTION and went unchecked without saying so.

    Its sibling `test_the_env_example_reader_sees_a_flag_with_a_digit_in_its_name`
    covered only the copy that was fixed, which is how one of two identical readers
    stayed blind through a fix aimed at exactly that blindness.
    """
    from check_diagram_drift import _diagram_captured_flags

    captured = _diagram_captured_flags(_real_flow_src())

    assert captured, "the flow-nodes flag reader came back empty"
    assert "COACH_PREVIOUS_30D_ENABLED" in captured


def test_both_flag_readers_agree_on_which_flags_exist():
    """The two halves of the parity check must read the same NAMES, or the
    comparison between them quietly narrows to their intersection."""
    from check_diagram_drift import _diagram_captured_flags, _env_example_flags

    documented = _env_example_flags()
    captured = _diagram_captured_flags(_real_flow_src())

    assert documented and captured
    assert not sorted(set(documented) - set(captured)), (
        "flags .env.example documents that the capture does not record are "
        "unchecked rather than in parity"
    )


def test_the_flag_reader_reports_an_unreadable_blob_rather_than_no_flags():
    """SENSITIVITY: a blob that cannot be parsed must be distinguishable from one
    carrying no flags, since only one of those is benign."""
    from check_diagram_drift import _diagram_captured_flags

    assert _diagram_captured_flags("const DATA = not-json;") is None
    assert _diagram_captured_flags("") is None


def test_the_generator_covers_every_screen_a_pointer_can_name():
    """#909 defect 2: `schedule` reached ScreenPointer's Literal and never reached
    the generator, so 34 of 60 captured turns recorded `screen: null` and the
    diagram drew the coach receiving no screen context where it receives a real
    one. No existing check could see it: every other one compares the app's
    declarations against the DIAGRAM's nodes, and the capture was internally
    consistent, just built from less than the app offers."""
    from app.schemas.thread import ScreenPointer
    from check_diagram_drift import _generator_screen_coverage, _literal_values

    covered, broke = _generator_screen_coverage()

    assert broke is None, broke
    assert sorted(_literal_values(ScreenPointer, "screen")) == sorted(covered)


def test_the_screen_coverage_check_fails_when_the_generator_misses_a_screen():
    """SENSITIVITY: the historical bug itself, replayed."""
    import check_diagram_drift as guard

    original = guard._generator_screen_coverage
    guard._generator_screen_coverage = lambda: (["home"], None)
    try:
        problems = guard._screen_coverage_problems()
    finally:
        guard._generator_screen_coverage = original

    assert problems and any("cannot rebuild" in p for p in problems), problems
    assert any("schedule" in p for p in problems), problems


def test_the_screen_coverage_check_fails_when_its_extractor_breaks():
    """SENSITIVITY: a declaration that stops being statically readable must be a
    loud failure, not an empty set that passes."""
    import check_diagram_drift as guard

    original = guard._CHAT_GENERATOR
    guard._CHAT_GENERATOR = _tmp_generator_without_declaration()
    try:
        covered, broke = guard._generator_screen_coverage()
    finally:
        guard._CHAT_GENERATOR = original

    assert covered is None
    assert broke and "EXTRACTOR BROKE" in broke


def _tmp_generator_without_declaration():
    import pathlib
    import tempfile

    path = pathlib.Path(tempfile.mkdtemp()) / "generate_chat_flow_data.py"
    path.write_text("SOMETHING_ELSE = ('home',)\n")
    return path
