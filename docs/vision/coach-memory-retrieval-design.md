# Coach Memory and Retrieval: settled design (Phase 1 / A2)

Status: settled 2026-06-09 from the interactive design session that resumed the inputs in `coach-memory-retrieval-notes.md`. This is the design A2 builds to. The inputs doc is now superseded by this one for everything it covers; it is kept only for provenance. Vocabulary lives in `CONTEXT.md`; the roadmap and the why live in `coach-north-star.md`.

The frame, in one line: the coach has **per-data-type ingestion pipelines that do the hard work in the background the moment data lands, shaping each type into a cheap-to-retrieve form, feeding one lean working context the coach reads from.** Retrieval is cheap because ingestion already paid the cost.

---

## 1. The five principles (how LLMs handle data best)

The design's guardrails. Every decision below is an application of one of these, and any later decision can be tested against them.

1. **Lean beats complete.** The model reasons better over a small relevant slice than a big dump. The right context, not more context.
2. **Pre-digested beats raw.** Give the model something already shaped for the question (a summary, a comparison, a flag), not raw material it must crunch itself. (Karpathy's "do the work on ingestion.")
3. **Reason before structure.** Let the model think in prose first, emit structure second. Forcing structured output up front hurts the reasoning. (Already adopted.)
4. **Pull beats preload.** Start lean, fetch specific detail only when the moment needs it.
5. **Fresh data wins over remembered data.** When a stored belief or summary disagrees with what this run measured, today's measurement wins.

The tension to keep honest: **2 vs 5.** Pre-digesting on ingestion lets stored summaries go stale. The split-memory design (section 3) handles it — deterministic facts are always re-derived; only the narrative is allowed to be soft, and it is re-grounded from facts on every consolidation.

---

## 2. The four-layer memory model

The glossary named three layers (raw store, working context, durable memory). The data-type framing surfaced a fourth that the three-layer view folded into "raw store": the **processed-artifacts layer**, which is the whole point of the reframe. The settled model is four layers.

1. **Raw store** — append-only source of truth. Never loaded wholesale. The current run always re-derives from it.
2. **Processed artifacts** — derived on ingestion from raw, stored, pulled on demand. The "do the work on ingestion so retrieval is cheap" layer.
3. **Durable memory** — persists across exchanges. Split into deterministic facts (authoritative for grounding) and an LLM narrative (authoritative for voice, never fact).
4. **Working context** — *not a store*. The lean view assembled per exchange from the three layers above.

```
                          ┌─────────────────────────────────────┐
   data lands ──ingest──▶ │ RAW STORE (append-only truth)        │
                          └─────────────────────────────────────┘
                                        │ background processing (immediate)
                                        ▼
                          ┌─────────────────────────────────────┐
                          │ PROCESSED ARTIFACTS (pre-digested)   │
                          │  metrics · stream view · digests ·   │
                          │  extracted subjective signals        │
                          └─────────────────────────────────────┘
        deterministic write-back │            │ pull on demand
                                  ▼            │
                          ┌──────────────────┐ │
                          │ DURABLE MEMORY   │ │
                          │  facts │ narrative│ │
                          └──────────────────┘ │
                                  │ always-on   │
                                  ▼            ▼
                          ┌─────────────────────────────────────┐
                          │ WORKING CONTEXT (assembled view)     │
                          │  B baseline + trigger focus payload  │
                          └─────────────────────────────────────┘
                                        │
                                        ▼ reason → prose → thin tail
                                     EXCHANGE
```

---

## 3. Data type → layer map

The ten data types from the inputs doc, placed. "Today" marks what already exists in code.

| Data type | Layer | Ingestion produces | Notes |
|---|---|---|---|
| Raw Strava activities + streams | Raw store | — | never in context |
| Derived metrics | Processed artifacts | per-run measured facts (today) | the measured layer; re-derived each run; authoritative |
| Consolidated stream view | Processed artifacts | small downsampled aligned HR/pace/grade/cadence | **new**; pulled only when an activity is the subject |
| User responses (check-in/chat/Telegram) | Raw store (+ extract) | divergence, pain trend, pushback flag | raw kept; signals extracted (today at read-time, moving to ingestion) |
| Past coach output (exchanges) | Raw store (+ digest) | digest: headline, lead argument, **commitments** | full prose kept; commitments stay machine-readable (load-bearing) |
| Summarised activity history | (not a new store) | — | numeric = rollups (exist); prose = narrative layer |
| Constructed memory (beliefs, norms) | Durable memory (facts) | belief/baseline deltas (today, deterministic) | write-back re-plumbed to fire on exchange boundary |
| User preferences | Durable memory (facts) | derived profile (today); declared Voice/Stance (Phase 2 slot) | declared and derived kept separate |
| LLM narrative | Durable memory (narrative) | the relationship story | **new**; background consolidation job, re-grounded from facts |
| Uploaded materials / future types | (reserved slot) | — | Phase 2 / future; architecture must accept a new pipeline without redesign |

---

## 4. Working context: B baseline + focus payload

Working context is assembled per exchange, lean by default, deep on demand. It has two parts.

**B baseline (always present):** this run's measured facts; the narrative summary; the relevant deterministic facts (active beliefs with confidence/recency tags, matching baseline/calibration bucket, derived preference profile, training load); the last exchange's digest; this run's subjective signals if present.

**Focus payload (trigger-scoped):** rich detail about whatever the exchange is anchored to, pulled on demand. For an activity-anchored exchange this is that activity's full metrics + splits + **consolidated stream view** + its check-in. For a general chat it is empty until the conversation references something.

Pulled only on demand (never preloaded): older exchanges' full prose, deeper history, raw streams, and later the corpus/materials. Raw streams never enter context at all; the consolidated stream view is the retrievable middle tier.

---

## 5. Trigger points

Two families. The **channel** (Telegram vs in-app) is orthogonal — same handler, different delivery adapter.

**Event triggers** run real background work and may cause the coach to speak:

- **New activity lands.** Memory work fires **immediately** in the background (ingest → analyze → produce consolidated stream view → extract signals) so retrieval is ready. The *exchange* fires later at block-complete (A1/A4 own the timing). Focus payload when it fires: the block (each activity's metrics + stream view) + B baseline.
- **Check-in submitted.** Ingests (store raw + extract subjective signals) *and* can release a waiting stage-two exchange (the cadence reply case). Focus payload: that activity + the new signals + B baseline.

**Conversation triggers** store the message, extract any light signal (pushback → "disputed"), and assemble a focus payload from what the message is about:

- **In-app chat on an activity.** Continuation of the one relationship; reads shared memory, writes back only the deterministic pushback signal. Focus payload: that activity + the thread + B baseline.
- **Telegram chat.** Same handler as in-app. The reply lacks a structural anchor, so the **subject is resolved intelligently:** a reply-window timing *hint* pre-loads the likely subject, the coach confirms or redirects from content (pulling the real subject on demand), and asks a quick clarifying question when genuinely ambiguous.
- **General chat (no activity).** The residual case: B baseline only, no focus payload, pull a specific activity on demand if the conversation turns to one. Still knows the runner (beliefs, preferences, narrative).

---

## 6. What A2 owns, and the boundaries

A2 is **memory and retrieval only.** It deliberately does not pull in the neighbouring milestones:

- **A1 (Block detection)** is deferred. A2 treats "a block is a single activity" as a placeholder, exactly as `_extract_planned_workout` returns `None` today. When Block detection lands, blocks start grouping — additive, no A2 rework.
- **A3 (output reframe)** is deferred. A2 keeps the current structured `CoachReport` as the exchange record. A3 later demotes it to a prose `Exchange`. A2's only constraint on A3: the commitments/next-steps in the thin tail must stay machine-readable, because the learning loop reads them.
- **A4 (cadence + pipeline)** owns *when* the coach speaks (block-complete, two-stage opener/timer, releasing a waiting turn). A2 owns the memory the cadence reads and writes, not the timing.

So A2's deliverables: the processed-artifacts layer; working context as an assembled view with a retrieval seam; split durable memory with the new narrative layer and its background consolidation job; and re-plumbing the M4–M10 learning loop onto the new memory model with the deterministic write-back firing on the exchange boundary.

**Sequencing:** Phase 0 (foundation fixes, #168 etc.) lands first — the new memory leans on the deterministic substrate harder. Then A2. Then A1/Block, A3, A4.

---

## 7. Open build-time tuning details (decide during the build, not now)

- Consolidated stream view resolution (per-split vs fixed point count).
- Telegram reply-window length; the stage-2 timer duration (A4).
- Consolidation cadence (every exchange vs batched) and whether it uses a cheaper model than the coaching message (coach defaults to Opus 4.8; consolidation is a candidate for a cheaper tier).
- Whether subjective-signal extraction moves fully to ingestion-time (optimisation) or stays read-time at first.
- The on-demand raw-stream sandbox compute (principle 2 deepened) — a real future capability, explicitly not an A2 dependency.
