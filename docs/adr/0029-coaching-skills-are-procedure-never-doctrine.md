# Coaching skills are procedure, never doctrine

The thread coach (ADR 0027) fields kinds of requests the report coach never had to: "plan my week", "compare these two sessions", "explain this metric", "am I ready for this race". Some of them need more than a good disposition — they need a procedure. "Write me a plan for next week" walks straight at the two hardest floors in this product: the medical-scope validator (ADR 0024) and the standing rule against recommending risky volume jumps.

The temptation is to answer that with more prompt. This project has been there: ADR 0021 pulled training philosophy *out* of the base prompt and into `corpus.py` precisely because the base was accreting doctrine, and ADR 0020 exists to keep `Voice`, `Coaching stance`, and `Coaching corpus` parallel and non-overlapping. Any new layer that carries coaching belief is a fourth axis colliding with all three.

## Decision

A **`Coaching skill`** is a named procedure for one KIND of request: when it applies, which tools to run and in what order, what must be checked before answering, the shape the answer takes, and the safety discipline that binds it.

**The boundary is the whole decision: a skill says how to conduct a turn, never what is true about training.** If a skill ever needs to assert a training belief, that belief belongs in `corpus.py` and the skill defers to it — so the runner's selected school still governs the substance of an answer a skill merely shaped. This keeps ADR 0020's axes parallel: the corpus is what the coach believes, voice is how it sounds, stance is what it foregrounds, and a skill is how it runs a multi-step request safely. None of them overlaps.

Two mechanics follow.

**1. Progressive disclosure, model-selected.** The system prompt lists each skill's name and a one-line "use when"; the model calls `load_coaching_skill(name)` to pull the full procedure. The tenth skill costs nothing on the nine turns that do not use it — the property that decides whether this mechanism is still healthy in a year, and the one an always-in-the-prompt catalogue does not have. Loading is allowed **in the same round as data fetching** (the loop already executes several `tool_use` blocks per round), so a skilled turn does not spend one of its two fetch rounds on the load; `_MAX_TOOL_ROUNDS` rises to 4 for slack. Selection stays with the model that writes the reply rather than a separate classifier, so a wrong choice is visible in the answer instead of buried in a router log.

**2. Skills are house-authored and code-resident.** A skill's text is instructions to the coach, so it lives in code alongside `corpus.py` and is never runner-supplied. Runner-supplied procedure is `User materials`, which ADR 0017 already contains as reference-the-coach-reasons-over, never instructions it obeys. A user-editable skill would hand an untrusted party the coach's operating procedure — the exact containment ADR 0017 was written to prevent, bypassed through a side door.

## Consequences

- **The initial set is near-empty and evidence-driven.** A skill earns its place from an observed failure, not from an imagined request taxonomy. `make coach-review` already pulls real production chats into `docs/audit/` for annotation, which is a proper ground-truth source for "the coach handled this kind of request badly"; a catalogue invented up front is not.
- **A skill can never widen the write set.** Proposed actions (ADR 0027) are a fixed, server-minted set. A procedure may decide *whether* to offer one, never invent one.
- **Skills are the natural future home of model routing.** If tiered model selection is ever wanted, the skill is the one place that knows a request is hard — but that is a later decision, taken on real usage data rather than ahead of it.
