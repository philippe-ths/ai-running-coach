# Coach Report — Update Brief

> Execution-facing companion to `coach-report-improvement-plan.md`. That document holds the vision, the moat argument, and the six-voice debates (the *why*). This brief holds the *what* and *in what order*: milestones with explicit scope, intent, test discipline, success criteria, dependencies, and the skills that apply.
>
> **Read first:** the design plan's section 2 honest note — *today none of the moat exists in code*. This brief builds the moat; it does not polish prose.
>
> **Boundary discipline:** items flagged **[ASK-FIRST]** touch schema, migrations, or the public `CoachReportContent` contract and must be approved by the product owner before implementation (per `ai-workflow.md`). The deterministic policy validator gate is never bypassed. Previews share the production DB, so every migration is verified with that hazard in mind.

---

## How to use this brief

Each milestone is a coherent shippable unit. Work them in dependency order, not necessarily number order — the dependency graph below is authoritative. Before starting any milestone:

1. Confirm its **[ASK-FIRST]** items are approved (see Open Questions in the design plan, section 9).
2. Run `aiw-planning` (baseline + modality + oracle) for that milestone.
3. Establish the oracle with `aiw-ground-truth` before writing fixtures.
4. Gate the "done" claim through `aiw-verification`.

Milestone IDs map 1:1 to the design plan's roadmap (N1–N4, M1–M5, L1–L4) so the two documents stay reconcilable. Foundation pairs are merged where the plan says they ship together.

---

## Dependency graph

```
M0 (N1 cache seam) ─┐
M0 (N2 scope rule) ─┼─→ M1 (N3 reshape + N4 confounder) ─→ M2 (RunnerBaseline)
                    │                       │                     │
                    │                       ├─→ M6 (RPE-vs-HR)     │
                    │                       │                     ├─→ M4 (contrast pack)
                    │                       └─────────────────────┤
                    │                                             ▼
                    └──────────────────────────────────→ M5 (EVAL HARNESS) ── gate ──┐
                                                                                      │
                                       Long horizon, all gated by M5:                 │
                                       M7 (adherence loop) ←── M3, M4 ────────────────┤
                                       M8 (belief write-back) ←── M3 ─────────────────┤
                                       M9 (self-calibrating + referral) ←── M2 ───────┤
                                       M10 (preference model) ←── M7 ─────────────────┘
```

**The gate that matters most:** nothing in the Long horizon (M7–M10) starts until M5 (eval harness) exists. "Better than a cold AI" and "gets better over time" are unmeasurable without it, and the literature documents preference-drift that only an offline rubric catches.

---

# NEAR HORIZON

## M0 — Safety & versioning foundations *(plan: N1 + N2)*

**Intent.** Lay the two seams every later change rides on: a cache that can hold more than one report shape per activity, and a validator that refuses medical overreach. Both are pure infrastructure with no user-visible output on their own. They ship first because the reshape (M1) is unsafe without the scope rule and unshippable-incrementally without the versioned cache.

**In scope.**
- N1: change `CoachReport` cache identity from `unique=True` on `activity_id` to `(activity_id, prompt_id, schema_version)`; retain prior versions; auto-regenerate-on-read when the active version moves; stop `?force=true` from destructively deleting. **[ASK-FIRST: schema/migration]**
- N2: add a fifth deterministic rule to `services/coach/validator.py` rejecting dose advice and diagnosis verbs, forbidding escalation of a single wearable number into a health claim, while *permitting* context-aware metric correction.

**Out of scope.** Any change to report shape or prompt (that is M1). Any new metric. Confidence-routing enforcement in the validator (deferred to M-horizon). Touching the existing four validator rules beyond leaving them intact.

**TDD.** Validator is the textbook deterministic-oracle case: write red tests first from hand-authored positive/negative report fixtures (dose-advice string → reject; interpretive correction → permit; diagnosis verb → reject). Ground-truth comes from the medical-scope boundary stated in plan section 8, not from the LLM. For N1, characterise current cache behaviour with a test that proves `force=true` deletes today, then drive the version-keyed behaviour test-first; assert old versions survive and read auto-regenerates on version bump.

