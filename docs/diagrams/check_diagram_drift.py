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
_CHAT_NODES = _HERE / "coach-chat-nodes.js"
_GENERATOR = _HERE / "generate_flow_nodes_data.py"
_CHAT_GENERATOR = _HERE / "generate_chat_flow_data.py"
_PACK_SHAPE = _HERE / "pack-shape.json"
_ENV_EXAMPLE = _BACKEND / ".env.example"
_THREAD_TURN = _BACKEND / "app" / "services" / "coach" / "thread_turn.py"
_SCREEN_CONTEXT = _BACKEND / "app" / "services" / "coach" / "screen_context.py"

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


def _flow_blob(flow_src: str) -> dict | None:
    """The generated `const DATA = {...};` object, or None if it is missing/unparseable.

    The sibling of `_chat_blob`. Reading the blob STRUCTURALLY is the point: this
    used to be a regex scrape of `"COACH_X_ENABLED":true|false` pairs out of the
    raw JS, and that scrape read `[A-Z_]+`, so it could not see the one flag whose
    name carries a digit. A reader that quietly narrows what it reads is the
    vacuous-guard failure in its smallest form, and widening the character class
    would only have moved the next such name one character further away."""
    m = re.search(r"(?ms)^const DATA =\s*(.*?);\s*$", flow_src)
    if not m:
        return None
    try:
        blob = json.loads(m.group(1))
    except ValueError:
        return None
    return blob if isinstance(blob, dict) else None


def _diagram_captured_flags(flow_src: str) -> dict[str, bool] | None:
    """The COACH_*_ENABLED values the capture was generated under, read from the DATA
    blob's top-level `flags` object.

    Returns None when the blob or its `flags` map cannot be read at all, which the
    caller must report rather than treat as "no flags to check" -- the two are the
    same silence and only one of them is benign. Note `flags` also occurs DEEPER in
    the blob (`pack.metrics.flags` and friends are the risk-flag lists), which is
    exactly why the top-level key is addressed by path rather than by pattern."""
    blob = _flow_blob(flow_src)
    if blob is None:
        return None
    captured = blob.get("flags")
    if not isinstance(captured, dict):
        return None
    return {k: bool(v) for k, v in captured.items()
            if k.startswith("COACH_") and k.endswith("_ENABLED")}


def _env_example_flags() -> dict[str, bool]:
    """The prod-parity COACH_*_ENABLED values documented in backend/.env.example.

    The character class carries DIGITS deliberately. It read `[A-Z_]+` until
    #793, which silently skipped `COACH_PREVIOUS_30D_ENABLED` -- a flag this
    guard therefore never checked, and never said it was not checking. A reader
    that quietly narrows what it reads is the vacuous-guard failure in its
    smallest form, so if a future flag name takes a character this misses, widen
    it here rather than accepting the shorter list."""
    if not _ENV_EXAMPLE.is_file():
        return {}
    return {k: v == "true" for k, v in
            re.findall(r'^(COACH_[A-Z0-9_]+_ENABLED)=(true|false)', _ENV_EXAMPLE.read_text(), re.M)}


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


# --- the CHAT diagram (#855) ------------------------------------------------------
#
# The report pack is one declared Pydantic model, so its guard walks that model. A thread
# turn has no such model: its context is assembled per turn out of several independently
# declared sets — the tools it is handed, the skills it can load, the actions it can mint,
# the screens a pointer can name, the prompt's slots, the baseline sections. So the chat
# guard pins THOSE SETS BY NAME. That is the same principle as check 4 (compare declaration
# against a record, never against a captured value), reached through a different door.

# What each declared set is called, and what it means, in one place — so a failure message
# can say which part of the conversational surface moved.
_CHAT_SURFACE_LABELS = {
    "tools": "the tools a thread turn is handed",
    "skills": "the coaching skills it can load",
    "action_kinds": "the actions it can propose",
    "screens": "the screens a ScreenPointer can name",
    "screen_builders": "the screens that resolve a real view",
    "prompt_slots": "the system prompt's slots",
}

