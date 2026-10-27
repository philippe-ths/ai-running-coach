"""Checks 6-8 of the diagram drift guard: the CHAT diagram (coach-chat-nodes.js).

Split out of check_diagram_drift.py, whose docstring describes every check;
check_drift there runs them and re-exports every name here. The readers that
tests monkeypatch on that module (the screen resolver, the baseline builder and
the generator screen coverage) stay there, so a patch still reaches its callers.
"""
from __future__ import annotations

import json
import re

from diagram_drift_paths import (
    _CHAT_NODES,
)
from diagram_drift_report import (
    _env_example_flags,
    _env_example_prompt_id,
    _difference_hint,
)


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
    for key in ("action", "search", "skill"):
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
    for key in ("action", "search", "skill"):
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
        # The node's UPSTREAM list. Edges are declared on the CONSUMER
        # (`from:['d_schedule']` on the slot node), so the set of all `from`
        # entries is the set of nodes something downstream actually reads.
        from_m = re.search(r"\bfrom:\[([^\]]*)\]", chunk)
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
            "from": re.findall(r"'(\w+)'", from_m.group(1)) if from_m else [],
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

    # A drawn node is not a wired one (#859). Node coverage runs both ways and still
    # passed while `d_running_norm` sat as a dead end: it was drawn, its section was
    # declared, and nothing asked whether anything downstream READ it — so the page
    # said "END OF CHAIN — nothing downstream consumes this" about a section the coach
    # reads on every turn. Every section builder feeds `{baseline_block}`, so every one
    # of them must be named in some node's `from`.
    consumed = {src for node in nodes for src in node["from"]}
    if not consumed:
        problems.append(
            f"PARSER BROKE: no `from:[...]` edge was read out of {_CHAT_NODES.name}, so the "
            "edge-reachability check cannot fail. Fix the parser.")
    elif orphans := sorted(
        node["id"] for node in nodes if node["section"] and node["id"] not in consumed
    ):
        problems.append(
            f"Builder nodes in {_CHAT_NODES.name} that no other node consumes: {orphans}. "
            "The section reaches the coach through {baseline_block}, but the diagram draws "
            "it as a dead end. Add the node's id to the `from:[...]` of the slot node it "
            "feeds.")

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
