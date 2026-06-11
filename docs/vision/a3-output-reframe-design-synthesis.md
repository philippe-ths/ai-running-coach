# A3 Output-Reframe: Design Synthesis

> Status: synthesis of the completed design panel (2026-06-10). Inputs: three candidates (Dual-Track, Message-First from the salvaged 2026-06-09 run in `a3-output-reframe-design-candidates.md`; In-Place Reframe generated 2026-06-10), an Anthropic API fact-check against current docs, and three judge verdicts (loop-and-safety, migration-and-ops, vision-fidelity lenses). The judge round completed; the panel's own synthesis step was cut for quota and this document was written by the main session from the judges' structured verdicts. Seeds ADR 0009 and the A3 build brief; owner forks below are open until ratified.

## Scoreboard

| Candidate | Loop & safety | Migration & ops | Vision fidelity | Total | Fatal flaws |
|---|---|---|---|---|---|
| Evolution (In-Place Reframe) | 9 | 9 | 8 | 26 | 0 |
| Message-First (new exchanges table) | 5 | 4 | 6.5 | 15.5 | 1 per judge |
| Dual-Track (claims-array tail) | 6 | 5 | 4 | 15 | 1 per judge |

All three judges independently reached the same ranking. Both salvaged candidates carry the same fatal flaw as written: drafted against a stale snapshot (prompt v8, five validator rules, seven eval assertions, pre-A2d adherence gather), they omit validator rule 6 (narrative-as-evidence, the A2c authority boundary) from their new validation entry points. Repairable staleness, but as submitted each violates the never-weaken-the-gate constraint. The evolution candidate is the only one whose integration claims were verified true against the shipped code.

## Recommended design: Evolution (In-Place Reframe), with grafts