# The CHAT.meta booleans that mirror a COACH_*_ENABLED kill switch, so the capture's
# config can be checked against the prod-parity contract exactly as flow-nodes.js's is.
# `threads_enabled` is deliberately absent: COACH_THREADS_ENABLED is not documented in
# .env.example, and check 8 only ever compares flags that ARE.
_CHAT_META_FLAG_SETTINGS = {
    "voice_block_enabled": "COACH_VOICE_BLOCK_ENABLED",
    "memory_enabled": "COACH_MEMORY_ENABLED",
}


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


def _declared_tools() -> list[dict]:
    """The tool list a thread turn is really handed.

    Assembled through the app's OWN composition (`thread_tools(CHAT_TOOLS)` plus the skill
    tool, the expression at thread_turn's call site) rather than re-added here from its
    three parts. A guard that re-implements the decision it is checking is a second copy of
    that decision, and a tool added at the real assembly site would be invisible to it."""
    from app.services.coach.coaching_skills import LOAD_SKILL_TOOL
    from app.services.coach.proposed_actions import thread_tools
    from app.services.coach.query_tools import CHAT_TOOLS

    return [*thread_tools(CHAT_TOOLS), LOAD_SKILL_TOOL]


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


def _chat_blob(chat_src: str) -> dict | None:
    """The generated `const CHAT = {...};` object, or None if it is missing/unparseable."""
    m = re.search(r"(?m)^const CHAT = (.*);$", chat_src)
    if not m:
        return None
    try:
        blob = json.loads(m.group(1))
    except ValueError:
        return None
    return blob if isinstance(blob, dict) else None


def _recorded_chat_surface(blob: dict) -> dict[str, list[str]]:
    """The same sets as they were RECORDED when coach-chat-nodes.js was last regenerated.

    Deliberately NO sidecar, unlike check 4's pack-shape.json — and the difference is not
    an inconsistency, it follows from what each generator captures. flow-nodes.js records
    one runner's one run, so the pack's declared key set is simply not in it (an Optional
    field the runner never populated is absent from the capture, which is why diffing
    against the capture would be both noisy and unfixable) and the declaration had to be
    written down separately. generate_chat_flow_data.py records the DECLARATIONS
    THEMSELVES, verbatim: the tool JSON schemas, the skill list, the ScreenPointer and
    ProposedActionRequest JSON schemas, the prompt template. So the blob already IS the
    lockfile, and adding a sidecar would create a second record of the same facts that can
    disagree with the first. The refresh path stays the one the standing repo rule already
    requires — regenerate the diagram — and there is no flag on this guard that silences it.
    """
    tools = blob.get("tools") or {}
    schemas = blob.get("schemas") or {}

    def enum_of(schema_key: str, field: str) -> list[str]:
        schema = schemas.get(schema_key) or {}
        prop = (schema.get("properties") or {}).get(field) or {}
        return sorted(prop.get("enum") or [])

    named = [t.get("name") for t in (tools.get("data") or []) if isinstance(t, dict)]
    for key in ("action", "skill"):
        entry = tools.get(key)
        if isinstance(entry, dict) and entry.get("name"):
            named.append(entry["name"])
    return {
        "tools": sorted(n for n in named if n),
        "skills": sorted(
            s.get("name") for s in (blob.get("skills") or []) if isinstance(s, dict) and s.get("name")
        ),
        "action_kinds": enum_of("proposed_action", "action_type"),
        "screens": enum_of("screen_pointer", "screen"),
        "screen_builders": sorted(blob.get("screen_builders") or []),
        "prompt_slots": list(blob.get("slot_order") or []),
    }