**Success looks like.** A report shape can change without silently overwriting history; both shapes coexist keyed by version. The validator rejects a crafted dose-advice report and a crafted diagnosis report while still passing a legitimate "discount this HR drift" correction. All existing validator tests stay green.

**Links.** Hard prerequisite for M1 (reshape needs the version seam to ship incrementally and the scope rule to be safe). The scope rule is extended later in M9 (referral layer) and by the M-horizon confidence-routing enforcement.

**Useful skills.** `aiw-planning`, `aiw-ground-truth` (oracle = stated medical boundary), `aiw-testing`, `aiw-security-testing` (the scope rule is a trust-boundary control), `aiw-verification`, `aiw-github`. Schema items: `grill-with-docs` to pin the migration decision against ADR/CONTEXT before approval; `aiw-project-context-management` to update `project-context.md` after the migration lands.

---

## M1 — Grounded reshape *(plan: N3 + N4)*

**Intent.** The change felt first by the runner, paired in the same horizon with the deterministic correction so the new confidence rides on a corrected number from day one. Reshape alone would be a better-dressed one-run chatbot stating misleading HR with more conviction; correction makes the confidence honest. They land together.

**In scope.**
- N3: add `headline` (reuse the ADR-0007 composed Headline), `thesis`, and a single ranked `lead_argument` above the existing `CoachReportContent` fields; re-rank `key_takeaways` by evidence strength; relax the fixed 2–4 cardinality so length varies by activity; rewrite prompt rules 6/8 from "concise/conservative" to "state the strongest claim the evidence supports — confident where `DerivedMetric.confidence` is high, abstain where low." Keep evidence refs and the validator. **[ASK-FIRST: public schema/shared contract]**
- N4: new stage in `services/analysis/` that joins `average_temp` (parsed from `raw_summary`), `is_hilly`, and a new medication/physiology context field against `hr_drift`/`effort_score`, emitting a structured `discount_signals` annotation on `DerivedMetric`; pass it into the context pack as evidence; instruct the LLM to honor it. Degrade gracefully (no temp → no heat discount, lower confidence, never a fabricated confound). The new medication field is **[ASK-FIRST]**; the temp join is not.

**Out of scope.** Any longitudinal/trend claim (needs M2). Reading prior reports (M4). Computing `RunnerBaseline` (M2). The medication *capture* UI — N4 reads a field; populating it durably is M2. Population thresholds become RunnerBaseline-relative only at M9.

**TDD.** N4 is deterministic and fixture-testable: author input fixtures (activity with 28 °C + hills + stimulant flag → expect `hr_drift_likely_inflated_by: [...]`; activity with no temp → expect no heat discount + lowered confidence). This is the strongest TDD candidate in the brief. For N3, the testable surface is schema validation (additive fields, legacy coercion via the existing `model_validator`) and that the validator still keys off context fields/text patterns rather than bullet structure — prove the reshape does not break the gate. Prompt-objective change is verified through the eval rubric (M5) and the frontend render, not unit assertions.

**Success looks like.** A heat-inflated run produces a report that says "discount this drift number" because the pipeline told it to, not because the LLM improvised. A recovery jog renders as three sentences; a structured interval session goes deep on rep fade. The `headline`/`thesis`/`lead_argument` fields populate and render. Frontend coach-report panel renders the new shape (verified on a seeded local DB or a preview, read-only). Validator and all existing tests stay green.

**Links.** Depends on M0 (both items). Feeds M5 (gives the eval harness something to score). N4's `discount_signals` are consumed by M6 (RPE-vs-HR weighting), M9 (individualized correction). The new `average_temp` join enables temp-band bucketing in M2.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-verification`, `aiw-security-testing` (parsing untrusted `raw_summary`; medication = health-adjacent data crossing a boundary), `aiw-github`. `verify` / `run` to confirm the new shape renders against real seeded data. `grill-with-docs` for the schema contract change. `aiw-project-context-management` after the analysis-pipeline stage is added (the maintenance checklist requires it).

---

# MID HORIZON

## M2 — Trend substrate: compute `RunnerBaseline` *(plan: M1)*

