#!/usr/bin/env python3
"""Drift guard for the two data-flow diagrams (flow-nodes.js, coach-chat-nodes.js).

The diagram's TOPOLOGY (its nodes and fate maps) is HAND-AUTHORED — only the embedded
DATA blob is regenerated. So the graph silently desyncs from the code as the context pack
and the DerivedMetric model evolve, and the desync is only ever caught by eye. It has been
caught by eye twice (stream_view, then block / user_materials / efficiency_analysis), which
is exactly the failure this guard exists to make impossible.

It pins the two drift classes that have actually bitten us — the dangerous "real data the
LLM receives, invisible in the graph" class — by diffing the hand-authored sets against the
LIVE code:

  1. PACK COVERAGE. Every `CoachContextPack` section (the JSON the model receives) must be
     bound by a `p_*` node, and every `p_*` node must bind a real pack section. Retired /
     never-serialized fields are allowlisted in _PACK_NOT_SHOWN.

  2. DERIVEDMETRIC COVERAGE. Every DerivedMetric data column must appear in BOTH the
     generator's `_DM_FIELDS` (so the DerivedMetric node renders it) AND the `FATE_DERIVED`
     map (so it carries a fate chip). This is what hid efficiency_analysis / confidence /
     training_context.

  3. KILL-SWITCH PARITY. The diagram's captured `D.flags` (the COACH_*_ENABLED values the
     capture was generated under) must match the prod-parity values documented in
     backend/.env.example. This pins the diagram to prod: it catches a capture regenerated
     with the wrong local config (e.g. every switch left on while prod runs the lean pack —
     the exact bug that made stops_analysis read as forwarded), and the reverse, a change to
     .env.example that was not followed by a regenerate. It ties the diagram to the committed
     prod-parity contract (.env.example), not to live Railway — keeping .env.example itself
     current with prod stays a human step.

  4. NESTED PACK KEY SET (#763). Checks 1-3 all work one level down from the pack root, so
     a field added INSIDE an existing section slipped through: #742 added `profile.body` and
     the guard stayed green, which would have shipped a new coach input undrawn. This check
     diffs the FULL declared key set the pack can carry (every field of every nested model,
     as dotted paths) against the set RECORDED in pack-shape.json when the diagram was last
     regenerated. See _declared_pack_key_paths for why the declaration — not the captured
     DATA.pack — is the source, and why that is what keeps this check quiet.

  4b. REPORT TAIL TOOL (#962). The report coach is handed one tool, `record_coach_tail`,
     and its name, description and property prose are the report's whole output contract.
     It is recorded verbatim in pack-shape.json and compared verbatim, the way check 6
     pins the chat tools: #944 added a ~700-character `offer` property and this guard
     stayed green.

  5. GENERATOR CALL SIGNATURES (#840). The generators call into backend/app to build their
     capture, so they rot when a callee's signature changes: generate_flow_nodes_data.py
     passed a `voice=` argument that #822 had removed and raised TypeError for four days
     while `make diagram-check` stayed green — the guard was policing the file's contents
     while the only tool that can produce those contents was dead. A generator needs a
     seeded DB, so CI cannot run one; but the observed failure was a SIGNATURE mismatch and
     not a data problem, so this check statically finds each call into app.* and binds it
     against the callee's REAL signature (inspect.signature.bind) without any data.

Checks 1-4 pin the REPORT diagram. Checks 6-8 pin the CHAT diagram (coach-chat-nodes.js),
which had no contents guard at all until #855 while `make diagram-check` stayed green over
it — a reader saw one green guard and reasonably assumed both diagrams were covered:

  6. CHAT SURFACE RECORD (#855). The conversational coach's surface is DECLARED in code:
     the tools it is handed, the coaching skills it can load, the proposed actions it can
     mint, the screens a pointer can name, and the system prompt's slots. Those declarations
     must match the ones RECORDED in the committed CHAT blob. See _recorded_chat_surface for
     why the blob itself is the record here and no pack-shape.json-style sidecar is added.
     The names are only half of it, so the same comparison also runs over the CONTENT the
     blob reproduces verbatim — the system prompt's prose, each tool's description and
     input schema, each coaching skill's procedure, the authority-tiering briefings and the
     floor's canned answers. Without that half, RULE 2 ("NEVER diagnose") could be inverted
     in the code while the diagram kept showing the old wording, green throughout.

  7. CHAT NODE COVERAGE (#855). The report guard's check 1, transferred: every declared tool
     must have a node in the hand-authored NODES, every prompt slot must have a slot node,
     and every relationship-baseline section must have a builder node — and the converse in
     each direction. This is the half a blob comparison cannot see: regenerate the blob and
     leave the topology alone and the new input is in the data but drawn nowhere. It found
     the real thing on arrival: #856 gave the conversational coach the runner's SCHEDULE and
     the diagram had no node for it.

  8. CHAT CAPTURE PARITY (#855). Check 3/3b transferred: the chat capture records the prompt
     id and the kill-switch states it was taken under, and they must match the prod-parity
     contract in backend/.env.example. The chat generator pins its prompt with a LITERAL
     whose own comment says it "goes stale silently" — this is what stops that. It also fails
     a capture committed with no DB behind it, because a template-only blob has quietly
     dropped every real value the diagram promises to show.

It does NOT verify edge correctness (which upstream stage feeds which node) — that is harder
to mechanise and still relies on the periodic human/agent audit. This guard's job is narrow
and high-value: no pack section, metric column, or conversational input can ever again reach
the model with no node.

Run standalone:  python docs/diagrams/check_diagram_drift.py   (exit 1 on drift)
Also enforced by: backend/tests/test_diagram_drift.py (so CI fails on drift)
                  make diagram-check
No DB and no `node` runtime are required — pure schema introspection + text parsing.
The helpers live in diagram_drift_report.py (checks 1-5), diagram_drift_chat.py (6-8) and
diagram_drift_paths.py; this file runs them and re-exports every name, so callers import from here.
"""
from __future__ import annotations