def _chat_surface_problems(
    declared: dict[str, list[str]], recorded: dict[str, list[str]] | None
) -> list[str]:
    """Diff the live conversational surface against the recorded one. Pure, so the suite
    can prove it actually fails on an added tool without damaging any real file."""
    if recorded is None:
        return [
            f"{_CHAT_NODES.name} carries no readable `const CHAT = ...` blob, so what the "
            "conversational coach is served is unpinned. Regenerate the diagram: "
            "python docs/diagrams/generate_chat_flow_data.py"
        ]
    problems: list[str] = []
    for key, label in _CHAT_SURFACE_LABELS.items():
        live, was = declared.get(key) or [], recorded.get(key) or []
        # An empty declared set is a broken extractor, not a coach with no tools — and a
        # check that reads nothing is the vacuous-guard failure this whole issue is about.
        if not live:
            problems.append(
                f"EXTRACTOR BROKE: the declared set for {label} ({key}) is empty. The chat "
                "drift guard cannot be trusted — fix it.")
            continue
        if live != was:
            problems.append(
                f"The conversational coach's surface has changed but {_CHAT_NODES.name} still "
                f"records the old one — {label} ({key}): code has {live}, the diagram records "
                f"{was}. Regenerate the diagram in this same change "
                "(python docs/diagrams/generate_chat_flow_data.py).")
    return problems


# The CONTENT the diagram reproduces verbatim, beyond the names of things. Names alone are
# not the whole of what the coach receives: a tool's description, a skill's procedure and
# the system prompt's own prose are what the model actually reads, and the blob records
# every one of them in full. Without this, RULE 2 ("NEVER diagnose") could be inverted in
# the code while the diagram kept showing the old wording, green throughout.
def _declared_chat_content() -> dict[str, object]:
    from app.jobs import thread_maintenance as tm  # lazy: needs the app importable
    from app.services.coach import chat as chat_mod
    from app.services.coach import coaching_skills
    from app.services.coach import thread_turn as tt

    return {
        "the system prompt template": tt.THREAD_SYSTEM_TEMPLATE,
        "the tool definitions (description + input schema)": _declared_tools(),
        "the coaching skill procedures": [
            {"name": s.name, "use_when": s.use_when, "procedure": s.procedure}
            for s in coaching_skills.SKILLS
        ],
        "the authority-tiering briefings": {
            "header": chat_mod._TIERING_HEADER,
            "voice": chat_mod._VOICE_TIER,
            "memory": chat_mod._MEMORY_TIER,
            "corpus": chat_mod._CORPUS_TIER,
            "corpus_only": chat_mod._CORPUS_ONLY_TIER,
            "training_load": chat_mod._TRAINING_LOAD_TIER,
            "conversation": chat_mod._RELATIONSHIP_CONVERSATION_TIER,
        },
        "the floor's canned answers": {
            "medical_redirect": chat_mod.MEDICAL_REDIRECT_MESSAGE,
            "budget_paused": tt.THREAD_BUDGET_PAUSED_MESSAGE,
            "title_system": tm._TITLE_SYSTEM,
        },
    }


def _recorded_chat_content(blob: dict) -> dict[str, object]:
    tools = blob.get("tools") or {}
    recorded_tools = list(tools.get("data") or [])
    for key in ("action", "skill"):
        if isinstance(tools.get(key), dict):
            recorded_tools.append(tools[key])
    return {
        "the system prompt template": blob.get("template"),
        "the tool definitions (description + input schema)": recorded_tools,
        "the coaching skill procedures": [
            {k: s.get(k) for k in ("name", "use_when", "procedure")}
            for s in (blob.get("skills") or [])
            if isinstance(s, dict)
        ],
        "the authority-tiering briefings": blob.get("tiering"),
        "the floor's canned answers": blob.get("messages"),
    }


def _difference_hint(live: object, was: object) -> str:
    """A short, legible description of HOW two recorded artifacts differ.

    These are prompts and tool schemas — thousands of characters each — so dumping both
    sides would bury the signal. Strings report where they first diverge; dicts and lists
    report which entries moved."""
    if isinstance(live, str) and isinstance(was, str):
        at = next(
            (i for i, (a, b) in enumerate(zip(live, was)) if a != b), min(len(live), len(was))
        )
        return (
            f"they first differ at character {at} of {len(was)} -> {len(live)}: "
            f"code has ...{live[at:at + 70]!r}, the diagram records ...{was[at:at + 70]!r}"
        )
    if isinstance(live, dict) and isinstance(was, dict):
        moved = sorted(k for k in set(live) | set(was) if live.get(k) != was.get(k))
        return f"entries that differ: {moved}"
    if isinstance(live, list) and isinstance(was, list):
        if len(live) != len(was):
            return f"the diagram records {len(was)} entries, the code declares {len(live)}"
        moved = [
            (a.get("name") if isinstance(a, dict) else i)
            for i, (a, b) in enumerate(zip(live, was))
            if a != b
        ]
        return f"entries that differ: {moved}"
    return "they are not equal"