**Intent.** The real engineering spine. The entire longitudinal vision sits on an empty table whose docstring says computation is deferred. Build it. Persist the per-run signals that actually move over a 4–12 week block, bucketed so comparisons are valid rather than noise.

**In scope.** Implement the baseline computation; persist per-run Efficiency Factor, HR/pace decoupling, heart-rate recovery, and drift trends, bucketed by effort band, `is_hilly`, and temperature band; abstain on any trend claim until `sample_count` crosses a threshold (the threshold value is an Open Question for the owner — plan section 9, item 4). **[ASK-FIRST: schema if new columns/table]**

> **Verified against `main` (not greenfield):** `trends.py:362` already has `build_efficiency_trend` — but only a *per-activity* point series (`speed / avg_hr`, filtered to >1km with valid HR), with no bucketing, no baseline-relative delta, no abstention. Build M2 *on top of* that primitive rather than re-implementing it; the new work is the like-sessions bucketing, the cross-run trend, and the `sample_count` gate. Also note `RunnerBaseline.sample_count` already exists as a column, so the abstention gate needs no new field for that counter.

**Out of scope.** Injecting trends into the report (that is M4). ACWR/TSB as precise numbers (explicitly forbidden — framing only). The self-calibrating "expected HR for these conditions" model (M9). Anything that asserts a trend on fewer than threshold comparable sessions.

**TDD.** Ground-truth is the hard part: EF/decoupling/HRR are defined formulas, so author fixtures from hand-computed expected values over synthetic-but-realistic streams (oracle = the formula applied by hand, recorded with provenance per `aiw-ground-truth`). Test the bucketing logic separately (like-sessions only) and the abstention gate (below threshold → no trend emitted). Characterise against real seeded activities for sanity, not as the primary oracle.

**Success looks like.** Given N comparable easy runs, the layer reports an EF trend with a direction and magnitude; given two runs it abstains. Buckets never mix a hilly hot interval session with a flat cool easy run. Numbers reconcile with hand computation on the fixtures.

**Links.** Depends on M1 (temperature field enables temp-band bucketing). Feeds M4 (baseline deltas in the pack), M9 (RunnerBaseline-relative individualized correction). Without it, M4's longitudinal claims have no substrate.

**Useful skills.** `aiw-planning`, `aiw-ground-truth` (formula-based oracle, document provenance), `aiw-testing`, `aiw-performance-profiling` (cross-run aggregation is a heavy data loop), `aiw-verification`, `aiw-github`, `aiw-project-context-management` (new model/migration triggers the checklist).

---

## M3 — *(reserved — see M4)*

> The design plan numbers prior-reports-as-contrast as M3 and the eval harness as M4. This brief orders by dependency: the contrast pack is **M4** below and the eval harness is **M5**, because the harness gates the long horizon and is conceptually a precondition, not a peer. IDs are kept aligned to the plan via the parenthetical *(plan: …)* tags.

---

## M4 — Longitudinal contrast in the pack *(plan: M3)*

**Intent.** The cheapest longitudinal *feel*. Make the report reference what it told you last time and whether the conditions moved — without fabricating a trend line. The prior report already sits in the DB keyed by activity; this is mostly a wiring + prompt-discipline job, plus baseline deltas once M2 lands.

**In scope.** Inject the last 1–2 reports' `lead_argument` + `next_step` and (when available) M2 baseline deltas into the context pack, with an explicit "advance the narrative, do not restate" instruction. `DerivedMetric` stays the re-derived ground truth each run. Pass a *digest* (last N lead-arguments), never full reports, to bound token growth.

**Out of scope.** Labeling whether the runner acted on the advice (that is the adherence loop, M7). Writing belief deltas back (M8). Any memory store beyond reading existing `CoachReport` rows.

**TDD.** Reports-only path can land before M2: test that the pack contains the prior `lead_argument`/`next_step` digest and not the full report body (token bound), and that the anti-restate instruction is present. The "advance vs parrot" quality is scored by the M5 rubric, not unit-asserted. For the baseline-delta path, fixture a runner with a known M2 trend and assert the delta reaches the pack.

**Success looks like.** Report N references report N-1 as contrast ("drift down from your Tuesday long run") rather than repeating it. Pack size stays bounded as history grows. Parroting is caught by the M5 rubric.

