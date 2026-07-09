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

It does NOT verify edge correctness (which upstream stage feeds which node) — that is harder
to mechanise and still relies on the periodic human/agent audit. This guard's job is narrow
and high-value: no pack section or metric column can ever again reach the model with no node.

Run standalone:  python docs/diagrams/check_diagram_drift.py   (exit 1 on drift)
Also enforced by: backend/tests/test_diagram_drift.py (so CI fails on drift)
                  make diagram-check
No DB and no `node` runtime are required — pure schema introspection + text parsing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parents[1] / "backend"
_FLOW_NODES = _HERE / "flow-nodes.js"
_GENERATOR = _HERE / "generate_flow_nodes_data.py"
_ENV_EXAMPLE = _BACKEND / ".env.example"

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
    """The pack sections the live CoachContextPack schema can emit to the LLM."""
    from app.schemas.coach_context import CoachContextPack  # lazy: needs the app importable

    return set(CoachContextPack.model_fields.keys())


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
    print("ai-flow-graph diagram is in sync with the code (pack sections + DerivedMetric columns).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