def _chat_content_problems(
    declared: dict[str, object], recorded: dict[str, object] | None
) -> list[str]:
    """Diff the verbatim content the diagram reproduces against the live code. Pure."""
    if recorded is None:
        return []  # the surface check already reported the missing blob
    problems: list[str] = []
    for label, live in declared.items():
        if not live:
            problems.append(
                f"EXTRACTOR BROKE: {label} read back empty from the code. The chat drift "
                "guard cannot be trusted — fix it.")
            continue
        was = recorded.get(label)
        if live != was:
            problems.append(
                f"{label} — what the conversational coach actually reads — has changed, but "
                f"{_CHAT_NODES.name} still shows the old text: {_difference_hint(live, was)}. "
                "Regenerate the diagram in this same change "
                "(python docs/diagrams/generate_chat_flow_data.py).")
    return problems


def _chat_nodes(chat_src: str) -> list[dict[str, str]]:
    """The hand-authored NODES entries, as {id, title, tag} triples.

    The generated `const CHAT = ...` line is dropped first: it is one 400 kB line of JSON
    and nothing in it should be mistaken for a node."""
    topology = "\n".join(
        line for line in chat_src.splitlines() if not line.startswith("const CHAT = ")
    )
    nodes: list[dict[str, str]] = []
    starts = [m.start() for m in re.finditer(r"\{\s*id:'", topology)]
    starts.append(len(topology))
    for i in range(len(starts) - 1):
        chunk = topology[starts[i]:starts[i + 1]]
        id_m = re.match(r"\{\s*id:'(\w+)'", chunk)
        if not id_m:
            continue
        title_m = re.search(r"\btitle:'([^']*)'", chunk)
        tag_m = re.search(r"\btag:'([^']*)'", chunk)
        # The explicit binding a builder node declares to the baseline section it builds
        # (`section:'schedule'`). An explicit binding rather than a `d_<key>` id convention,
        # because `d_*` is the id prefix for EVERY baseline builder — the profile, the
        # anchor, the voice — and only some of those correspond to a section. Without the
        # binding the check could only run one way, and a section REMOVED from the builder
        # would leave its node drawing an input the coach no longer gets.
        section_m = re.search(r"\bsection:'([^']*)'", chunk)
        # The same explicit binding, for the screen key whose view a resolver node draws
        # (`screen:'trends'`). Explicit for the same reason: `screen_*` prefixes the
        # resolver entry point itself as well as the per-screen builders, so an id
        # convention could only run the check one way (#871).
        screen_m = re.search(r"\bscreen:'([^']*)'", chunk)
        nodes.append({
            "id": id_m.group(1),
            "title": title_m.group(1) if title_m else "",
            "tag": tag_m.group(1) if tag_m else "",
            "section": section_m.group(1) if section_m else "",
            "screen": screen_m.group(1) if screen_m else "",
        })
    return nodes