**Links.** Depends on M0/M1 (versioned reports exist in the new shape); reports-only sub-path can ship before M2; baseline-delta sub-path depends on M2. Feeds M7 (adherence references prior next_step) and M8 (write-back reads the same slot).

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-performance-profiling` (token/cost bound is a first-class concern here), `aiw-verification`, `aiw-github`.

---

## M5 — Offline eval harness *(plan: M4) — THE GATE*

**Intent.** The un-sexy precondition for everything that follows. Without an offline rubric, "better than a cold AI" and "gets better over time" are vibes, and the preference-drift the literature documents is invisible. This gates the entire Long horizon.

**In scope.** Via `make seed-local`, freeze 15–20 real activities with rubric assertions: did the report lead with a headline; did it discount an inflated HR when `discount_signals` fired; did it avoid medical overreach; did it advance rather than parrot the prior report; did it abstain on a trend with too few comparable sessions. Produce a repeatable score.

**Out of scope.** Any learning/memory feature (those are what the harness gates). A live A/B in production. Automated frontend tests beyond what already exists.

**TDD.** The harness *is* a test artifact, so the discipline inverts: ground-truth is real seeded activities (path 2 in the design plan's Real-Data Verification) plus a human-authored rubric. The rubric assertions are the oracle; author them from the milestone success criteria above. Validate the harness itself by running it against a deliberately-bad report (should fail the rubric) and a known-good one (should pass).

**Success looks like.** One command scores a frozen set of real reports against the rubric and flags regressions. A reshaped-but-overreaching report fails; a grounded one passes. The score is stable enough to detect drift between prompt/model versions (which M0's versioned cache makes comparable).

**Links.** Depends on M1 (needs reshaped reports to score) and benefits from M4. **Gates M7, M8, M9, M10.** This is the single most important sequencing constraint in the brief.

**Useful skills.** `aiw-planning`, `aiw-ground-truth` (real activities + human rubric = the authoritative oracle), `aiw-testing`, `aiw-verification`, `aiw-github`. Local real-data path via `make seed-local` (see plan's Real-Data Verification). `tdd` skill for the rubric-first loop.

---

## M6 — RPE-vs-HR divergence + pain-score trend *(plan: M5)*

**Intent.** Surface a signal only this app holds both sides of: compare subjective `CheckIn.rpe` against the measured `effort_score`, and weight RPE over HR when a confounder is flagged (RPE survives HR distortion). Extends the ADR-0007 "the gap is the signal" philosophy from intent-vs-execution to perception-vs-physiology.

**In scope.** Compute RPE-vs-`effort_score` divergence; when N4's `discount_signals` flag an HR confounder, weight RPE above HR in the report's reasoning; trend `pain_score` over time.

**Out of scope.** Any diagnosis from pain (the M9 referral layer owns the non-diagnostic nudge). Acting on adherence (M7). Requiring a CheckIn — the signal is sparse-but-precise and degrades gracefully when absent.

**TDD.** Deterministic divergence calc is fixture-testable: author CheckIn + DerivedMetric pairs with known RPE/effort gaps and assert the divergence value and the weighting decision (confounder present → RPE weighted up). Null-checkin path must stay safe (an existing validator rule already covers null check-ins — do not regress it).

**Success looks like.** A run where the runner reported high RPE but HR looked easy (heat-suppressed) is reasoned about with RPE in the lead. Pain trend is computed without ever asserting a diagnosis. Sparse/absent CheckIns degrade cleanly.

**Links.** Depends on M1 (N4 confounder flags). Independent of the trend substrate. Feeds the richer signal set the long-horizon learning loop draws on.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-security-testing` (pain/health-adjacent data; keep it inside the wellness lane), `aiw-verification`, `aiw-github`.

---

# LONG HORIZON — all gated by M5

## M7 — Adherence learning loop *(plan: L1)*

**Intent.** The literal "gets better over time." For each emitted `next_step`, label the subsequent comparable activity acted-on / ignored / contradicted from Strava data — zero extra runner effort. The uncrossable proprietary dataset. Advisory, not accusatory.

