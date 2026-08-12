#!/usr/bin/env python3
"""Drift guard for the ai-flow-graph data-flow diagram (docs/diagrams/flow-nodes.js).

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

  5. GENERATOR CALL SIGNATURES (#840). The generators call into backend/app to build their
     capture, so they rot when a callee's signature changes: generate_flow_nodes_data.py
     passed a `voice=` argument that #822 had removed and raised TypeError for four days
     while `make diagram-check` stayed green — the guard was policing the file's contents
     while the only tool that can produce those contents was dead. A generator needs a
     seeded DB, so CI cannot run one; but the observed failure was a SIGNATURE mismatch and
     not a data problem, so this check statically finds each call into app.* and binds it
     against the callee's REAL signature (inspect.signature.bind) without any data.

It does NOT verify edge correctness (which upstream stage feeds which node) — that is harder
to mechanise and still relies on the periodic human/agent audit. This guard's job is narrow
and high-value: no pack section or metric column can ever again reach the model with no node.

Run standalone:  python docs/diagrams/check_diagram_drift.py   (exit 1 on drift)
Also enforced by: backend/tests/test_diagram_drift.py (so CI fails on drift)
                  make diagram-check
No DB and no `node` runtime are required — pure schema introspection + text parsing.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import sys
import typing
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parents[1] / "backend"
_FLOW_NODES = _HERE / "flow-nodes.js"
_GENERATOR = _HERE / "generate_flow_nodes_data.py"
_CHAT_GENERATOR = _HERE / "generate_chat_flow_data.py"
_PACK_SHAPE = _HERE / "pack-shape.json"
_ENV_EXAMPLE = _BACKEND / ".env.example"

# The generators whose calls into backend/app must still bind (check 5). Both build their
# capture by importing the real pipeline, so both rot the same way; generate_example_chats.py
# is excluded because it drives the live API rather than importing the backend.
_GENERATORS = (_GENERATOR, _CHAT_GENERATOR)

# CoachContextPack fields that intentionally have NO node in the diagram. Keep this list
# tiny and documented — every entry is a field the model carries in the schema but that is
# deliberately not a drawn section.
_PACK_NOT_SHOWN = {
    # #451: retired legacy summary — no longer populated or serialized, kept only as an
    # Optional schema field so pre-#451 stored packs still validate. Never reaches the model.
    "recent_training_summary",
    # M4 (ADR 0025): the retired belief / preference / narrative durable-memory sections.
    # Kept as never-populated Optional stubs so a pre-M4 stored pack still parses; never
    # serialized, never reach the model, so they intentionally have no node.
    "believed_facts",
    "preference_profile",
    "narrative",
}

# DerivedMetric columns that are not "shown" data fields (identity / FKs / timestamps).
_DM_NON_DATA = {"id", "activity_id", "created_at", "updated_at"}


def _canonical_pack_sections() -> set[str]:
    """The FLAT pack sections the live CoachContextPack schema can emit to the LLM.

    ADR 0026 grouped the schema's top-level fields into five coaching-question groups
    (this_run/right_now/…), but the SERIALIZED pack the LLM receives still carries the
    flat sections (to_serializable_dict), and the diagram depicts that flat data flow.
    So the drift guard enumerates the flat section universe — every section relocated
    into a group (_SECTION_GROUP) plus the top-level meta fields (salience/safety_rules/
    retired stubs), excluding the group container fields — not the grouped top level. A
    genuinely new pack section still trips the guard: it is declared on a group model
    and wired into _SECTION_GROUP, so it appears here with no p_* node."""
    from app.schemas.coach_context import (  # lazy: needs the app importable
        CoachContextPack,
        _GROUP_NAMES,
        _SECTION_GROUP,
    )

    top_level_meta = set(CoachContextPack.model_fields.keys()) - set(_GROUP_NAMES)
    return set(_SECTION_GROUP) | top_level_meta


def _models_in(annotation) -> list:
    """Every Pydantic model reachable from a field annotation.

    Unwraps the containers the pack actually uses — Optional[X], List[X], Dict[str, X],
    Union[A, B] — recursively, so `Optional[List[BlockMember]]` yields BlockMember. An
    opaque `Dict[str, Any]` blob yields nothing, which is correct: it has no declared
    keys, so there is nothing for this guard to pin."""
    from pydantic import BaseModel  # lazy: needs the app's deps importable

    found = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        found.append(annotation)
    for arg in typing.get_args(annotation) or ():
        found.extend(_models_in(arg))
    return found


def _walk_model(model, prefix: str, seen: frozenset) -> list[str]:
    """Dotted key paths for every declared field of `model` and its nested models.

    `seen` guards against a self-referential model looping forever. A list-of-model
    field contributes ONE path per declared field (`block.members.type`), not one per
    element — the pack's key set is about what keys exist, not how many rows carry them.
    """
    paths: list[str] = []
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}"
        paths.append(path)
        for sub in _models_in(field.annotation):
            if sub in seen:
                continue
            paths.extend(_walk_model(sub, path, seen | {sub}))
    return paths


def _declared_pack_key_paths() -> list[str]:
    """Every key the coach context pack CAN carry, to full nesting depth, as dotted paths.

    Source is the DECLARATION (the Pydantic models), deliberately NOT the captured
    `DATA.pack` in flow-nodes.js, for two reasons that together decide the whole design
    of check 4:

      * The capture is one real runner's one real run, so most Optional fields are simply
        absent from it — `profile.body` is absent right now because that runner has stated
        no build, `calibration` and `block` drop for a solo run, a kill-switched section
        drops entirely. Diffing against the capture would fire on all of those, which is
        the "noisy enough that people regenerate reflexively" failure the issue names, AND
        it would be unfixable: regenerating cannot conjure a value the runner never stated.
      * Declaration-vs-declaration means value churn contributes exactly nothing. A capture
        taken from a different activity, a different day, a different runner produces the
        same key set. That is what makes it safe to walk to FULL depth rather than stopping
        at one level: depth costs no noise here, and `profile.body.weight_kg` — the #742
        field this check exists for — is two levels down.

    So the recorded half (pack-shape.json, rewritten by the generator) is a lockfile on the
    pack's shape: change what the coach can receive and the guard fails until the diagram is
    regenerated, which is the standing repo rule this check makes enforceable.

    Sections in _PACK_NOT_SHOWN are excluded — they are never serialized, so their fields
    never reach the coach and pinning them would only add churn."""
    from app.schemas.coach_context import (  # lazy: needs the app importable
        CoachContextPack,
        _GROUP_NAMES,
    )

    # ADR 0026: a flat section is declared either on one of the five group models or as a
    # top-level meta field. Same universe check 1 enumerates, but keeping the ANNOTATION
    # so the nested models can be walked.
    section_annotations: dict = {}
    for group in _GROUP_NAMES:
        group_model = CoachContextPack.model_fields[group].annotation
        for name, field in group_model.model_fields.items():
            section_annotations[name] = field.annotation
    for name, field in CoachContextPack.model_fields.items():
        if name not in _GROUP_NAMES:
            section_annotations[name] = field.annotation

    paths: set[str] = set()
    for name, annotation in section_annotations.items():
        if name in _PACK_NOT_SHOWN:
            continue
        paths.add(name)
        for model in _models_in(annotation):
            paths.update(_walk_model(model, name, frozenset({model})))
    return sorted(paths)


def _recorded_pack_key_paths() -> list[str] | None:
    """The pack key set recorded when the diagram was last regenerated, or None if the
    sidecar is missing/unreadable (which is itself drift — the guard reports it)."""
    if not _PACK_SHAPE.is_file():
        return None
    try:
        blob = json.loads(_PACK_SHAPE.read_text())
    except ValueError:
        return None
    paths = blob.get("paths") if isinstance(blob, dict) else None
    return sorted(paths) if isinstance(paths, list) else None


def write_pack_shape(paths: list[str] | None = None) -> Path:
    """Rewrite pack-shape.json from the live declaration. Called by the generator, so
    regenerating the diagram is the ONE supported way to refresh the lockfile — there is
    deliberately no flag on this guard that would let someone silence check 4 without
    regenerating the diagram the check exists to keep current."""
    paths = _declared_pack_key_paths() if paths is None else paths
    _PACK_SHAPE.write_text(
        json.dumps(
            {
                "_note": (
                    "Every key the coach context pack can carry, to full nesting depth, as "
                    "dotted paths, recorded when flow-nodes.js was last regenerated. Read by "
                    "docs/diagrams/check_diagram_drift.py (#763) so a field added INSIDE an "
                    "existing pack section cannot ship with the diagram unregenerated. "
                    "Rewritten by generate_flow_nodes_data.py — do not hand-edit."
                ),
                "paths": paths,
            },
            indent=1,
        )
        + "\n"
    )
    return _PACK_SHAPE


def _pack_shape_problems(declared: list[str], recorded: list[str] | None) -> list[str]:
    """Diff the live declared pack key set against the recorded one. Pure, so the suite
    can prove the check actually fails on an added key without touching the real files."""
    if recorded is None:
        return [
            f"{_PACK_SHAPE.name} is missing or unreadable, so the pack's nested key set is "
            "unpinned and a new coach input can ship undrawn (the #742 class of bug). "
            "Regenerate the diagram: python docs/diagrams/generate_flow_nodes_data.py"
        ]
    problems: list[str] = []
    added = sorted(set(declared) - set(recorded))
    removed = sorted(set(recorded) - set(declared))
    if added:
        problems.append(
            "Pack keys the coach can now receive that are NOT in the recorded shape "
            f"({_PACK_SHAPE.name}): {added}. A new coach input reaches the model while the "
            "diagram still depicts the old pack. Regenerate the diagram in this same change "
            "(python docs/diagrams/generate_flow_nodes_data.py), which rewrites both "
            "flow-nodes.js and the recorded shape."
        )
    if removed:
        problems.append(
            f"Pack keys recorded in {_PACK_SHAPE.name} no longer exist on CoachContextPack: "
            f"{removed}. The diagram depicts data the coach can no longer receive. "
            "Regenerate the diagram."
        )
    return problems


def _imported_app_names(tree: ast.AST) -> dict[str, tuple[str, str | None]]:
    """Map each locally-bound name to the `app.*` module/attribute it was imported from.

    Walks the WHOLE tree, so the function-body imports the generators use (deferred to keep
    module import cheap) are bound too. Collisions across scopes collapse into one flat
    namespace, which is fine here: these are single-purpose scripts that import a name once."""
    bound: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module or node.module.split(".")[0] != "app":
                continue
            for alias in node.names:
                bound[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "app":
                    bound[alias.asname or alias.name] = (alias.name, None)
    return bound


def _resolve_imported(module: str, attr: str | None):
    """Import `module` and return `attr` off it — falling back to importing `module.attr`
    as a submodule, which is how `from app.services.coach import chat as chat_mod` binds."""
    imported = importlib.import_module(module)
    if attr is None:
        return imported
    try:
        return getattr(imported, attr)
    except AttributeError:
        return importlib.import_module(f"{module}.{attr}")


def _generator_signature_problems(source: str, label: str) -> list[str]:
    """Bind every call the generator makes into `app.*` against the callee's real signature.

    This is the #840 check: the generators need a seeded DB, so CI cannot run them, but the
    failure that went unnoticed for four days (`build_system_prompt(..., voice=...)` after
    #822 removed the parameter) needed no data to detect — only the callee's signature.

    Two call shapes are resolved: a directly imported name (`build_system_prompt(...)`) and
    one attribute off an imported name or module (`Classification.from_metrics(...)`,
    `query_tools.get_session_detail(...)`), which covers everything the generators do.
    Binding is STRICT (`bind`, which also catches a newly required parameter the call site
    does not pass) unless the call unpacks `*args`/`**kwargs`, where only the arguments that
    are actually visible can be checked (`bind_partial`).

    Arguments bind as an opaque placeholder: this proves the call SHAPE is still legal —
    arity and keyword names, which is what drifts — not that the values have the right types.
    """
    placeholder = object()
    problems: list[str] = []
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as exc:
        return [f"{label} does not parse: {exc}"]

    bound = _imported_app_names(tree)

    # An import that no longer resolves is the same failure class as a bad call site (the
    # generator dies at import), and it is the shape a DELETED callee takes, so check every
    # binding even if the generator never calls it.
    resolved: dict[str, object] = {}
    for name, (module, attr) in sorted(bound.items()):
        try:
            resolved[name] = _resolve_imported(module, attr)
        except Exception as exc:  # noqa: BLE001 — any import failure is a dead generator
            problems.append(
                f"{label} imports {attr or module!r} from {module!r}, which no longer "
                f"resolves ({exc}). The generator cannot run."
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in resolved:
            callee, shown = resolved[func.id], func.id
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in resolved
        ):
            owner = resolved[func.value.id]
            shown = f"{func.value.id}.{func.attr}"
            callee = getattr(owner, func.attr, None)
            if callee is None:
                problems.append(
                    f"{label}:{node.lineno} calls {shown}(), but {func.attr!r} no longer "
                    f"exists on {func.value.id!r}. The generator cannot run."
                )
                continue
        else:
            continue
        if not callable(callee):
            continue
        try:
            signature = inspect.signature(callee)
        except (ValueError, TypeError):
            continue  # a builtin/C callable with no introspectable signature
        positional = [placeholder for a in node.args if not isinstance(a, ast.Starred)]
        keywords = {k.arg: placeholder for k in node.keywords if k.arg is not None}
        unpacks = len(positional) != len(node.args) or any(
            k.arg is None for k in node.keywords
        )
        try:
            if unpacks:
                signature.bind_partial(*positional, **keywords)
            else:
                signature.bind(*positional, **keywords)
        except TypeError as exc:
            call = ", ".join(["_"] * len(positional) + [f"{k}=_" for k in keywords])
            problems.append(
                f"{label}:{node.lineno} calls {shown}({call}), which no longer binds against "
                f"the real signature {shown}{signature}: {exc}. The generator cannot run, so "
                "the diagram cannot be regenerated — fix the call site."
            )
    return problems


def _canonical_derived_columns() -> set[str]:
    """The live DerivedMetric data columns (identity/timestamps excluded)."""
    from app.models import DerivedMetric  # lazy: needs the app importable

    return {c.name for c in DerivedMetric.__table__.columns} - _DM_NON_DATA


def _pack_keys_bound_by_nodes(src: str) -> set[str]:
    """Each `p_*` node renders exactly one pack section, bound either via a
    `jProv('<key>')` / `jTallProv('<key>')` provenance render or, for the few nodes that
    render P directly, via the first `P.<key>` reference. Extract that binding for every
    pack node so we can diff node coverage against the schema."""
    keys: set[str] = set()
    # Split the NODES text on node boundaries, then for each `id:'p_xxx'` chunk take its
    # section binding: prefer the explicit jProv('key') form, else the first `P.<key>`.
    node_starts = [m.start() for m in re.finditer(r"\{\s*id:'", src)]
    node_starts.append(len(src))
    for i in range(len(node_starts) - 1):
        chunk = src[node_starts[i]:node_starts[i + 1]]
        id_m = re.match(r"\{\s*id:'(p_\w+)'", chunk)
        if not id_m:
            continue
        # jProv('k') / jTallProv('k') and their per-field-override variants jProvX('k',{...}) /
        # jTallProvX('k',{...}); else fall back to the first P.<key> the node renders.
        key_m = re.search(r"\bj(?:Tall)?ProvX?\('(\w+)'", chunk) or re.search(r"\bP\.(\w+)", chunk)
        if key_m:
            keys.add(key_m.group(1))
    return keys


def _fate_derived_keys(src: str) -> set[str]:
    """Keys covered by the FATE_DERIVED map: the `[...].forEach` array literal plus every
    explicit `m.<key>=` assignment in the IIFE."""
    block_m = re.search(r"const FATE_DERIVED\s*=\s*\(\(\)=>\{([\s\S]*?)return m;\s*\}\)\(\);", src)
    if not block_m:
        return set()
    block = block_m.group(1)
    keys: set[str] = set()
    arr_m = re.search(r"\[([\s\S]*?)\]\s*\.forEach", block)
    if arr_m:
        keys |= set(re.findall(r"'(\w+)'", arr_m.group(1)))
    keys |= set(re.findall(r"\bm\.(\w+)\s*=", block))
    return keys


def _diagram_captured_flags(flow_src: str) -> dict[str, bool]:
    """The COACH_*_ENABLED values the capture was generated under, read from the DATA blob's
    `flags` object. These `"COACH_X_ENABLED":true|false` pairs appear ONLY inside that object
    (the JS logic references the flags as single-quoted string literals, never `"...":bool`)."""
    return {k: v == "true" for k, v in
            re.findall(r'"(COACH_[A-Z_]+_ENABLED)":(true|false)', flow_src)}


def _env_example_flags() -> dict[str, bool]:
    """The prod-parity COACH_*_ENABLED values documented in backend/.env.example."""
    if not _ENV_EXAMPLE.is_file():
        return {}
    return {k: v == "true" for k, v in
            re.findall(r'^(COACH_[A-Z_]+_ENABLED)=(true|false)', _ENV_EXAMPLE.read_text(), re.M)}


def _diagram_captured_prompt_id(flow_src: str) -> str | None:
    """The prompt id the capture was generated under, from the DATA blob's `meta`."""
    m = re.search(r'"prompt_id":"([A-Za-z0-9_]+)"', flow_src)
    return m.group(1) if m else None


def _env_example_prompt_id() -> str | None:
    """The prod-parity COACH_PROMPT_ID documented in backend/.env.example."""
    if not _ENV_EXAMPLE.is_file():
        return None
    m = re.search(r'^COACH_PROMPT_ID=(\S+)', _ENV_EXAMPLE.read_text(), re.M)
    return m.group(1) if m else None


def _generator_dm_fields(src: str) -> set[str]:
    """The `_DM_FIELDS` list the generator uses to render the DerivedMetric node."""
    m = re.search(r"_DM_FIELDS\s*=\s*\[([\s\S]*?)\]", src)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


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
            "The diagram carries NO captured COACH_*_ENABLED flags — regenerate it under the "
            "prod-parity config (backend/.env mirroring .env.example) so the off-state chips are "
            "capture-driven. See generate_flow_nodes_data.py.")
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

    # 5. Generator call signatures (#840): a generator that cannot execute must fail a
    #    check rather than pass silently.
    for generator in _GENERATORS:
        if not generator.is_file():
            problems.append(f"missing diagram generator: {generator}")
            continue
        problems.extend(
            _generator_signature_problems(generator.read_text(), generator.name))

    return problems


def main() -> int:
    try:
        problems = check_drift()
    except Exception as exc:  # noqa: BLE001 — surface any failure as a guard failure
        print(f"diagram drift guard ERRORED: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("ai-flow-graph diagram has DRIFTED from the code:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}\n", file=sys.stderr)
        print("Fix flow-nodes.js / generate_flow_nodes_data.py, then re-run.", file=sys.stderr)
        return 1
    print("ai-flow-graph diagram is in sync with the code (pack sections + nested pack keys "
          "+ DerivedMetric columns + kill-switch parity), and the generators still bind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