import ast
import re
import sys
import typing

from diagram_drift_paths import (  # noqa: F401 (re-exported: tests and generators import them from here)
    _HERE,
    _BACKEND,
    _FLOW_NODES,
    _CHAT_NODES,
    _GENERATOR,
    _CHAT_GENERATOR,
    _PACK_SHAPE,
    _ENV_EXAMPLE,
    _THREAD_TURN,
    _SCREEN_CONTEXT,
    _GENERATORS,
)
from diagram_drift_report import (  # noqa: F401 (re-exported: tests and generators import them from here)
    _PACK_NOT_SHOWN,
    _DM_NON_DATA,
    _canonical_pack_sections,
    _models_in,
    _walk_model,
    _declared_pack_key_paths,
    _recorded_pack_key_paths,
    _declared_report_tail_tool,
    _recorded_report_tail_tool,
    _report_tail_tool_problems,
    write_pack_shape,
    _pack_shape_problems,
    _imported_app_names,
    _resolve_imported,
    _generator_signature_problems,
    _canonical_derived_columns,
    _pack_keys_bound_by_nodes,
    _fate_derived_keys,
    _flow_blob,
    _diagram_captured_flags,
    _env_example_flags,
    _diagram_captured_prompt_id,
    _env_example_prompt_id,
    _generator_dm_fields,
    _difference_hint,
)
from diagram_drift_chat import (  # noqa: F401 (re-exported: tests and generators import them from here)
    _CHAT_SURFACE_LABELS,
    _CHAT_META_FLAG_SETTINGS,
    _declared_tools,
    _chat_blob,
    _recorded_chat_surface,
    _chat_surface_problems,
    _declared_chat_content,
    _recorded_chat_content,
    _chat_content_problems,
    _chat_nodes,
    _chat_node_problems,
    _chat_capture_problems,
)


# --- the CHAT diagram (#855) ------------------------------------------------------
#
# The report pack is one declared Pydantic model, so its guard walks that model. A thread
# turn has no such model: its context is assembled per turn out of several independently
# declared sets — the tools it is handed, the skills it can load, the actions it can mint,
# the screens a pointer can name, the prompt's slots, the baseline sections. So the chat
# guard pins THOSE SETS BY NAME. That is the same principle as check 4 (compare declaration
# against a record, never against a captured value), reached through a different door.