**In scope.** Label each `next_step` from the next comparable activity; store as retrievable memory; next report references it. Comparable-conditions-gated (never fire on a noisy non-comparable run); overridable by CheckIn/chat pushback (explicit beats noisy implicit).

**Out of scope.** Belief-state write-back of confounds/philosophy (M8). The preference reward model (M10). Firing a verdict when conditions are not comparable. Any non-overridable automatic accusation.

**TDD.** Fixture a `next_step` ("keep the easy run easy") + a subsequent comparable activity that does/does not comply; assert the label and that a non-comparable activity yields *no* label. Assert CheckIn/chat pushback overrides the implicit label. Run the M5 rubric to confirm references advance rather than nag.

**Success looks like.** Report references whether the prior action landed, only on comparable conditions, and a runner's explicit "that was off" flips the implicit label. M5 rubric shows no parroting/preference-drift regression.

**Links.** Depends on M2, M4, **and M5 (gate)**. Feeds M10 (the preference model trains on this dataset). Pairs with M8 (the two halves of the write-back loop).

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-security-testing` (per-user memory, user-scoped keys for ADR-0005), `aiw-verification`, `aiw-performance-profiling` (memory retrieval cost), `aiw-github`.

---

## M8 — Belief-state write-back per report *(plan: L2)*

**Intent.** The architectural heart of the moat. Each report writes a small structured belief delta (confirmed confound: "HR inflates ~6 bpm above 25 °C"; "responds to easy-day discipline"; active block) into the gated store the next report reads. The `CoachReport` history stops being a pile of artifacts and becomes the runner-model.

**In scope.** Per-report structured belief delta written into the **[ASK-FIRST]** gated store; write gates (non-redundant, quality-thresholded, contradiction-resolved); TTL by semantic class (immutable injury ≈ infinite, transient "on a stimulant this week" short, preferences between); confidence/recency tags on every retrieved memory; never append every run.

**Out of scope.** The base-model fine-tune or reward model (M10). Anything that lets memory override the re-derived `DerivedMetric` ground truth (forbidden by design). Unbounded "remember everything" (the literature's 13%-accuracy failure mode).

**TDD.** Test the write gates as deterministic logic: redundant delta → not written; contradicting delta → resolved per rule; TTL expiry → memory decays below retrieval threshold; never-retrieved memory decays, reinforced memory persists. Assert retrieved memories carry confidence/recency tags. The drift it is meant to prevent is measured by M5 — run it.

**Success looks like.** Report N+1 is provably built on report N's recorded beliefs; stale facts decay; contradictions resolve rather than accumulate; `DerivedMetric` still overrides memory each run. M5 rubric detects no drift.

**Links.** Depends on M2, **M5 (gate)**, and the M2 durable-context layer (the design plan's M2 `CoachingContext` table — its **[ASK-FIRST]** approval applies here). Pairs with M7.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-security-testing` (gated memory, user-scoped, PII/health-adjacent), `aiw-verification`, `aiw-performance-profiling` (token/cost of the memory digest), `aiw-github`, `aiw-project-context-management` (new store = schema/structure change).

---

## M9 — Self-calibrating correction + non-diagnostic referral *(plan: L3)*

**Intent.** Turn confounder correction from population rules-of-thumb into RunnerBaseline-relative individualized anomalies ("expected HR for *these* conditions for *this* runner"), and add the deterministic clinician-nudge layer that keeps the product responsibly inside the general-wellness lane.

**In scope.** Individualize correction against the M2 baseline; migrate population thresholds (5%/10% drift bands, heat curves) toward personal deltas; add a separate deterministic referral layer for red-flag patterns (sustained resting-HR rise, persistently elevated post-exercise HR, performance drop after illness) producing a "consider seeing a clinician" nudge — pipeline-owned, never a diagnosis.

**Out of scope.** Any diagnosis verb (the M0 validator scope rule forbids it and is extended here). The preference model (M10). Replacing population heuristics before enough personal baseline accrues — labeled-as-heuristic until then.

**TDD.** Fixture a runner with an established baseline and an anomalous run; assert the correction is computed relative to that baseline, not a population constant. Fixture red-flag patterns and assert the referral nudge fires *and* that it never contains a diagnosis verb (reuses/extends the M0 validator tests). Confirm graceful fallback to labeled heuristics when baseline is thin.

