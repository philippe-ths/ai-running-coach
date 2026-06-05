# Running Coach — Report Plan: From Metric Narrator to Continuous-Improvement Coach

> Status: design plan for product-owner review. Schema-touching items are flagged **[ASK-FIRST]** per the project's Boundary Rules and must be approved before implementation. Code claims below were verified against `backend/app/services/coach/`, `models/`, `schemas/`, and `services/trends.py` on the current `main`.
>
> Produced by a multi-perspective design workflow: a research sweep (sports science, AI personalization/memory, competitive landscape, LLM health-report design) feeding a six-voice panel debate (Coach/physiologist, Data Scientist, AI Product Designer, Runner, Safety Skeptic, Systems Architect), synthesized here.

---

## 1. Vision

The Coach Report stops being a four-box thermometer reading of one run and becomes a short, opinionated coaching note that situates today's effort inside the runner's own trajectory: it leads with a verdict, corrects numbers that the conditions made misleading, references what it told you last time and whether you acted on it, and ends with one prioritized action tied to your goal. Each report is simultaneously an artifact the runner reads and a state-update that makes the next report better — the report *writes back* what it now believes about this runner (confirmed confounds, adherence, working philosophy) so report N+1 is provably built on report N's outcome. That write-back loop, fed by per-second stream signals and persistent personal context a chatbot never sees, is the heart of a system whose entire purpose is to make this specific runner measurably fitter over a season.

---

## 2. The core insight / moat

A cold AI handed the same single run can already produce a confident headline, correct an inflated HR if you *tell* it the temperature, and write nice prose. Those moves are copyable in a week. The two things it structurally cannot do:

1. **Persistent context captured once, applied automatically every run** — the amber-foot rehab block, "fitness is ahead of tissue," the stimulant's HR effect, the auto-ingested weather — on top of per-second HR/pace/power/cadence streams it never sees.
2. **A closed learning loop** — each report records its prioritized next-step; the *next Strava activity reveals for free* whether it was followed; that adherence outcome plus the runner's own rolling baseline feed the following report.

The moat is operational, not model-based: it is the *fusion* of deterministic stream metrics with a curated, gated per-runner memory, plus the discipline to keep refreshing it. The prompt and the model are swappable; the durable assets are the deterministic confounder-correction layer, the computed RunnerBaseline/trend layer, and the gated memory store. The product owner's bar — "better than any single AI given the same run data" — is met precisely at the points where the app holds context and history the cold AI cannot.

**Honest note for the owner:** today *none of the moat exists in code*. `context.py` (160 lines) feeds the LLM one activity, its metrics, the check-in, the profile, and three volume tallies. It never reads `average_temp` (the string appears nowhere in `backend/app/`), never loads a prior `CoachReport`, never reads `RunnerBaseline` — whose own docstring says *"Schema-only for now. Computation logic will be added in a future phase."* The substrate the owner believes exists is an empty table. This plan is mostly about building the moat, not polishing prose.

---

## 3. The key debates

### Debate A — Opinionated voice vs. safety hedging
**Runner, Coach, Data Scientist, Architect:** The timidity is a *product defect located in the prompt*, not a safety property. Verified: prompt rule 6 is literally "Be concise," rule 8 "be conservative. Never recommend risky volume jumps," and the schema forces `min_length=2, max_length=4` equal-weight `key_takeaways`. "It depends" is the answer runners quit over.
**Safety Skeptic:** Confidence is dangerous; the validator has four rules and *none* forbid medical directives (verified). A confident coach with no scope gate is a liability.

**Resolution:** Both are right about different things. Hedging-as-tone goes; hedging-as-grounding stays. Confidence becomes a *per-claim router off the existing `DerivedMetric.confidence`/`confidence_reasons`* — loud where grounded, explicitly "not enough data" where not — rather than one uniform softening. This is structurally safer than today, because today the report states an inflated HR drift as durability signal with no correction at all. The Safety Skeptic wins their actual demand: a **deterministic medical-scope rule added to the validator ships *before* any opinionated feature**, and it is the validator (not the prompt) that enforces "no dose advice, no diagnosis verbs, never escalate one wearable number into a health claim." Opinionated-where-grounded is compatible with a hard scope gate. The validator gate is mandatory per `ai-workflow.md` and is never bypassed.