def _chat_node_problems(
    declared: dict[str, list[str]],
    baseline_sections: list[str],
    screen_builders: list[str],
    nodes: list[dict[str, str]],
) -> list[str]:
    """Every declared conversational input must be DRAWN, and every drawn one must be real.

    This is the half a blob comparison cannot see. The blob is generated; the topology is
    hand-authored. Regenerate the blob and leave NODES alone and the new tool is in the
    data with nothing depicting it — which is exactly the stream_view class of bug check 1
    exists for on the report side."""
    problems: list[str] = []
    if len(nodes) < 40:
        return [
            f"PARSER BROKE: found only {len(nodes)} nodes in {_CHAT_NODES.name} (expected "
            "~55). The chat node-coverage guard cannot be trusted — fix it."]

    titles = {n["title"] for n in nodes}

    # Tools: one node each, titled with the tool's own name.
    declared_tools = set(declared.get("tools") or [])
    drawn_tools = {n["title"] for n in nodes if n["tag"] == "tool"}
    if missing := sorted(declared_tools - drawn_tools):
        problems.append(
            f"Tools the conversational coach is handed but that have NO node in "
            f"{_CHAT_NODES.name}: {missing}. Add a tool node and wire it into the loop.")
    if spurious := sorted(drawn_tools - declared_tools):
        problems.append(
            f"Tool nodes in {_CHAT_NODES.name} that no tool declares: {spurious}. The diagram "
            "draws a lookup the coach cannot make. Remove the node.")

    # Prompt slots: one node each, titled with the slot marker verbatim.
    declared_slots = {f"{{{s}}}" for s in (declared.get("prompt_slots") or [])}
    drawn_slots = {t for t in titles if re.fullmatch(r"\{\w+\}", t)}
    if missing := sorted(declared_slots - drawn_slots):
        problems.append(
            f"System-prompt slots with no slot node in {_CHAT_NODES.name}: {missing}. The "
            "coach reads that slot and the diagram does not show it.")
    if spurious := sorted(drawn_slots - declared_slots):
        problems.append(
            f"Slot nodes in {_CHAT_NODES.name} for slots THREAD_SYSTEM_TEMPLATE no longer "
            f"carries: {spurious}. Remove them.")

    # Baseline sections: one builder node each, bound by an explicit `section:'<key>'`.
    if not baseline_sections:
        problems.append(
            "EXTRACTOR BROKE: no relationship-baseline sections were read out of "
            "thread_turn._build_baseline_sections. The chat drift guard cannot be trusted.")
    drawn_sections = {n["section"] for n in nodes if n["section"]}
    if missing := sorted(set(baseline_sections) - drawn_sections):
        problems.append(
            "Relationship-baseline sections the thread turn assembles but that no node in "
            f"{_CHAT_NODES.name} binds: {missing}. Add a builder node declaring "
            f"section:'{missing[0]}'. A new conversational input reaches the coach undrawn — "
            "the #856 schedule class of bug.")
    if spurious := sorted(drawn_sections - set(baseline_sections)):
        problems.append(
            f"Builder nodes in {_CHAT_NODES.name} bound to baseline sections the thread turn "
            f"no longer assembles: {spurious}. The diagram draws an input the coach no "
            "longer receives. Remove the node.")

    # Screens that resolve a view: one resolver node each, bound by `screen:'<key>'` (#871).
    # The `screens` set is compared as names only, so a screen key moving from identity-only
    # to view-resolving changes what the coach is served while every name-level set still
    # matches. This is the one place that shows.
    if not screen_builders:
        problems.append(
            "EXTRACTOR BROKE: no view-resolving screens were read out of "
            "screen_context.resolve_screen_view. The chat drift guard cannot be trusted.")
    drawn_screens = {n["screen"] for n in nodes if n["screen"]}
    if missing := sorted(set(screen_builders) - drawn_screens):
        problems.append(
            "Screens that resolve a view into the prompt's LOOKING AT section but that no "
            f"node in {_CHAT_NODES.name} binds: {missing}. Add a resolver node declaring "
            f"screen:'{missing[0]}'. The coach is served that screen's contents and the "
            "diagram shows it learning only where the runner is.")
    if spurious := sorted(drawn_screens - set(screen_builders)):
        problems.append(
            f"Resolver nodes in {_CHAT_NODES.name} bound to screens that no longer resolve "
            f"a view: {spurious}. That screen is identity-only now and the diagram still "
            "draws its contents reaching the coach. Remove the node.")
    return problems