def _literal_values(model, field: str) -> list[str]:
    """The Literal alternatives declared for one field — the closed vocabularies the
    conversational surface is built from (an action kind, a screen key)."""
    return [
        arg
        for arg in typing.get_args(model.model_fields[field].annotation)
        if isinstance(arg, str)
    ]


def _baseline_builder_body() -> ast.FunctionDef | None:
    """The AST of thread_turn._build_baseline_sections, or None if it was renamed away."""
    tree = ast.parse(_THREAD_TURN.read_text(), filename=_THREAD_TURN.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_build_baseline_sections":
            return node
    return None


def _declared_baseline_sections() -> list[str]:
    """The relationship-baseline sections a thread turn assembles, read from the builder.

    `_build_baseline_sections` returns a plain dict built at runtime, so there is no
    constant to import. The keys are still DECLARED — they are string literals assigned in
    that function's body — so this reads them statically off the source rather than adding
    a second list in the app that could drift from the builder it describes. Static, so it
    needs no DB and no runtime state.

    Paired with _baseline_extractor_problems, which refuses any OTHER way of writing into
    that dict — because an extractor that understands one syntax and silently ignores the
    rest is a guard that reads less than it claims to."""
    body = _baseline_builder_body()
    if body is None:
        return []
    keys: list[str] = []
    for sub in ast.walk(body):
        if (
            isinstance(sub, ast.Subscript)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "sections"
            and isinstance(sub.slice, ast.Constant)
            and isinstance(sub.slice.value, str)
        ):
            keys.append(sub.slice.value)
    return sorted(set(keys))


def _baseline_extractor_problems() -> list[str]:
    """Refuse any write into `sections` this reader cannot see the key of.

    _declared_baseline_sections understands exactly one form, `sections["x"] = ...`. A
    section added by `sections.update({...})`, by rebinding the name to a populated dict,
    or by a comprehension would be invisible to it — and PARTIAL blindness is worse than
    total, because the empty-set check would not fire. So an unrecognised write is a loud
    failure telling the next person to teach the reader that form, not a silent pass."""
    body = _baseline_builder_body()
    if body is None:
        return [
            "EXTRACTOR BROKE: thread_turn._build_baseline_sections no longer exists, so the "
            "conversational baseline sections cannot be read. Point the guard at its "
            "replacement."]
    problems: list[str] = []
    for sub in ast.walk(body):
        # `sections.update(...)`, `sections.setdefault(...)`, and friends.
        if (
            isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "sections"
        ):
            problems.append(
                f"EXTRACTOR BROKE: _build_baseline_sections writes into `sections` via "
                f"`.{sub.attr}()` at line {sub.lineno}, a form this guard cannot read the "
                "keys of, so a baseline section could reach the coach unseen. Teach "
                "_declared_baseline_sections that form.")
        # rebinding `sections` to anything but the empty dict it starts as
        if isinstance(sub, (ast.Assign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            if any(isinstance(t, ast.Name) and t.id == "sections" for t in targets):
                value = sub.value
                empty = isinstance(value, ast.Dict) and not value.keys
                if not empty:
                    problems.append(
                        "EXTRACTOR BROKE: _build_baseline_sections binds `sections` to a "
                        f"populated value at line {sub.lineno}, so its keys are not visible "
                        "to this guard. Teach _declared_baseline_sections that form.")
    return problems


def _screen_resolver_body() -> ast.FunctionDef | None:
    """The AST of screen_context.resolve_screen_view, or None if it was renamed away."""
    tree = ast.parse(_SCREEN_CONTEXT.read_text(), filename=_SCREEN_CONTEXT.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_screen_view":
            return node
    return None


def _screen_key_comparisons(body: ast.FunctionDef) -> list[ast.Compare]:
    """Every comparison in the resolver that tests which screen the pointer names."""
    found = []
    for sub in ast.walk(body):
        if not isinstance(sub, ast.Compare):
            continue
        left = sub.left
        if isinstance(left, ast.Attribute) and left.attr == "screen":
            found.append(sub)
    return found


def _declared_screen_builders() -> list[str]:
    """The screen keys that resolve a real VIEW, read off the resolver's own branches.

    #871. The `screens` set pinned above is the KEYS a ScreenPointer can name, so adding
    a key trips the guard. It says nothing about which of those keys resolve a view: the
    keys are split between identity-only screens (the baseline already carries their
    content, so the coach learns only WHERE the runner is) and screens that run a builder
    and put a real view in the prompt's LOOKING AT section. Moving a screen from the first
    group to the second changes what the coach is served and is precisely what the diagram
    exists to depict, and every other pinned set matched through it.

    There is no registry to read: the fact lives only in `resolve_screen_view`'s
    if-branches. So this reads them statically, the same door `_declared_baseline_sections`
    goes through for a dict built at runtime — a declaration in the code that has no
    constant to import is still a declaration. Static, so it needs no DB and no runtime
    state.

    A branch on the screen key that does something OTHER than resolve a view would be read
    as a builder here. That is deliberate: any special-casing of one screen is a fact about
    what that screen gets, and the guard failing is the prompt to draw it or to say why not.

    Paired with _screen_builder_extractor_problems, which refuses any branch form this
    reader cannot get the key out of."""
    body = _screen_resolver_body()
    if body is None:
        return []
    keys: list[str] = []
    for compare in _screen_key_comparisons(body):
        for op, comparator in zip(compare.ops, compare.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    keys.append(comparator.value)
            elif isinstance(op, ast.In) and isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                for element in comparator.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        keys.append(element.value)
    return sorted(set(keys))


def _generator_screen_coverage() -> tuple[list[str] | None, str | None]:
    """The screens generate_chat_flow_data declares it can rebuild a pointer for.

    Read from the generator's SOURCE, not by importing it: every other generator
    check in this file is static for the same reason, since the generators need a
    seeded database and CI has none.
    """
    tree = ast.parse(_CHAT_GENERATOR.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SCREENS_COVERED":
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    return None, (
                        "EXTRACTOR BROKE: generate_chat_flow_data.SCREENS_COVERED is no longer a "
                        "literal, so the generator's screen coverage cannot be read statically. "
                        "Keep it a plain tuple of strings, or teach _generator_screen_coverage "
                        "the new form.")
                return sorted(str(v) for v in value), None
    return None, (
        "EXTRACTOR BROKE: generate_chat_flow_data no longer declares SCREENS_COVERED, so nothing "
        "checks that the generator can rebuild every screen a ScreenPointer accepts. Restore the "
        "declaration or replace this check with one that reads whatever succeeded it.")


def _screen_coverage_problems() -> list[str]:
    """The generator must cover every screen the schema accepts.

    The gap this closes is not hypothetical. `schedule` was added to ScreenPointer's
    Literal and the generator was not taught it, so 34 of 60 captured turns recorded
    `screen: null` and the rendered diagram showed the coach receiving no screen
    context for the majority of the conversation. It actually received a label and a
    real LOOKING AT block. Every other check here compares the app's declarations
    against the DIAGRAM's nodes, and none of them can see this: the capture was
    internally consistent, just built from less than the app offers.
    """
    from app.schemas.thread import ScreenPointer  # lazy: needs the app importable

    covered, broke = _generator_screen_coverage()
    if broke:
        return [broke]
    accepted = sorted(_literal_values(ScreenPointer, "screen"))
    missing = sorted(set(accepted) - set(covered or []))
    spurious = sorted(set(covered or []) - set(accepted))
    problems: list[str] = []
    if missing:
        problems.append(
            f"generate_chat_flow_data cannot rebuild a screen pointer for {missing}, which "
            "ScreenPointer accepts. Every turn asked from one of those screens captures as "
            "`screen: null`, so the diagram draws the coach receiving no screen context where "
            "it receives a real one. Add the screen to SCREENS_COVERED and to the branch that "
            "handles it in _pointer_for.")
    if spurious:
        problems.append(
            f"generate_chat_flow_data declares screen coverage for {spurious}, which "
            "ScreenPointer does not accept. Remove them from SCREENS_COVERED.")
    return problems


def _screen_builder_extractor_problems() -> list[str]:
    """Refuse any way of dispatching on the screen key this reader cannot follow.

    _declared_screen_builders understands two forms, `pointer.screen == "x"` and
    `pointer.screen in ("x", "y")`. A `match pointer.screen:` statement, a lookup into a
    builder dict, or a comparison against a name rather than a literal would be invisible
    to it — and PARTIAL blindness is worse than total, because the empty-set check below
    would not fire. So an unrecognised dispatch is a loud failure telling the next person
    to teach the reader that form, not a silent pass.

    This is _baseline_extractor_problems' rule applied to the other statically-read
    declaration, for the same reason: a guard that reads less than it claims to is the
    vacuous-guard failure this whole file exists against."""
    body = _screen_resolver_body()
    if body is None:
        return [
            "EXTRACTOR BROKE: screen_context.resolve_screen_view no longer exists, so which "
            "screens resolve a view cannot be read. Point the guard at its replacement."]
    problems: list[str] = []
    for sub in ast.walk(body):
        if isinstance(sub, ast.Match):
            problems.append(
                f"EXTRACTOR BROKE: resolve_screen_view dispatches with `match` at line "
                f"{sub.lineno}, a form this guard cannot read the screen keys of. Teach "
                "_declared_screen_builders that form.")
    for compare in _screen_key_comparisons(body):
        for op, comparator in zip(compare.ops, compare.comparators):
            readable = (
                (isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant)
                 and isinstance(comparator.value, str))
                or (isinstance(op, ast.In)
                    and isinstance(comparator, (ast.Tuple, ast.List, ast.Set))
                    and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                            for e in comparator.elts))
            )
            if not readable:
                problems.append(
                    f"EXTRACTOR BROKE: resolve_screen_view tests the screen key at line "
                    f"{compare.lineno} in a form this guard cannot read the key out of "
                    f"({type(op).__name__} against {type(comparator).__name__}). Teach "
                    "_declared_screen_builders that form.")
    return problems


def _declared_chat_surface() -> dict[str, list[str]]:
    """Every closed set the conversational coach's surface is made of, from the live code."""
    from app.schemas.thread import ScreenPointer  # lazy: needs the app importable
    from app.services.coach import coaching_skills, proposed_actions
    from app.services.coach import thread_turn as tt

    return {
        "tools": sorted(t["name"] for t in _declared_tools()),
        "skills": sorted(s.name for s in coaching_skills.SKILLS),
        "action_kinds": sorted(
            _literal_values(proposed_actions.ProposedActionRequest, "action_type")
        ),
        "screens": sorted(_literal_values(ScreenPointer, "screen")),
        # #871's other half. The node check below already notices a screen that
        # STARTS resolving a view, via the hand-authored bindings; this pins the
        # set in the generated blob too, so the fact is recorded where every
        # other closed set is recorded rather than only inferable from the
        # topology. Both readings go through _declared_screen_builders, and the
        # generator records it through the same function, so the only way live
        # and recorded can differ is somebody changing the resolver without
        # regenerating.
        "screen_builders": sorted(_declared_screen_builders()),
        # ORDER matters here and nowhere else: the diagram numbers its slot nodes
        # "slot 1".."slot 8" in template order, so a reordered template misnumbers them.
        "prompt_slots": re.findall(r"\{(\w+)\}", tt.THREAD_SYSTEM_TEMPLATE),
    }


def check_drift() -> list[str]:
    """Return a list of human-readable drift problems. Empty list == diagram is in sync."""
    if _BACKEND.is_dir() and str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))

    problems: list[str] = []
    flow_src = _FLOW_NODES.read_text()
    gen_src = _GENERATOR.read_text()

    pack_sections = _canonical_pack_sections()
    derived_columns = _canonical_derived_columns()
    node_keys = _pack_keys_bound_by_nodes(flow_src)
    fate_keys = _fate_derived_keys(flow_src)
    dm_fields = _generator_dm_fields(gen_src)

    # Self-check: if a parser silently returned almost nothing, FAIL LOUD rather than pass.
    if len(node_keys) < 15:
        problems.append(f"PARSER BROKE: found only {len(node_keys)} pack-node bindings in "
                        "flow-nodes.js (expected ~20). The drift guard cannot be trusted — fix it.")
    if len(fate_keys) < 18:
        problems.append(f"PARSER BROKE: found only {len(fate_keys)} FATE_DERIVED keys "
                        "(expected ~23). The drift guard cannot be trusted — fix it.")
    if len(dm_fields) < 18:
        problems.append(f"PARSER BROKE: found only {len(dm_fields)} _DM_FIELDS entries "
                        "(expected ~23). The drift guard cannot be trusted — fix it.")
    if problems:
        return problems

    # 1. Pack coverage.
    expected_pack = pack_sections - _PACK_NOT_SHOWN
    missing_nodes = expected_pack - node_keys
    spurious_nodes = node_keys - pack_sections  # binds a P.<key> that is not a real section
    if missing_nodes:
        problems.append(
            "Pack sections reach the LLM but have NO p_* node in the diagram (the stream_view "
            f"class of bug): {sorted(missing_nodes)}. Add a p_<section> node + wire it into "
            "llm.from, OR allowlist it in _PACK_NOT_SHOWN if it is intentionally never shown.")
    if spurious_nodes:
        problems.append(
            f"p_* nodes bind pack keys that no longer exist on CoachContextPack: {sorted(spurious_nodes)}. "
            "Remove the node or fix the P.<key> it renders.")

    # 2. DerivedMetric coverage — generator render set.
    missing_dm = derived_columns - dm_fields
    spurious_dm = dm_fields - derived_columns
    if missing_dm:
        problems.append(
            f"DerivedMetric columns missing from the generator's _DM_FIELDS (so they never render "
            f"on the DerivedMetric node): {sorted(missing_dm)}. Add them to _DM_FIELDS.")
    if spurious_dm:
        problems.append(
            f"_DM_FIELDS lists columns that are not on the DerivedMetric model: {sorted(spurious_dm)}. "
            "Remove them.")

    # 2b. DerivedMetric coverage — fate map.
    missing_fate = derived_columns - fate_keys
    spurious_fate = fate_keys - derived_columns
    if missing_fate:
        problems.append(
            f"DerivedMetric columns missing a FATE_DERIVED chip (so their fate into pack.metrics is "
            f"undocumented — the efficiency_analysis class of bug): {sorted(missing_fate)}. Add a "
            "fate entry (forwarded / reduced / gated).")
    if spurious_fate:
        problems.append(
            f"FATE_DERIVED maps keys that are not DerivedMetric columns: {sorted(spurious_fate)}. Remove them.")

    # 3. Kill-switch parity: the captured D.flags must match .env.example's prod-parity block.
    captured = _diagram_captured_flags(flow_src)
    documented = _env_example_flags()
    if not captured:
        problems.append(
            "The diagram carries NO readable captured COACH_*_ENABLED flags — its `const DATA` "
            "blob, or the top-level `flags` map inside it, could not be read. Regenerate it under "
            "the prod-parity config (backend/.env mirroring .env.example) so the off-state chips "
            "are capture-driven. See generate_flow_nodes_data.py.")
    elif not documented:
        problems.append(
            "backend/.env.example documents no COACH_*_ENABLED prod-parity block, so the diagram's "
            "kill-switch state cannot be verified against prod. Add the prod-parity flags to .env.example.")
    else:
        mismatched = sorted(
            f"{k} (diagram={captured[k]}, .env.example={documented[k]})"
            for k in captured.keys() & documented.keys()
            if captured[k] != documented[k]
        )
        if mismatched:
            problems.append(
                "The diagram was generated under coach kill-switch flags that DISAGREE with the "
                f"prod-parity set in backend/.env.example: {mismatched}. Regenerate flow-nodes.js "
                "under prod-parity config (or update .env.example if prod itself changed), so the "
                "diagram reflects what prod actually sends.")
        # The comparison above is an INTERSECTION, so a flag .env.example documents that the
        # capture does not record is not a match and not a mismatch -- it is simply unchecked,
        # and silently so. That is the same failure one step earlier: the capture stopped
        # pinning something the parity contract names. The chat half already reports this;
        # the report half did not, which is how a whole flag can go unguarded while green.
        unrecorded = sorted(set(documented) - set(captured))
        if unrecorded:
            problems.append(
                f"backend/.env.example documents COACH_*_ENABLED flags the flow-nodes.js capture "
                f"does not record at all, so they are unchecked rather than in parity: {unrecorded}. "
                "Regenerate flow-nodes.js so its `flags` map covers every flag the parity block names.")

    # 3b. PROMPT parity. The kill switches are only half of what decides the pack: the
    #     prompt id gates whole sections (a section reaches a v9 pack and not a v5 one), so
    #     a capture taken under the wrong prompt misdraws the coach's input exactly as a
    #     wrong switch does. This half was unguarded, and the generator's own default had
    #     been left at grouped_v5 while prod ran grouped_v9 — regenerating without setting
    #     PROMPT_ID would have silently downgraded the capture with nothing to catch it.
    captured_prompt = _diagram_captured_prompt_id(flow_src)
    documented_prompt = _env_example_prompt_id()
    if captured_prompt and documented_prompt and captured_prompt != documented_prompt:
        problems.append(
            f"The diagram was captured under prompt {captured_prompt!r} but backend/.env.example "
            f"documents {documented_prompt!r} as the prod prompt. A prompt id gates whole pack "
            "sections, so the diagram is drawing a different coach input than prod sends. "
            "Regenerate flow-nodes.js under the prod prompt (or update .env.example if prod "
            "itself changed).")

    # 4. Nested pack key set (#763): checks 1-3 stop at the pack root, so a field added
    #    INSIDE a section (profile.body) shipped green. Declared shape vs recorded shape.
    declared_paths = _declared_pack_key_paths()
    if len(declared_paths) < 200:
        # Same fail-loud posture as the parsers above: a walker that silently returned
        # almost nothing would turn check 4 into a check that cannot fail.
        problems.append(
            f"PARSER BROKE: walked only {len(declared_paths)} declared pack key paths "
            "(expected ~575). The nested-key guard cannot be trusted — fix it.")
    else:
        problems.extend(_pack_shape_problems(declared_paths, _recorded_pack_key_paths()))

    # 4b. Report tail tool (#962): the report coach's output contract, verbatim. The chat
    #     tools were pinned by check 6 while this one, which every report's model is
    #     handed, was not looked at at all.
    problems.extend(
        _report_tail_tool_problems(_declared_report_tail_tool(), _recorded_report_tail_tool())
    )

    # 5. Generator call signatures (#840): a generator that cannot execute must fail a
    #    check rather than pass silently.
    for generator in _GENERATORS:
        if not generator.is_file():
            problems.append(f"missing diagram generator: {generator}")
            continue
        problems.extend(
            _generator_signature_problems(generator.read_text(), generator.name))

    # 6-8. The CHAT diagram (#855). Until now the chat diagram had no contents guard at
    #      all, so `make diagram-check` was green over a diagram nothing checked.
    if not _CHAT_NODES.is_file():
        problems.append(f"missing chat diagram: {_CHAT_NODES}")
        return problems
    chat_src = _CHAT_NODES.read_text()
    chat_blob = _chat_blob(chat_src)
    declared_chat = _declared_chat_surface()
    problems.extend(
        _chat_surface_problems(
            declared_chat, _recorded_chat_surface(chat_blob) if chat_blob else None
        )
    )
    problems.extend(
        _chat_content_problems(
            _declared_chat_content(), _recorded_chat_content(chat_blob) if chat_blob else None
        )
    )
    problems.extend(_baseline_extractor_problems())
    problems.extend(_screen_builder_extractor_problems())
    problems.extend(_screen_coverage_problems())
    problems.extend(
        _chat_node_problems(
            declared_chat,
            _declared_baseline_sections(),
            _declared_screen_builders(),
            _chat_nodes(chat_src),
        )
    )
    problems.extend(_chat_capture_problems(chat_blob))

    return problems


def main() -> int:
    try:
        problems = check_drift()
    except Exception as exc:  # noqa: BLE001 — surface any failure as a guard failure
        print(f"diagram drift guard ERRORED: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("the data-flow diagrams have DRIFTED from the code:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}\n", file=sys.stderr)
        print("Fix flow-nodes.js / coach-chat-nodes.js and their generators, then re-run.",
              file=sys.stderr)
        return 1
    print("ai-flow-graph diagram is in sync with the code (pack sections + nested pack keys "
          "+ the report tail tool + DerivedMetric columns + kill-switch parity), the coach-chat diagram is in sync "
          "(tools + skills + actions + screens + which of them resolve a view + prompt "
          "slots + baseline sections + the prompt/tool/skill text itself + capture "
          "parity), and the generators still bind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