The winner's skeleton, in one paragraph: one Anthropic Messages call with adaptive thinking produces, in token order, private reasoning, then the coach's prose `message` (markdown text blocks, the product, unconstrained), then exactly one strict-schema tool call `record_coach_tail` carrying a thin bookkeeping tail (`headline`, 0-3 `next_steps` byte-shaped to today's `action`/`details`/`why`, `risks` flag declarations, `questions` with tappable options). Message and tail merge into a `CoachMessageReport` dict stored in the existing `coach_reports.report` JSON column under a new cache identity `(prompt_id="coach_message_v1", schema_version="2.0")`. Zero migrations, zero new tables or columns. Because `next_steps` keeps the same key and field shape, `retrieval.fetch_prior_commitments`, all of `adherence.py`, `chat.py`, and `write_back_beliefs` run verbatim (verified against the code); `digest.py` gains one branch; the validator's six rule bodies are refactored into shared functions feeding both a byte-equivalent legacy entry point and a new `validate_message_policy` whose policed surface is the full prose plus every tail text field. `SCHEMA_VERSION` becomes a prompt-family map so cutover and rollback are each a pure `COACH_PROMPT_ID` config flip with cached rows intact on both sides. Full detail in the third candidate ("In-Place Reframe") in `a3-output-reframe-design-candidates.md`.

Grafts from the runners-up:

1. **Typed tappable options (from Message-First).** Replace the evolution candidate's bare-string `options` with Message-First's `TappableOption {id, label, kind: rpe|pain|reply|dispute|custom, payload}`. This is the real I1 contract (typed quick RPE/pain capture), costs near nothing now, and closes the winner's main I1 gap without building any delivery surface (chips render label only in A3; Telegram inline keyboards stay I1 scope). Credited to the vision judge's explicit synthesis suggestion.
2. **Order-agnostic parsing and stop_reason discipline (from Message-First + fact-check).** Parse text blocks and the single tool_use wherever they appear; branch distinctly on `stop_reason` values `end_turn` (tail skipped, corrective retry), `max_tokens` (truncated, retry), and `refusal` (strict guarantee void, fallback). Use `messages.stream().get_final_message()`, the docs-recommended call path.
3. **Atomicity in one direction only (already in the winner, ratified against Dual-Track).** A tail is never stored without its message, but a real message survives a tail failure as `meta.tail_degraded=true` with empty next_steps (M7 abstains, the existing discipline). Dual-Track's full atomicity (discard good prose over bookkeeping) is rejected: the message is the product.
4. **No claims array (ratified against Dual-Track).** The tail carries affordances and memory hooks, never content, per decision 4's own words. Per-claim evidence survives only on `next_steps`. This is the traceability-for-humanity trade taken deliberately; the ADR records it.

## Corrections from the API fact-check

Everything the candidates depend on is real and GA: adaptive thinking, strict tools, `tool_choice: auto` with prose before the tool call, prompt-cache layout (tools then system). The corrections that change the design text:

- **Nothing is Opus-gated.** Adaptive thinking, strict tools, and effort all work on the current prod `claude-sonnet-4-6`. Build the call to the stricter Opus parameter surface (no `temperature`/`top_p`/`top_k`, `thinking={"type":"adaptive"}`, no prefills) and the Sonnet/Opus choice stays a pure `COACH_MODEL_ID` config flip. Both salvaged candidates wrongly treated Opus 4.8 as decided; Message-First hardcoded it.
- **`tool_choice: auto` is mandatory, not a preference.** Forced tool_choice (`any`/`tool`) both suppresses preceding prose and is an outright API error with extended thinking. Auto + prompt mandate + one corrective retry is the only viable mechanism, so the skip-the-tail failure mode is inherent and the degraded-tail path is required, not optional.
- **`max_tokens` must rise to 8-16K.** Thinking tokens count inside `max_tokens`; at 4000-4096 (all three candidates) truncation kills the tail first, since it generates last, directly inflating the design's own health metric. Log `stop_reason` and `usage`.
- **The 60s client timeout needs re-baselining** under adaptive thinking; streaming removes the SDK timeout class.
- **Strict-schema subset forbids string maxLength**, so the headline's <=80-char bound lives in the prompt, not the schema. Hand-frozen tool JSON (not runtime Pydantic generation) keeps the cache prefix byte-stable and avoids server-side schema recompilation.
- **Thinking display defaults differ** (Sonnet `summarized`, Opus `omitted`); set `display` explicitly so behaviour does not change across a model flip. Thinking blocks need no preservation (single one-shot call, no tool_result continuation).

## Open forks for the owner

Six were raised across the judges; three are resolved by the synthesis itself (tail content: no claims array; atomicity: degrade-not-withhold; option shape: typed TappableOption graft). Three remain genuinely open and are put to the owner alongside this document:

1. **Medical floor hardening.** A `medical_overreach` violation that survives the policy retry currently stores with a flag; the winner promotes it to forced fallback since prose now renders verbatim. Stricter, never looser; the cost is that a rare rule-5 false positive withholds a whole report. Recommendation: adopt.
2. **Exchange record now or at A1.** Message-First pre-pays the `exchanges` table; the winner keeps the demoted `CoachReport` row and accepts a possible re-homing when A1 (Block/Exchange model) lands. Recommendation: defer to A1, where the north-star puts the event-unit data model; A3 ships the reframe cheapest and A4's storage needs (two-stage turns) inform the real record shape.
3. **Model choice.** Pure config flip by construction. Recommendation: ship A3 on `claude-sonnet-4-6`, evaluate the prose, and treat flat prose as a model question (the winner's risk 4) before paying Opus prices.

## Rejected alternatives and why

**Dual-Track** is rejected primarily for its tail: a mandatory 1-6 ranked evidence-backed claims array rebuilds the structured analytical report inside the tail, keeping report-as-protagonist DNA and pressuring the model back toward form-shaped prose, the exact failure A3 exists to kill. Its full atomicity discards good coaching over bookkeeping, its rollback as designed is broken (the single SCHEMA_VERSION constant swap would orphan every cached legacy row on rollback), and it was drafted stale. Its strengths (same-column storage, byte-equivalent legacy validator) are already in the winner.

**Message-First** is rejected for A3 on cost and staleness, not on vision: it has the best protagonist fidelity and the best A4/I1 setup, but the new table orphans the shipped A2 retrieval seam (`fetch_prior_digests`, `fetch_prior_commitments`, `fetch_recent_user_digests` all read `coach_reports`; A2c consolidation would silently freeze on legacy digests), forces permanent two-table union adapters, has the largest blast radius of the three (hard to credit as one issue + one PR), hardcodes the model, and its runtime-generated strict schema is invalid as written. Its typed TappableOption and its stop_reason/streaming discipline are grafted into the winner; its `exchanges` table idea is deferred to A1 where it belongs.

## Constraint compliance check

1. Machine-readable commitments: tail `next_steps` stored at the same JSON key with the same dict shape; `fetch_prior_commitments` and M7 run verbatim. By construction.
2. Validator never weakened: all six rules mapped; policed surface strictly grows to include the prose; legacy entry point byte-equivalent, pinned by an equivalence test; medical floor strengthened (pending fork 1).
3. Byte-stable prompts: `coach_report_v1`..`v10` untouched; new family `coach_message_v1`.
4. Versioned cache retained: same `(activity_id, prompt_id, schema_version)` identity; flip is a clean cache miss; all history kept; family map makes rollback config-only.
5. Eval continuity: extractor/loader branches plus dual-shape selftest fixtures; step-1 refactor gated by an identical pre/post v10 scorecard; cutover gated by `compare_scorecards` on seeded regenerated data with re-baselined lexical thresholds.
6. Write-back stays LLM-free: `write_back_beliefs` never reads the report body; `test_writeback_is_llm_free.py` passes unmodified.
7. Additive-only migrations: vacuously satisfied; zero schema changes.
8. Narrative authority boundary: validator rule 6 scans tail evidence paths; prompt rule 24 discipline carried into `coach_message_v1`; the documented residual (prose sentences carry no field paths, so prose narrative-leakage is prompt-and-eval governed) is named in the ADR.

## Known residual risks (carried into the ADR)

- Prose/tail divergence is the shared, deterministically-uncheckable blind spot: a commitment phrased only in prose exits the M7 loop. Bounded by the mirroring prompt rule, M7's conservative abstention, the thin tail (smallest divergence surface of the three designs), and eval spot-checks; the #164 LLM-judge is the future mitigation.
- Eval lexical thresholds (parrot Jaccard, trend/discount terms, the question carve-out moving to a ?-sentence heuristic) were tuned on terse fields and must be re-baselined on seeded prose before the flip.
- `tail_degraded` rate is the headline health metric across cutover; persistent elevation starves M7/M8 silently and is surfaced as a scorecard counter.
- Flat prose on Sonnet 4.6 after an otherwise green cutover is a model question, not an architecture failure; diagnose it as fork 3 before touching the design.
