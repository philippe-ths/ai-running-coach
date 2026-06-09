# Coach Memory and Retrieval (A2) — Build Brief

> Execution-facing companion to `docs/vision/coach-memory-retrieval-design.md` (the *what* and *why* of the memory model) and `docs/adr/0008-coach-memory-is-a-four-layer-pull-model.md` (the decision). This brief holds the *in what order*: milestones with explicit scope, intent, test discipline, success criteria, dependencies, and the skills that apply. Same shape as `coach-report-update-brief.md`.
>
> **Scope discipline.** A2 is **memory and retrieval only.** It assumes "a block is one activity" (A1/Block deferred), keeps the structured `CoachReport` as the exchange record (A3 output reframe deferred), and does not own *when* the coach speaks (A4 cadence). See design doc §6.
>
> **Boundary discipline.** Items flagged **[ASK-FIRST]** touch schema, migrations, a new background job, or a public contract and must be approved by the product owner before implementation (per `ai-workflow.md`). The deterministic policy validator gate is never bypassed. Previews share the production DB, so every migration is verified with that hazard in mind.

---

## How to use this brief

Each milestone is a coherent shippable unit: one issue, one PR, TDD throughout. Work them in dependency order. Before starting any milestone:

1. Confirm its **[ASK-FIRST]** items are approved.
2. Run `aiw-planning` (baseline + modality + oracle) for that milestone.
3. Establish the oracle with `aiw-ground-truth` before writing fixtures.
4. Gate the "done" claim through `aiw-verification`.

---

## Dependency graph

```
Phase 0 foundation (#168 etc.) ──▶ A2a (processed-artifacts layer) ──┬─▶ A2b (working-context view + retrieval) ──┐
                                                                     │                                            ├─▶ A2d (re-plumb learning loop + write-back)
                                                                     └─▶ A2c (split durable memory + narrative) ──┘
```

**The order that matters most:** Phase 0 lands first — the new memory leans on the deterministic substrate (effort_score, discount gate, confidence/interval fixes) *harder*, not less. A2a is the substrate for the rest of A2: nothing can be pulled on demand until ingestion stores it. A2d is last because it migrates the existing M4–M10 loop onto everything the earlier three build.

---

## A2a — The processed-artifacts layer

**Intent.** Stand up the "do the work on ingestion so retrieval is cheap" layer. Make ingestion produce and *store* the pre-digested artifacts as first-class, retrievable records, instead of re-deriving them inside `context.py` on every call.

**In scope.**
- New **consolidated stream view** artifact: a small, downsampled, aligned snapshot (HR, pace, grade, cadence) per activity, produced during analysis, stored, retrievable. **[ASK-FIRST: schema/migration]**
- Persist the **exchange digest** (headline, lead argument, commitments) as a stored artifact rather than the in-memory digest M4 recomputes. **[ASK-FIRST: schema/migration]**
- Persist the **extracted subjective signals** (RPE-vs-HR divergence, pain trend, pushback flag) from user responses. May begin read-time and move to ingestion-time as an optimisation. **[ASK-FIRST: schema if stored]**
- Derived metrics already exist; this milestone names them as the measured artifact, no change.

**Out of scope.** The retrieval seam that *reads* these (A2b). The narrative layer (A2c). Any change to how the loop consumes digests (A2d). Block grouping. Raw-stream sandbox compute.

**TDD.** Stream-view downsampling is a pure-function oracle: hand-author a stream input, assert the downsampled output is aligned, bounded in point count, and preserves the shape (peaks/grades survive). Ground-truth for the digest fields is the existing M4 digest contents (characterise current behaviour first, then prove the stored artifact matches). Migrations verified up/down against local Postgres (SQLite tests do not exercise migration SQL).

**Success looks like.** After an activity is analysed, its consolidated stream view and (after an exchange) its digest exist as stored rows, retrievable by id, lean (tens of points, not thousands). No coach behaviour changes yet — this is substrate.

**Links.** Prerequisite for A2b and A2c. Resolution of the stream view (per-split vs fixed point count) is a build-time tuning detail (design doc §7).

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-performance-profiling` (downsampling is a hot data loop), `aiw-verification`, `aiw-github`, `grill-with-docs` (pin migrations against ADR/CONTEXT), `aiw-project-context-management`.

---

## A2b — Working context as an assembled view + retrieval seam

**Intent.** Refactor `context.py` from "build the whole pack fresh" into "assemble a lean `B baseline` + pull a trigger-scoped focus payload on demand." Introduce the retrieval seam that reads A2a's artifacts.

**In scope.**
- Define and assemble the **B baseline** (design doc §4): this run's measured facts, narrative summary slot, relevant deterministic facts (beliefs/baseline/preference/load), last exchange digest, this run's subjective signals.
- Implement the **focus payload** as a pull: given a subject activity, retrieve its full metrics + splits + consolidated stream view + check-in.
- The **retrieval seam** itself: pull a specific activity's artifacts and older exchanges on demand, not preloaded. Raw streams never enter context.
- Subject resolution for conversation triggers: timing hint + content + clarifying-question fallback (design doc §5) — the *memory* half; the cadence/release half is A4.

**Out of scope.** Producing the artifacts (A2a). The narrative content (A2c). Migrating the learning-loop sections to read from the view (A2d). Output shape (A3).

**TDD.** Assert the assembled B baseline contains exactly the agreed fields and *omits* the bulky ones (no raw streams, no full stream view unless an activity is the subject). Assert a focus-payload pull for activity X returns X's artifacts and nothing for an unrelated activity. Characterise the current `CoachContextPack` first so the refactor is provably behaviour-preserving where it should be.

**Success looks like.** Context assembly is measurably leaner by default (no full-detail-for-every-activity), the focus payload appears only for the subject, and a deep-dive on a specific activity pulls its stream view on demand.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-performance-profiling` (context-assembly is on the request path), `aiw-verification`, `aiw-github`.