def _chat_capture_problems(blob: dict | None) -> list[str]:
    """The capture's config must be the prod-parity contract, and it must be a real capture.

    Checks 3/3b for the chat diagram. The chat generator pins its prompt with a LITERAL
    (PROD_PROMPT_ID) whose own comment says it goes stale silently — this is what makes
    that loud. A template-only blob is flagged too: the diagram's whole promise is that the
    values are real, and a regeneration run without a DB drops every one of them."""
    if blob is None:
        return []  # already reported by the surface check
    problems: list[str] = []
    meta = blob.get("meta") or {}

    captured_prompt = meta.get("coach_prompt_id")
    documented_prompt = _env_example_prompt_id()
    if not captured_prompt:
        problems.append(
            f"{_CHAT_NODES.name} records no prompt id, so which conversational context it "
            "depicts cannot be checked. Regenerate the diagram.")
    elif documented_prompt and captured_prompt != documented_prompt:
        problems.append(
            f"The chat diagram was captured under prompt {captured_prompt!r} but "
            f"backend/.env.example documents {documented_prompt!r} as the prod prompt. The "
            "prompt id gates the voice block and the pack sections the conversation inherits, "
            "so the diagram is drawing a different coach than prod runs. Regenerate "
            "coach-chat-nodes.js under the prod prompt (or update .env.example if prod "
            "itself changed).")

    documented_flags = _env_example_flags()

    # #793: the capture now records EVERY COACH_*_ENABLED flag it ran under, not
    # the two that happen to have a `meta` boolean of their own. Before this, a
    # capture labelled `prod-pinned` had fourteen of its seventeen flags riding
    # the developer's .env, unrecorded and unchecked -- and the sharpest of them
    # was COACH_RELATIONSHIP_ENABLED, which gates whether the runner's voice
    # reaches the prompt at all, so the voice slot could render or not render for
    # a reason the diagram never showed.
    captured_flags = meta.get("coach_flags")
    if not isinstance(captured_flags, dict) or not captured_flags:
        problems.append(
            f"{_CHAT_NODES.name} records no `meta.coach_flags` map, so the kill-switch "
            "configuration its capture ran under is unknown and the `prod-pinned` label "
            "cannot be checked. Regenerate the diagram: "
            "python docs/diagrams/generate_chat_flow_data.py")
        captured_flags = {}

    mismatched = sorted(
        f"{setting} (diagram={captured_flags[setting]}, .env.example={want})"
        for setting, want in documented_flags.items()
        if setting in captured_flags and bool(captured_flags[setting]) != want
    )
    # A flag .env.example documents that the capture does not record at all is
    # the same failure one step earlier: it means the generator stopped pinning
    # something the contract names.
    unrecorded = sorted(set(documented_flags) - set(captured_flags))
    if captured_flags and unrecorded:
        problems.append(
            f"{_CHAT_NODES.name} does not record {unrecorded}, which backend/.env.example "
            "documents as part of the prod-parity contract. The capture cannot claim to be "
            "prod-pinned for a flag it did not pin. Regenerate the diagram.")
    # The two legacy meta booleans must not drift away from the map beside them.
    inconsistent = sorted(
        f"{setting} (meta.{key}={meta[key]}, meta.coach_flags={captured_flags[setting]})"
        for key, setting in _CHAT_META_FLAG_SETTINGS.items()
        if key in meta and setting in captured_flags
        and bool(meta[key]) != bool(captured_flags[setting])
    )
    if inconsistent:
        problems.append(
            f"{_CHAT_NODES.name} carries two disagreeing records of the same flag: "
            f"{inconsistent}. Regenerate the diagram.")

    if mismatched:
        problems.append(
            "The chat diagram was captured under coach kill-switch flags that DISAGREE with "
            f"the prod-parity set in backend/.env.example: {mismatched}. Regenerate "
            "coach-chat-nodes.js under prod-parity config (or update .env.example if prod "
            "itself changed).")

    assembled = blob.get("assembled") or {}
    if not assembled.get("ok"):
        problems.append(
            f"{_CHAT_NODES.name} carries a TEMPLATE-ONLY capture "
            f"(assembled.ok is false: {assembled.get('reason')!r}), so every node renders "
            "'no capture' instead of what the coach was really served. It was regenerated "
            "with no seeded DB reachable. Re-run the generator against a seeded local DB "
            "(make seed-local) before committing.")
    return problems


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
          "+ DerivedMetric columns + kill-switch parity), the coach-chat diagram is in sync "
          "(tools + skills + actions + screens + which of them resolve a view + prompt "
          "slots + baseline sections + the prompt/tool/skill text itself + capture "
          "parity), and the generators still bind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