### Debate B — Single-run depth vs. longitudinal narrative
**Coach, Data Scientist, AI Designer:** A run only means something against the runner's recent history; lead with the 4-week aerobic trend before any single-run number.
**Architect:** You cannot narrate a "4-week aerobic trend" on top of a `RunnerBaseline` table that contains zero computation. Anyone promising it next sprint is selling vapor.

**Resolution:** The longitudinal vision is the spine, but it has a hard dependency the others gloss over. Sequence it honestly: the **trend layer (compute RunnerBaseline + persist per-run EF/decoupling/HRR bucketed by comparable conditions) is mid-horizon, not near.** Near-term, the cheap proxy is injecting the **previous 1–2 CoachReports as contrast** so the report *feels* longitudinal without fabricating a trend line. Confident trend claims must **abstain until `sample_count` crosses a threshold** — the report narrates in blocks and refuses to trend two runs. (Note: `trends.py` has weekly-bucketing machinery and a `build_efficiency_trend` that emits a *per-activity* speed/HR point series — but no like-sessions bucketing, baseline-relative delta, or abstention; the trended, condition-controlled EF layer is genuinely new code built on that primitive, not a wiring job.)

### Debate C — Memory richness vs. overfitting/parroting
**AI Designer:** Persistent memory is the moat.
**Data Scientist & Architect:** Unbounded "remember everything" collapsed an agent to 13% accuracy; memory induces parroting and preference drift. The deterministic `DerivedMetric` must stay the ground truth each report *re-derives* from.

**Resolution:** No real conflict on the destination, only on the discipline. Memory is **write-gated** (non-redundant, quality-thresholded, contradiction-resolved), **TTL-tiered by semantic class** (immutable `max_hr_source`/structural injury ≈ infinite; transient "on a stimulant this week" / "amber-foot block" short; preferences in between), and **prior reports enter the prompt only as CONTRAST, never as a template** with an explicit "advance the narrative, do not restate" instruction. Memory ships **last, behind an eval harness**, because without offline quality measurement we cannot detect the drift the literature documents.

### Debate D — "Ship the new shape first" vs. "shape on a cold pack is just a prettier chatbot"
**Runner, Coach:** The output reshape is the change felt first and touches no deterministic metric — ship it now.
**AI Designer, Architect:** A confident report on a stateless context pack is a *better-dressed one-run chatbot* that will now state misleading HR numbers with more conviction. Tone must ride on grounding.

**Resolution:** Ship the reshape near-term **but pair it in the same horizon with the deterministic confounder-correction stage**, so the new confidence rides on a corrected number from day one. The reshape alone is necessary, cheap, and de-risks the prompt-versioning work; it is explicitly *not* declared "the fix." Confidence and grounding land together.

### Debate E — Automation vs. runner control
**AI Designer:** Adherence is inferable for free from the next Strava activity; instrument it automatically.
**Runner & Safety Skeptic (implicit):** Inferring "you ignored the easy-day advice" from one noisy activity can be wrong and erodes trust.

**Resolution:** Automate the *signal*, keep the *verdict* advisory. Adherence labels require **comparable conditions** before firing, stay conservative, and **CheckIn / chat pushback overrides the implicit label.** Cheap explicit channels (a per-report "this was off / useful" control; chat pushback as a labeled correction) are weighted above noisy implicit signals. No long onboarding form — run informative-by-default off the existing profile + first sync, and spend the existing 0–4 questions channel on the 1–3 highest-information asks only.

---

## 4. What the report becomes