---

## A2c — Split durable memory: narrative layer + consolidation job

**Intent.** Formalise durable memory as the two-authority split, and add the genuinely new piece: the LLM narrative and the background `Consolidation` job that maintains it, re-grounded from the deterministic facts every time.

**In scope.**
- New **narrative store**: one bounded per-runner relationship narrative in durable memory. **[ASK-FIRST: schema/migration]**
- New background **`Consolidation` job**: after exchanges, re-writes the narrative from deterministic facts + recent exchange digests; decoupled so the user-facing turn never waits on it. **[ASK-FIRST: new background job + RQ wiring]**
- Enforce the authority boundary in code and prompt: the narrative is voice-only, never cited as fact, never overrides a re-derived `DerivedMetric`.
- Name the existing beliefs (`CoachingContext`) + norms (`RunnerBaseline`) + derived preferences as the deterministic-facts half (no change to them here).

**Out of scope.** Declared Voice/Stance (Phase 2 / P1; reserve the slot only). The learning-loop re-plumb (A2d). Consolidation cadence tuning and cheap-model selection (build-time details, design doc §7).

**TDD.** The decoupling is testable: assert the exchange path does not block on consolidation. The authority boundary reuses the validator discipline — assert a narrative string can never become a cited factual claim and that a narrative contradicting today's `DerivedMetric` does not change the metric. Consolidation input is deterministic facts + digests; assert it never reads raw LLM output as fact. Narrative generation itself is LLM-backed: gate with the M5 eval discipline, not a brittle string assertion.

**Success looks like.** A per-runner narrative exists and updates in the background after exchanges; it visibly re-grounds (a stale narrative claim is corrected once the facts move); the user-facing exchange never waits on it; no narrative text can override measured data.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-security-testing` (the facts/narrative authority boundary is a trust boundary), `aiw-verification`, `aiw-github`, `grill-with-docs`, `aiw-project-context-management`.

---

## A2d — Re-plumb the learning loop + write-back seam

**Intent.** Migrate the existing M4–M10 learning loop onto the new memory model: read from the assembled view and stored artifacts instead of re-deriving from raw each time, and confirm the deterministic write-back fires on the exchange boundary reading only deterministic signals.

**In scope.**
- Point M4 (longitudinal), M7 (adherence), M8 (beliefs), M9 (calibration), M10 (preference) at the new working-context view + stored processed artifacts.
- Re-plumb the deterministic **write-back** (`belief_store.write_back_beliefs`) to fire on the exchange boundary. The 2026-06-09 seam audit confirms it reads only deterministic pack signals; this milestone preserves that and adds a test that pins it.
- Preserve the hard constraint: commitments/next-steps stay **machine-readable** in the thin structured tail (M4/M7 read them). **[ASK-FIRST if the tail contract changes]**

**Out of scope.** Output reframe to prose (A3) — the structured `CoachReport` stays the exchange record here. New belief kinds. Block grouping.

**TDD.** Regression-first: the M5 eval harness is the gate — the re-plumbed loop must score no worse than the current loop on the existing rubric (run `make eval` before and after). Add a test that proves the write-back reads only deterministic inputs (feed it adversarial LLM prose; assert no belief delta derives from it). Prove a learning-loop section produces the same output reading from the stored artifact as it did re-deriving from raw (behaviour-preserving migration).

**Success looks like.** The full M4–M10 loop runs against the new memory model with no eval regression; the deterministic write-back is provably LLM-free and fires once per exchange; commitments survive as structured data.

**Links.** Depends on A2a (artifacts), A2b (view), A2c (durable-memory split). The M5 eval gate (`make eval`) is the regression guard. The seam audit is recorded in `coach-north-star.md` open-questions and the project memory.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-verification` (eval-as-gate), `aiw-security-testing` (write-back is the auditability boundary), `aiw-github`.

---

## Open build-time tuning (not milestone-blocking)

Carried from design doc §7: stream-view resolution; Telegram reply-window length; consolidation cadence and model tier; whether subjective-signal extraction moves fully to ingestion-time; the deferred raw-stream sandbox compute. Decide each inside the milestone that hits it; none blocks starting.