**Success looks like.** Correction speaks in this runner's own expected values once baseline is sufficient; the referral nudge fires on red-flag patterns as a nudge, never a diagnosis, and passes the medical-scope validator. M5 rubric confirms no overreach.

**Links.** Depends on M2 (baseline) and **M5 (gate)**. Extends M0's validator scope rule.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-security-testing` (medical-scope is the core risk here), `aiw-verification`, `aiw-github`.

---

## M10 — Per-runner preference model *(plan: L4)*

**Intent.** Compounding personalization: a lightweight reward model (T-POP style, **no base-model fine-tune**) that reranks report framing toward advice this runner demonstrably acts on. The capstone — only meaningful once the adherence dataset and the eval harness exist.

**In scope.** A per-runner preference/reward model that reranks framing using accumulated adherence (M7) + explicit feedback signals. Retrieval/reranking, not fine-tuning.

**Out of scope.** Fine-tuning the base model (explicitly excluded). Shipping before the dataset and eval exist (Data Scientist and Architect gate this hard). Letting the reranker override `DerivedMetric` ground truth.

**TDD.** Hardest oracle in the brief — defer detailed test design until M7's dataset shape is known. Minimum: hold-out evaluation through the M5 harness showing the reranked framing scores no worse on safety/grounding and better on adherence-relevant framing. No regression on the medical-scope or trend-abstention rubric items.

**Success looks like.** Reranked framing measurably improves the adherence-relevant rubric score on held-out data without degrading safety/grounding scores. Demonstrated through M5, not asserted.

**Links.** Depends on M7 (dataset) and **M5 (gate)**. Terminal milestone.

**Useful skills.** `aiw-planning`, `aiw-ground-truth`, `aiw-testing`, `aiw-performance-profiling`, `aiw-verification`, `aiw-security-testing`, `aiw-github`. `claude-api` if the reranking touches the Anthropic model layer.

---

## Cross-cutting guardrails (apply to every milestone)

- **Validator gate is never bypassed** (`ai-workflow.md`). It grows from four rules to five+ (M0 scope rule, then M-horizon confidence-routing and precise-injury-probability bans).
- **Trend abstention until `sample_count` crosses threshold** — never trend two runs (M2 onward).
- **`average_temp` degrades gracefully** — absent/wrong temp → no heat discount + lower confidence, never a fabricated confound (M1 onward).
- **Parroting / preference-drift** — prior reports as contrast only, anti-repetition lead-argument digest, `DerivedMetric` re-derived each run (M4, M7, M8).
- **Token / cost bound** — retrieve a digest, never full reports; the pack must not grow unbounded as it gets richer (M4, M8). `aiw-performance-profiling` applies.
- **Preview-deploy hazard** — previews point at the production backend/Postgres; every schema add (M0, M1 field, M2, M8) is migrated and verified with that hazard in mind. A careless write on a preview mutates real data.
- **Multi-user from day one** — memory keys and write-gates are user-scoped now, designed for the ADR-0005 Phase 2 transition (M7, M8).

## Open questions blocking start (from design plan section 9)

These must be resolved with the product owner before the dependent milestones begin:

| Question | Blocks |
| --- | --- |
| Schema approval in principle + migration discipline given preview/prod shared DB | M0, M1 (med field), M2, M8 |
| Land N3+N4 together vs reshape-alone-first | M1 sequencing |
| Comfort capturing medication/physiology data | M1 (N4 field), M8 |
| `sample_count` threshold for trend assertions | M2 |
| Accept the eval-before-learning-loop gate | M7, M8, M10 |
| Per-report explicit feedback control in scope? | M7 (explicit signal weighting) |
| Default opinion lever on a medium-confidence run | M1 prompt objective |

---

**Anchor files:** `backend/app/services/coach/{context,prompts,validator,service}.py`, `backend/app/schemas/coach.py`, `backend/app/models/{coach_report,runner_baseline,user_profile}.py`, `backend/app/services/trends.py`, `backend/app/services/analysis/_orchestrator.py`.