**Today** (verified `CoachReportContent`): `key_takeaways` (2–4, equal weight), `next_steps` (1–3), `risks` (0+), `questions` (0–4). No framing field, fixed cardinality, prompt ordered to be concise and conservative → flat hedged list, identical box-shape for a recovery jog and a race.

**Becomes** a narrative spine layered *on top of* the existing fields (legacy coercion already exists in the schema's `model_validator`, so additive growth is safe):

- **`headline`** — the one-line verdict, reusing the ADR-0007 composed Headline. *"Strong tempo despite the heat — your HR is lying to you."* Two-second read.
- **`thesis`** — one opinionated sentence placing the run in the block. *"Solid threshold long run; ignore the high average HR, it's the 28 °C and the climbing — your aerobic durability is actually trending up."*
- **`lead_argument`** — the single highest-load point with its strongest evidence ref, ranked above everything. For any session with `interval_structure`, this is the **interval KPIs** (rep CV, `recovery_quality_per_60s`, `first_vs_last_fade`, `work_rest_ratio`) — the exact thing Strava admits it "struggles with."
- **Number correction, baked in** — when the deterministic `discount_signals` field fires (heat + hills + medication inflating HR), the report says so out loud: *"discount this drift number."* This is grounded and pipeline-owned, never LLM improvisation.
- **Longitudinal thread** — leads with a trend verdict when `sample_count` permits, and references the prior report as contrast: *"drift down from your Tuesday long run"; "last time I asked you to keep the easy run easy — you did, and decoupling dropped."*
- **One prioritized next action** — concrete dose + "why" tied to `goal_type`/`target_date`, plus whether the last one landed.
- **Re-ranked supporting points** — `key_takeaways` ordered by evidence strength, not presented as peers.
- **Variable shape** — easy runs are three sentences; hard structured sessions go deep on rep fade. The rigid 2–4 cardinality is what flattens playbook nuance back into sameness.
- **Per-claim confidence** — loud where `DerivedMetric.confidence` is high, plain "insufficient data" where low, instead of one uniform hedge.

The evidence-ref discipline (the genuine edge over Strava's "wrong about zones" output) and the policy validator both stay intact. What changes is **shape, time horizon, and confidence routing — not the safety floor.**

---

## 5. The personalization & memory architecture

A flat report log is the wrong design (it collapses quality). The architecture is **layered**:

**What it remembers (the durable `CoachingContext` layer — the user-profile layer) [ASK-FIRST]:**
- Training-block intent ("amber-foot rehab block, fitness ahead of tissue")
- Medication/physiology notes (stimulants/beta-blockers — they change what HR *means*)
- Coaching philosophy / risk tolerance the runner endorses
- Environmental sensitivities; structural injury history
- Prior-report corrections the runner accepted or rejected

Captured **once**, injected into **every** context pack. This is the single highest-leverage change for "gets better over time," and it is what makes the confounder-correction and woven-context moves fire automatically instead of needing a manual paste. Today `ProfileContext` carries only `injury_notes`; there is nowhere for block intent, medication, or philosophy to live.

**What signals it learns from:**
- **Adherence (implicit, free, proprietary)** — for each emitted `next_step`, label the subsequent comparable activity *acted-on / ignored / contradicted* from Strava data. Zero extra runner effort.
- **CheckIn** (`rpe`, `pain_score`, `pain_location`, `sleep_quality`) — sparse but precise; `rpe`-vs-`effort_score` divergence and `pain_score` trend are first-class signals (extending the ADR-0007 "the gap is the signal" philosophy from intent-vs-execution to RPE-vs-HR).
- **Explicit feedback** — a per-report "this was off / useful" control; `CoachChatMessage` pushback as a labeled correction. Optional, but weighted above noisy implicit signals.

**How it feeds the next report:** Each report writes back a small **structured belief delta** (confirmed confound: "this runner's HR inflates ~6 bpm above 25 °C"; "responds to easy-day discipline"; "amber-foot block still active") into the gated store. The next report reads it. The `CoachReport` history stops being a pile of artifacts and becomes the runner-model.

**Cold-start:** No questionnaire. Run informative-by-default off the existing `UserProfile` + first-sync `DerivedMetric`, and spend the existing 0–4 questions channel on the highest-information-gain asks only (block intent, current niggle, medication).

**Anti-staleness / anti-overfitting / anti-repetition — the non-negotiable disciplines:**
- **Write gates:** non-redundant, quality-thresholded, contradiction-resolved. Never append every run.
- **TTL by semantic class:** immutable vs. transient vs. preference; never-retrieved memories decay below threshold; useful ones reinforce.
- **Confidence/recency tags on every retrieved memory** so the LLM hedges old context appropriately — reusing the app's existing `confidence_reasons` machinery; prevents the "changed jobs six months ago" stale-fact bug.
- **Prior reports as CONTRAST only**, with `DerivedMetric` as the re-derived ground truth each run — prevents parroting and preference drift.
- **Anti-repetition digest** — pass the last N *lead arguments* (not full reports) with an "advance the narrative" instruction; this also bounds token/cost growth.

**Multi-user from day one:** Per-user memory curation that is trivial for one user becomes a privacy/scaling problem at scale (ADR 0005). Memory keys and write-gates are **user-scoped now**, designed for the Phase 2 transition, even though we ship single-user.

**Retrieval, not fine-tuning:** The 2025–26 consensus and this single-user codebase both point to structured memory + retrieval. A lightweight per-runner preference model (T-POP style, no base-model fine-tune) is a *long-horizon-only* option after the adherence dataset and eval harness exist.

---

## 6. The longitudinal / health layer

Single runs roll up into a fitness trajectory through a **cross-run trend layer** built on a finally-computed `RunnerBaseline`. The signals that actually move over a 4–12 week block:

- **Efficiency Factor** (normalized-graded-pace / HR on steady aerobic runs) rising at matched conditions = real aerobic gain.
- **HR/pace decoupling** under ~5% on long steady runs = durability; climbing = pace above current aerobic ceiling.
- **Heart-rate recovery** (60s post-effort drop) improving over 6–8 weeks.
- **Resting-HR proxy** trend.

**The critical methodological constraint:** these are only valid across **LIKE sessions**. Trends bucket by **effort band, `is_hilly`, and temperature band** — and the app's orthogonal classification axes plus the new `average_temp` field are *exactly* the controls that make the comparison valid rather than noise. The report **leads with the longitudinal verdict** when `sample_count` permits (*"over 4 weeks your EF rose 6% at the same easy HR — that's real aerobic gain"*), and **reasons in blocks, refusing to over-read one run** — itself a trust-building, anti-cold-AI move.

**Load/distribution is framing, not verdict.** Use `training_context` (intensity distribution, `days_since_last_hard`, `hard_sessions_this_week`) to talk 80/20 and tissue-readiness as *conversation*. Do **not** present ACWR or TSB as precise injury/readiness numbers — the evidence base is contested and weakest exactly for recreational runners. Reserve confident language for the individually-supported trends (EF, decoupling, HRR, resting HR vs. *this runner's own* baseline); label population thresholds (5%/10% drift bands, heat curves) as the heuristics they are, and migrate toward RunnerBaseline-relative deltas as the personal "expected HR for these conditions" model accrues.

**Tissue-load as a first-class longitudinal narrative**, not a conditional block that only fires on flags: combine `risk_level`/`risk_reasons` + `training_context` + stated injury memory into a repeated, confident tissue-readiness call. This addresses the industry's universal failure (Garmin/Runna: plans that respect the heart but not the tissue) — the exact "fitness is ahead of tissue" philosophy in the motivating example.

A **non-diagnostic referral layer** sits *separate* from coaching: deterministic flags for red-flag patterns (sustained unexplained resting-HR rise, persistently elevated post-exercise HR, performance drop after illness) produce a *"consider seeing a clinician"* nudge — owned by the pipeline, never a diagnosis — keeping the product inside the general-wellness lane.

---

## 7. Phased roadmap

Sequencing principle (from the Architect, and agreed): **cheap+visible first, moat last behind eval.** Reshape and confounder-correction land together so confidence rides on grounding. Memory/learning loop is last because without the eval harness and version-aware cache it becomes the maintenance swamp that rots the product.

### Near horizon (concrete enough to become issues)

**N1 — Version-aware CoachReport caching; retain-on-supersede.** *(Architect)*
*What:* Change the cache identity from `unique=True` on `activity_id` to `(activity_id, prompt_id, schema_version)`; retain prior versions; auto-regenerate-on-read when the active version moves; stop `?force=true` from destructively deleting. *Why:* Verified — the cache key has no version dimension and `force=true` deletes the row, so any new report shape silently serves stale-shaped reports across all history with no A/B. *Dependency:* none; it is the seam that lets every later change ship incrementally. **[ASK-FIRST: schema/migration]**

**N2 — Add a deterministic medical-scope rule to the policy validator.** *(Safety Skeptic — non-negotiable, ships before any opinionated feature)*
*What:* Fifth validator rule rejecting dose advice and diagnosis verbs; forbid escalating a single wearable number into a health claim; permit context-aware metric correction. *Why:* Verified — the validator has four rules, none medical-scope; confident features are unsafe without it. *Dependency:* none.

**N3 — Reshape `CoachReportContent` + flip the prompt objective.** *(Runner, Coach, Data Scientist, Architect)*
*What:* Add `headline` (reuse composed ADR-0007 Headline), `thesis`, single ranked `lead_argument` above the existing fields; re-rank takeaways by evidence strength; relax fixed 2–4 cardinality so length varies by activity; rewrite prompt rules 6/8 from "concise/conservative" to "state the strongest claim the evidence supports; confident where `DerivedMetric.confidence` is high, abstain where low." Keep evidence refs and the validator. *Why:* The schema structurally forces the flat hedged list; verified the validator keys off context fields and text patterns, not bullet structure, so this is pure schema+prompt. *Dependency:* N1 (version-aware cache), N2 (scope rule first). **[ASK-FIRST: public schema/shared contract]**

**N4 — Deterministic confounder-correction stage → `discount_signals`.** *(Coach, Data Scientist, Architect, Runner — "lowest-risk, highest-leverage")*
*What:* New stage in `services/analysis/` that joins `average_temp` (parse from `raw_summary`), `is_hilly`, and a new medication/physiology context field against `hr_drift`/`effort_score`, emitting a structured `discount_signals` annotation on `DerivedMetric` ("hr_drift_likely_inflated_by: [heat_28C, hills, stimulant]"); pass it into the context pack as evidence; instruct the LLM to honor it. Degrade gracefully (no temp → no heat discount, lower confidence rather than assert). *Why:* `average_temp` is in `raw_summary` but never read; this is the cold-chat's signature move made guaranteed, deterministic, fixture-testable, and policy-safe. *Dependency:* the new medication field is **[ASK-FIRST]**; the temp join is not.

### Mid horizon

**M1 — Compute `RunnerBaseline` for real + persist the cross-run trend layer.** *(Coach, Data Scientist, AI Designer, Architect — the real engineering spine)*
*What:* Implement the baseline computation the table's docstring defers; persist per-run EF / HR-pace decoupling / HRR / drift trends bucketed by effort band, `is_hilly`, temperature band; abstain on trend claims until `sample_count` crosses threshold. *Why:* The entire longitudinal vision has no substrate today (empty table; `trends.py` has weekly bucketing and a per-activity `build_efficiency_trend`, but no like-sessions-bucketed, baseline-relative, abstaining EF trend). *Dependency:* N4 (temperature field enables temp-band bucketing).

**M2 — Durable `CoachingContext` layer, write-gated and TTL-tiered.** *(AI Designer, Runner, Coach)*
*What:* New user-scoped table for block intent, philosophy, medication/physiology, environmental sensitivities, structural injury; injected into every context pack; write gates + TTL by semantic class. *Why:* The persistent-context moat; makes N4's correction fire automatically every run. *Dependency:* designed user-scoped for ADR-0005. **[ASK-FIRST: schema]**

**M3 — Prior CoachReports + baseline into the pack as CONTRAST.** *(Runner — "I don't need the cathedral," AI Designer)*
*What:* Inject the last 1–2 reports' `lead_argument` + `next_step` and baseline deltas with an explicit "advance the narrative, do not restate" instruction; `DerivedMetric` stays re-derived ground truth. *Why:* Cheapest longitudinal feel; the prior report already sits in the DB keyed by activity. *Dependency:* M1 for baseline deltas; M3 alone (reports-only) can land earlier.

**M4 — Offline eval harness (15–20 seeded real activities + rubric).** *(Architect — the un-sexy precondition)*
*What:* Via `make seed-local`, freeze real activities with rubric assertions: did it lead with a headline, discount an inflated HR, avoid medical overreach, advance rather than parrot the prior report. *Why:* "Better than a cold AI" and "gets better over time" are unmeasurable without it; it is the precondition that lets confidence-routing and memory evolve safely and detects preference-drift. *Dependency:* N3/N4 give it something to score; **gates everything in the Long horizon.**

**M5 — Surface RPE-vs-HR divergence + pain-score trend as explicit signals.** *(Coach, Data Scientist)*
*What:* Compare `CheckIn.rpe` against `effort_score`; weight RPE over HR when a confounder is flagged (RPE survives HR distortion); trend `pain_score`. *Why:* The app uniquely holds both sides; reuses ADR-0007 philosophy. *Dependency:* N4 (confounder flags).

### Long horizon

**L1 — Adherence learning loop.** *(AI Designer, Coach — the literal "gets better over time")*
*What:* Label each `next_step` acted-on/ignored/contradicted from the subsequent comparable activity; store as retrievable memory; next report references it. Advisory, comparable-conditions-gated, overridable by CheckIn/chat. *Why:* The strongest behaviour-change lever and the uncrossable HITL dataset, free from Strava. *Dependency:* M2, M3, M4.

**L2 — Belief-state write-back per report.** *(AI Designer, Architect)*
*What:* Each report writes a structured belief delta (confirmed confounds, adherence, working philosophy) into the gated store the next report reads. *Why:* The architectural heart of the moat. *Dependency:* M2, M4.

**L3 — Self-calibrating personal "expected HR for these conditions" model + non-diagnostic referral layer.** *(Coach, Data Scientist, Safety Skeptic)*
*What:* Turn confounder correction from population rules of thumb into RunnerBaseline-relative individualized anomalies; add the deterministic clinician-nudge layer. *Why:* The analytical moat compounds and becomes uncrossable; keeps the product responsible. *Dependency:* M1, M4.

**L4 — Per-runner preference model (T-POP, no base-model fine-tune).** *(AI Designer; Data Scientist & Architect both gate this hard)*
*What:* Once adherence + explicit signals accumulate, a lightweight reward model that reranks framing toward advice this runner acts on. *Why:* Compounding personalization. *Dependency:* L1, M4 — **only after** the dataset and eval exist.

---

## 8. Risks & guardrails

**Safety Skeptic's non-negotiables (accepted in full):**
- **Medical-scope validator rule ships first (N2)**, before any opinionated feature. Reject dose advice and diagnosis verbs; never escalate one wearable number (off up to ~50% in some situations) into a health claim. Medication context is permitted as *interpretive correction* ("your HR reads high partly due to X, so this drift overstates fatigue") and forbidden as *directive*.
- **Confounder correction is a deterministic templated relay**, pipeline-owned, never LLM discretion — so it is auditable and the validator can catch fabrication.
- **Confidence is a validator-enforceable router** off the existing `confidence` field, not a "be-confident" tone.
- **Load ratios are framing; the validator forbids precise injury probabilities.**
- **Longitudinal memory ships behind decay, recency tags, and `DerivedMetric`-as-override.**

**Other guardrails:**
- **Population thresholds labeled as heuristics**; migrate to RunnerBaseline-relative deltas (L3) so the confident tone never outruns the evidence.
- **Trend abstention until `sample_count` crosses threshold** — never trend two runs.
- **`average_temp` degrades gracefully** — absent/wrong temp → no heat discount + lower confidence, never a fabricated confound.
- **Parroting/preference-drift**: prior reports as contrast only + anti-repetition lead-argument digest + `DerivedMetric` re-derived each run.
- **Token/cost bloat** (`max_tokens=1024` output, full pack as user JSON): retrieve a digest (last N lead-arguments), not full reports, or the pack grows unbounded as it gets richer.
- **Preview-deploy hazard**: previews point at the *production* backend/Postgres (per project notes) — every schema add (N1, N4 field, M2) must be migrated and verified carefully; a careless test write on a preview mutates real data.
- **Multi-user from day one**: memory user-scoped now, or the loop does not survive ADR-0005.

**How the validator evolves:** from four rules to five+ — add the medical-scope rule (N2), then confidence-routing enforcement and a precise-injury-probability ban (M-horizon). The existing interval-claim/zone/flag/null-checkin rules stay. The gate is never bypassed (`ai-workflow.md`).

---

## 9. Open questions for the product owner

1. **Schema approval [ASK-FIRST].** Several load-bearing items add tables/fields and touch the public `CoachReportContent` contract: version-aware caching (N1), the medication/physiology field (N4), the durable `CoachingContext` table (M2), the belief-state store (L2). These cross the project's Ask-First boundary. Approve in principle, and confirm the migration discipline given that previews share the production DB?

2. **Sequencing call.** Architect says reshape-first-but-grounded-concurrently; AI Designer warns shape-on-a-cold-pack is a prettier chatbot. The plan pairs N3+N4 in the near horizon. Do you accept landing them together, or do you want the *felt* win (N3 reshape) shipped alone first even knowing it rides an un-corrected number until N4?

3. **Medication/physiology capture.** Storing "on a stimulant," beta-blocker context, etc. is what enables the signature correction move and is *safer* than today's behaviour — but it is health-adjacent data on a non-medical product. Are you comfortable capturing it, given the validator scope gate and the strict interpretive-not-directive boundary?

4. **Confidence threshold for the trend narrative.** How many comparable sessions before the report is allowed to assert "your EF is rising"? This sets where the moat becomes visible vs. where it would over-read. (Engineering can propose; the risk tolerance is yours.)

5. **Eval before learning loop — accept the gate?** The Architect and Data Scientist insist memory/adherence (L1–L2) ship *only after* the eval harness (M4). That defers the most exciting "gets better over time" feature. Do you accept that gate, or do you want a faster, riskier path to the learning loop accepting un-measured drift?

6. **Per-report explicit feedback control.** Adding a "this was off / useful" control is the cheapest high-value explicit signal. In scope for this product, or noise you would rather not add to the UI?

7. **How opinionated, exactly?** The Runner wants a bold verdict headline; the Safety Skeptic wants confidence strictly routed off `DerivedMetric.confidence`. The plan routes per-claim. Where do you want the default lever set on a *medium-confidence* run — lead with a confident verdict, or lead by naming the data gap?

---

**Files that anchor this plan:** `backend/app/services/coach/context.py`, `backend/app/services/coach/prompts.py`, `backend/app/services/coach/validator.py`, `backend/app/schemas/coach.py`, `backend/app/models/coach_report.py`, `backend/app/models/runner_baseline.py`, `backend/app/models/user_profile.py`, `backend/app/services/trends.py`, `backend/app/services/analysis/_orchestrator.py`.
