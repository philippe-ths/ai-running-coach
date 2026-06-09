# Coach memory is a four-layer pull model, not one pre-built context pack

Today the coach builds its entire context fresh on every call: `context.py` re-derives a single `CoachContextPack` from the raw store each time, crushing per-sample streams into one number per metric and assembling every section (longitudinal, perceived-effort, adherence, beliefs, calibration, preference) in one pass. That worked for a per-activity report, but it scales badly into the coaching relationship: it either bloats (carry everything) or pre-crushes (lose detail), and it re-derives the same digests on every touchpoint. The relationship needs memory that persists, stays lean in view, and keeps detail reachable.

We adopt a **four-layer memory model with pull-based retrieval**, and we **split durable memory** into deterministic facts and an LLM narrative. The layers (full detail in `docs/vision/coach-memory-retrieval-design.md`):

1. **Raw store** — append-only truth, never loaded wholesale.
2. **Processed artifacts** — derived on ingestion, stored, pulled on demand (metrics, consolidated stream view, exchange digests, extracted subjective signals). The "do the work on ingestion so retrieval is cheap" layer.
3. **Durable memory** — persists across exchanges, split: **deterministic facts** (beliefs, baselines, training load, derived preferences — authoritative for grounding) and an **LLM narrative** (the relationship story — authoritative for voice, never fact, re-grounded from facts by a background `Consolidation` job).
4. **Working context** — not a store; the lean view assembled per exchange (a `B baseline` always present + a trigger-scoped focus payload pulled on demand).

The boundary that makes the split safe is absolute and mirrors the standing belief rule: **the narrative can never override a re-derived `DerivedMetric` or a deterministic fact, and can never be the cited source of a factual claim.** Deterministic write-back stays off the LLM and auditable.

## Considered options

- **Keep the single pre-built context pack, just grow it.** Rejected: this is the bloat-vs-pre-crush dilemma. Carrying the whole relationship balloons the prompt (violates lean); pre-summarising everything loses the detail the coach occasionally needs and bakes staleness in.
- **One durable memory, not split.** Rejected: a single store either lets LLM-written colour harden into cited fact (unauditable, ungrounded) or forbids narrative entirely (loses the voice the relationship needs). The authority split is the point — facts ground, narrative colours, and they never cross.
- **Let the coach write its own narrative inline per exchange.** Rejected: couples consolidation to producing the turn (slows the user-facing reply) and lets the story drift without re-grounding against facts. A decoupled background job re-grounds every time.
- **Three layers (fold processed artifacts into raw store).** Rejected: the pre-digested layer is structurally distinct (derived, not truth; stored, not assembled fresh; per-event detail, not cross-exchange generalisation) and is the heart of the reframe. Naming it keeps the model honest.

## Consequences

- New persistence: a processed-artifacts layer (consolidated stream view, stored exchange digests, extracted signals) and a durable-memory narrative store, with migrations. Beliefs (`CoachingContext`) and norms (`RunnerBaseline`) already exist as the deterministic-facts half.
- `context.py` is refactored from "build the whole pack" to "assemble a lean baseline + pull focus payload on demand" — a read-path retrieval seam.
- The M4–M10 learning loop is re-plumbed onto the new model; the deterministic write-back fires on the exchange boundary and is confirmed (seam audit, 2026-06-09) to read only deterministic signals. The thin structured tail must keep commitments/next-steps machine-readable or the loop breaks.
- This ADR is scoped to memory (A2). It assumes "a block is one activity" (A1 deferred) and keeps the structured `CoachReport` as the exchange record (A3 deferred). It depends on the Phase 0 foundation fixes landing first.
- Future reviews should not re-suggest a single flat context pack or a single unsplit memory: the four layers and the facts/narrative authority split are the design, not an accident.
