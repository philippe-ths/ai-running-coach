"""Checks 1-5 of the diagram drift guard: the REPORT diagram (flow-nodes.js).

Split out of check_diagram_drift.py, whose docstring describes every check;
check_drift there runs them and re-exports every name here.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import re
import typing
from pathlib import Path

from diagram_drift_paths import (
    _PACK_SHAPE,
    _ENV_EXAMPLE,
)


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


def _declared_report_tail_tool() -> dict:
    """The report coach's whole structured output contract, as the model receives it.

    The report LLM is handed exactly one tool, `record_coach_tail`, and its name,
    description and every property's prose are instructions the model reads on every
    report. JSON round-trip so the comparison sees what the API is sent, not Python
    tuple-versus-list differences.

    Not versioned per prompt, unlike the system prompt (prompt_clauses.py, archived in
    prompt_archive.py): it is one hand-frozen dict shared by every prompt id. That is why
    it is pinned here, beside the pack shape, rather than captured per prompt — the
    omission before #962 was an oversight, not a choice."""
    from app.services.coach.output_contract import (  # lazy: needs the app importable
        RECORD_COACH_TAIL_TOOL,
    )

    return json.loads(json.dumps(RECORD_COACH_TAIL_TOOL))


def _recorded_report_tail_tool() -> dict | None:
    """The tail tool recorded in pack-shape.json, or None if absent (itself drift)."""
    if not _PACK_SHAPE.is_file():
        return None
    try:
        blob = json.loads(_PACK_SHAPE.read_text())
    except ValueError:
        return None
    tool = blob.get("report_tail_tool") if isinstance(blob, dict) else None
    return tool if isinstance(tool, dict) else None


def _report_tail_tool_problems(declared: dict, recorded: dict | None) -> list[str]:
    """Diff the live tail tool against the recorded one, verbatim. Pure.

    Verbatim, not by shape: #944 added an `offer` property with ~700 characters of
    instruction, and a later edit can rewrite that prose without changing any key."""
    if not declared:
        return [
            "EXTRACTOR BROKE: the report tail tool read back empty from the code. The "
            "report-contract pin cannot be trusted — fix it."
        ]
    if recorded is None:
        return [
            f"{_PACK_SHAPE.name} records no report tail tool, so the report coach's output "
            "contract (record_coach_tail) is unpinned. Regenerate the diagram: "
            "python docs/diagrams/generate_flow_nodes_data.py"
        ]
    if declared == recorded:
        return []
    return [
        "The report coach's tail tool (record_coach_tail) — what every report's model is "
        f"told to output — has changed, but {_PACK_SHAPE.name} still records the old one: "
        f"{_difference_hint(declared, recorded)}. Regenerate the diagram in this same change "
        "(python docs/diagrams/generate_flow_nodes_data.py)."
    ]


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
                    "dotted paths, and the report coach's record_coach_tail tool verbatim, "
                    "recorded when flow-nodes.js was last regenerated. Read by "
                    "docs/diagrams/check_diagram_drift.py (#763, #962) so a field added "
                    "INSIDE an existing pack section, or a change to what the report model "
                    "is told to output, cannot ship with the diagram unregenerated. "
                    "Rewritten by generate_flow_nodes_data.py — do not hand-edit."
                ),
                "paths": paths,
                "report_tail_tool": _declared_report_tail_tool(),
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

