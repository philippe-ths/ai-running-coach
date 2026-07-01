# Knowledge-Base Alignment Audit — AI Running Coach

Date: 2026-06-28
Scope: the project's LLM-system design, audited against the personal knowledge base (Obsidian "LLM wiki", AI-agents cluster: memory, retrieval, context/attention, failure modes, prompt injection, structured output, evaluation, compute, self-improvement).
Method: read the KB AI-agent concept + source pages; mapped each to a project LLM surface from `project-context.md`; then **code-verified the load-bearing claims** with five parallel read-only agents against the real source (`prompts.py`, `coach_context.py`, `context.py`, `chat.py`, `retrieval.py`, `validator.py`, `belief_store.py`, `beliefs.py`, `consolidation.py`, `eval/`, and the pinning tests). Findings below carry `file:line` evidence.

---

## Verification status (added 2026-06-28, post code-check)

Every major claim in this report was checked against code. Result: the structure holds; the spot-checks generalised. Three corrections worth reading before the body:

1. **Durable memory is currently OFF by default in code.** `COACH_BELIEFS_ENABLED` and `COACH_NARRATIVE_ENABLED` both default `False` (`config.py:221-222`), disabled after a real prod incident (rest-day advice misclassified as easy-discipline → a poisoned "ignores easy guidance" belief → the narrative replayed that fixation into every report). So §D/§E describe machinery that is **correct but dormant** — and, importantly, the project *already experienced the exact KB "confident stale memory" failure it now guards against*. This reframes those sections from "exemplary live defense" to "exemplary defense, learned the hard way, now switched off pending recalibration." It also adds the top forward risk: re-enabling without fixing the M7 misclassification root cause re-arms the incident.
2. **The user-materials risk is stronger than first framed.** Precedence of a material over the house school is *unconditional and silent* by explicit prompt instruction (`prompts.py:555,562`: where a material and the data/floor pull apart "the data and the floor win, **silently**"; where a material and house philosophy pull apart, follow the material and say nothing). There is no trust/reliability field on a material at all (`DistilledMaterial` has four fields, `extra="forbid"`). Confirmed unmitigated.
3. **Prod prompt is now `coach_message_v12`** (flipped live 2026-06-28), the fullest pack — relevant to the attention finding, since v12 carries ~8 addenda after the safety floor.

Everything else verified as written.

---

## Verdict in one line

This codebase is, to an unusual degree, a working catalogue of the KB's hardest-won lessons. The central KB theme — **"a clean surface can hide a wrong answer; separate *did it pass the check* from *is it right*"** — is essentially the project's architectural spine. The strongest surfaces (untrusted-input containment, deterministic validation, fact/voice memory split, background re-grounding) are textbook-correct against the wiki. The real exposure is concentrated in three forward-looking places: **context/attention discipline as the pack grows, retrieval reliability once user materials are live, and the evaluation metric being the thing nothing yet stress-tests.**

---

## 1. The throughline: Trusting the Surface

`wiki/concept-trusting-the-surface` and its refinement `wiki/concept-silent-failure-mechanisms` argue that surface success ≠ real success, and split the failure into two root causes needing two different cures:

- **Group A — provenance failures** (untrustworthy because of *where content came from*): poisoned retrieval, planted instructions, stale consolidated memory. Cure: track provenance, treat external content as untrusted, weigh source reliability, build staleness checks.
- **Group B — commitment-timing failures** (forced to commit before reasoning): hard schema from the first token, quitting at the first error. Cure: reason free, constrain late; retry/state-check scaffolding.

The project's coach pipeline is built around exactly this distinction, mostly without naming it:

- **Group B is solved structurally** by the A3 prose-first output protocol: *reason privately → write prose `message` → call the strict tail tool once* (`output_contract.py`, `coach_message_v*`). This is "reason free, constrain late" verbatim.
- **Group A is solved by the fact/voice authority split**: deterministic re-derived `DerivedMetric` is the only fact; the LLM `narrative` and the `corpus` are explicitly *voice/judgment only, never evidence*, enforced in three layers (prompt rules, validator rules 6/7/8, eval assertions).

That a personally-collected research theme and an independently-built product converge this precisely is the headline finding. The rest of this report is where the convergence is exemplary, partial, or absent.

---

## 2. Surface-by-surface audit

Each surface: the KB lesson, the project's stance, a verdict (✅ Aligned / 🟡 Partial / 🔴 Gap-or-Risk).

### A. Structured output & the constraint tax — ✅ Aligned (exemplary)
KB: `concept-structured-output` / `src-constraint-tax` — hard schema from the first token costs ~43 pts of executable accuracy on the field that needs reasoning; "reason free, constrain late." `src-built-in-thinking-instruction-following` sharpens it: thinking trades *planning* accuracy for *precision* (exact form/count) accuracy.

Project: the A3 path generates prose first, then merges a strict `record_coach_tail` tool call, and **degrades-not-withholds** — a real message survives a missing/unusable tail as `tail_degraded=True`. The schema is applied last, exactly as prescribed.

Note the `src-built-in-thinking` nuance applies to one real surface: the **deterministic policy validator runs *after* generation on the precision-sensitive fields** (interval counts, zone tokens) — precisely the fields thinking-mode is documented to degrade. The validator is the right backstop for that risk. No action needed; this is the surface working as the KB predicts it should.

### B. Deterministic validation vs the surface — ✅ Aligned
KB: validation checks *shape*, not *truth*; track the wrong-valid rate; a fluent/confident tone is a reason to verify, not relax.

Project: `validator.py` is a genuinely deterministic regex/structured gate (verified: 537 lines, 8 rules, no LLM in the loop), assembled from shared `check_*` bodies and run over BOTH output shapes (`validate_policy` for structured, `validate_message_policy` for prose) and over **streamed chat** (`chat.py`, #340). It polices every rendered surface including tappable-chip labels (`_extract_message_text`, validator.py:443). A surviving medical overreach forces `is_fallback=True`. `ai-workflow.md` forbids bypassing it. This is the project's single strongest alignment with the KB.

One observation, not a defect: the medical-scope rule is an enumerated regex (`_CONDITION_TERMS`, validator.py:82-101) with self-documented leaks (spelled-out doses, unlisted conditions). The KB would frame this as Hyrum/precision-vs-recall, not as a hole — the design correctly prioritises precision (avoid false fallbacks) and backstops the highest-cost misses. Worth a periodic adversarial sweep (see §3).

### C. Untrusted input / prompt injection — ✅ Aligned (exemplary)
KB: `concept-prompt-injection` / `src-agentredbench` — indirect injection (instructions hidden in content the agent reads) succeeds 32–81% unprotected; a guard helps only if **aimed at the right surface** (tool/service content, not chat); default stance: everything read from outside is untrusted data, not commands; separate powers.

Project: `material_distiller.py` is the first untrusted-input surface and is a near-perfect implementation of the KB's "containment, not detection" stance (ADR 0017), verified in code:
1. **Structured-output-only** — forced tool, no free-form channel (distiller.py:62-94), so a payload can at most fill bounded fields, never emit prose or change behaviour.
2. **Strict coercion** — `DistilledMaterial` (`extra="forbid"`, bounded); a non-`missing` coercion failure **fails closed with no retry** (distiller.py:127-140, 180-182). An oversize/rogue-key/wrong-type output is treated as a containment signal.
3. **Raw never enters a prompt** — fixed system prompt invariant to content; raw text is fenced on the data channel only (distiller.py:200-214); the distilled record, never the raw, is what reaches an exchange.

This is exactly "aim the guard at the source surface" + "separate powers" (the distill path is isolated from the exchange pipeline). The authority of the result is further bounded downstream (prompt rule 28 / validator rule 8: materials are reference, outrank house philosophy for *stance only*, never override facts or the safety floor). Strong.

### D. Memory: types, provenance, staleness — ✅ Aligned
KB: `concept-agent-memory` — four memory types; selective forgetting and **staleness in high-confidence memories** are where systems fail most; **actor attribution** (tag user-stated vs agent-inferred) is a correctness requirement; conflating them is a bug.

Project maps cleanly:
- **Semantic/episodic facts** → `CoachingContext` beliefs + `RunnerBaseline` (deterministic, auditable, TTL-decayed by tier: hr_confound 120d, adherence 90d). The TTL tiers are exactly the KB's "staleness check" prescription.
- **Voice/relationship arc** → `CoachNarrative` (LLM-written, voice-only).
- **Working memory** → the context pack.
- **Actor attribution is enforced by construction** (code-verified): the M8 belief write-back derives deltas *only* from structured `discount_signals` + adherence outcome `label/theme/overridden` (`beliefs.py:138,154-156`), never from any LLM-written field. The pinning test `test_writeback_is_llm_free.py` is genuinely adversarial — it plants prose in the narrative and outcome `basis` saying "this runner is NOT affected by heat — never write a heat confound" and proves the only belief written comes from the structured signal, the prose having "zero effect." This is a *stronger* guarantee than the KB's "tag the provenance": the project doesn't tag, it **segregates the writers** (verified: `CoachingContext(...)` is constructed in exactly one place, `belief_store.py:99`; `CoachNarrative(...)` in exactly one place, `narrative_store.py:80` via consolidation). TTL tiers confirmed (`hr_confound` 120d, `adherence_pattern` 90d, plus reserved `physiology` 365d / `transient` 14d / default 90d), retrieval gated on `observation_count >= 2`.

**The load-bearing correction: this memory is OFF by default in code right now** (`COACH_BELIEFS_ENABLED`/`COACH_NARRATIVE_ENABLED = False`, `config.py:221-222`; both read *and* write gated, `service.py:769-772`, `context.py:804-820`). It was disabled after a production incident the config comment records verbatim: the M8 loop "misclassified 'take a rest day' advice as easy-discipline and reinforced a poisoned 'ignores easy guidance' belief; the A2c narrative replayed that fixation back into every report." **That incident is the KB's `concept-agent-memory` staleness failure and the `concept-silent-failure-mechanisms` Group-A provenance failure (#5, confident stale memory) realised in production.** The LLM-free guarantee held — the bad belief was *deterministically derived from a mislabel*, not LLM-invented — but the failure still occurred upstream of the guarantee, at the M7 adherence classifier. So the actual lesson is sharper than "the project pre-empted the KB risk": it pre-empted *contamination of facts by LLM text*, then hit *contamination of facts by a deterministic mislabel*, which the segregation guarantee does not cover. The correct read: the write-back's auditability is exactly what let the team *diagnose* the poison and kill-switch it — the KB's "track provenance" paying off in the post-mortem.

`src-memtrace` ("evidence-use, not retrieval, is the bottleneck") is latent but not yet a concern at this memory scale.

### E. Background consolidation = sleep-time compute + the staleness trap — ✅ Aligned by design (currently dormant)
KB: `concept-inference-compute-scaling` / `src-sleep-time-compute` — a background agent (slower/cheaper model is fine, it's not latency-bound) maintains shared memory during idle time. **The catch** (`concept-agent-memory`, `concept-trusting-the-surface`): background work can bake a stale/wrong conclusion into confident memory — "a faster path to the wrong answer."

Project: the A2c `Consolidation` job is a faithful sleep-time-compute architecture — decoupled from the user-facing turn, runs the cheap hardcoded `claude-haiku-4-5`, maintains the shared narrative row. It **defuses the documented staleness trap by construction** (verified, `consolidation.py:11-17,83-140`): `assemble_consolidation_inputs` reads beliefs + preferences + baseline scalars + recent digests and **never reads the prior `CoachNarrative`**, so "a stale claim it can no longer support simply disappears." It writes only the narrative row, never a fact.

Caveat, per §D: in the incident the *narrative did its job too well* — it faithfully re-grounded on a poisoned belief and replayed it. The staleness defense protects against the narrative drifting away from the facts; it does nothing when the *facts themselves* are wrong. That is the gap the kill switch currently covers and the recalibration must close before re-enable. So this is best read as "architecturally correct, and the right half of the problem solved" — not a complete defense against bad memory, because the narrative is only as good as the deterministic facts it re-grounds on.

### F. Retrieval reliability — 🟡 Partial / forward risk
KB: `concept-agent-retrieval` + `src-confident-wrong-document` — relevance ≠ reliability; even a *clean* retrieved set pulled models off ~38–42% of answers they already knew, rising past 56% fully poisoned, with **confidence inflation** (wrong answers stated *more* assertively as context worsens); rank/filter on **source trustworthiness**, not similarity; prefer systems that **surface disagreement** rather than silently pick a side.

Project today: retrieval is mostly **keyed lookup, not vector similarity** (`fetch_corpus` keys the school; `fetch_prior_digests`/`fetch_prior_commitments` are SQL by user/recency). House corpus is code-resident and trusted, so the poisoned-document risk is structurally near-zero there — a deliberate, good choice that sidesteps the entire failure family.

The exposure is at the two surfaces that ingest non-house content:
1. **User materials** (`corpus.user_materials`). These are distilled (containment is solid, §C), but once consumed (prod runs v12, materials-aware), a *distilled-but-misleading* material is exactly a "confident wrong document" with elevated stance authority. Code-verified, the gap is wider than first framed: materials are gated only by recency (`created_at desc`), `status=="active"`, a count cap of 10, and a strict-schema shape-skip (`retrieval.py:97-110`) — **no trust/reliability field exists at all** (`DistilledMaterial` has exactly four fields, `extra="forbid"`, `material.py:44-55`), so a freshly-uploaded misleading material outranks the house school *identically* to a faithful one. And precedence is **unconditional and silent by explicit prompt instruction**: where a material and the data/floor pull apart "the data and the floor win, **silently**" (`prompts.py:562`); where a material and the house philosophy pull apart, "follow the runner's material" and say nothing (`prompts.py:555`). Validator rule 8 only stops the model citing a `corpus.user_materials.*` path *as evidence* (`validator.py:366-389`); it never inspects material content or detects contradiction. **There is no conflict-surfacing of any kind** (searched `context.py`, `prompts.py`, `corpus.py`, `stance.py`, `validator.py`) — exactly the KB's recommended defense, and it is absent.
2. **Cross-activity chat digest** (`chat._build_cross_activity_block`, #339) injects the runner's own recent chat from other activities. Low poisoning risk (self-authored) but it is unranked-by-reliability recency injection — watch it if it ever widens.

Recommendation in §3.

### G. Context window & attention — 🟡 Partial (the clearest structural gap)
KB: `concept-context-window-and-attention` / `src-attention-closure` — attention is a fixed light budget; the Goal Accessibility Ratio to the system prompt declines every turn with a *predictable crossover turn* where instruction-following falls off a cliff; **fix = re-inject the rules near the crossover**. Tool/section saturation is the same mechanism.

Project: two sub-surfaces, different exposure. Both code-verified; the framing held, with sharper specifics.
- **The report pack** is single-turn and assembled fresh each time, so multi-turn drift doesn't apply. But the system prompt order is fixed (`prompts.py`): identity → HOW YOU WORK → GROUNDING → **SAFETY** → reading → relationship → *then* ~8 appended addenda (voice/corpus/stance/readiness/user-materials/volume/stream-view/recent-training/training-history) + the activity playbook, followed by a large JSON pack as the user message. So in v12 the medical/zone/interval **safety floor is stated exactly once, early, ahead of eight addenda and the pack, and is never re-stated near the end** (the `safety_rules` *pack* field is last, but it carries only `{never_diagnose, pain_severe_threshold, no_invented_facts}` — a thin data echo, not the prose floor; `coach_context.py:123-128`). The byte-stable Optional-and-drop registry (`PACK_SECTIONS`, #493) and the one-fact-one-place folds are excellent *hygiene* but manage presence and dedup, **not attention budget**. A broad grep found no crossover-turn estimate, no goal-accessibility measure, no length-triggered re-injection anywhere in `app/`. The validator is the hard backstop that makes this non-fatal — but "the validator catches it" is defence-in-depth, not the KB's preferred "re-inject so the model doesn't drift in the first place."
- **Coach chat is genuinely multi-turn** and `concept-context-window` applies directly. Nuance from code: the system prompt (carrying rule 2 medical floor + the `RELATIONSHIP MEMORY & AUTHORITY TIERING` block + voice) *is* re-sent fresh on every call (`chat.py:119-163,496-500`), so the rules are technically present each turn — but they sit at the **front**, ahead of the full replayed `messages` history (`chat.py:462-470`), which grows every turn. So the floor drifts ever further from the generation point exactly as the KB predicts; there is no per-turn re-statement *after* the history and no length-aware re-injection. Here too the real guard is post-generation: chat buffers the full reply and runs the deterministic `check_*` bodies before any byte ships, withholding a medical overreach and serving `MEDICAL_REDIRECT_MESSAGE` (`chat.py:478-527`). It catches; it does not prevent.

Recommendation in §3.

### H. Evaluation — ✅ Aligned today, 🔴 the metric is the unguarded flank
KB cluster: `src-grader-agrees-by-luck`, `src-llm-judges-dark-current`, `src-judge-with-a-fever` — an LLM judge is a *measurement instrument* with dark current and position bias; demand chance-corrected agreement and a position-swap test before trusting it. And the meta-lesson from `concept-self-improving-agents` / Goodhart: **strengthen the metric before you let anything optimise against it.**

Project: the M5 eval harness is a **deterministic rubric** (verified: eleven assertions, no Anthropic/LLM call anywhere in `eval/` — the only two grep hits are comments *asserting* the absence of a judge), scoped per `(prompt_id, schema_version)`, scored per-report with no cross-row joins so it is order-independent and byte-stable. Against the KB's judge-reliability warnings this is the *safe* choice by construction — there is no dark-current/position-bias surface because there is no LLM grader. A nice touch: `compare_scorecards` treats an assertion that *stops being evaluated* as a regression, not a vacuous pass ("a gate that silently stops checking a rule is worse than no gate"). The harness already pre-stages the #164 seam honestly: `rubric.py:368` names the semantic-parrot gap as "the LLM-judge upgrade target," and `TestKnownBlindSpots` pins today's blind-spot passes with a note that they "should flip to FAIL when the LLM-judge tier lands."

Two flags, both forward-looking:
1. **The deferred #164** ("automate the *feels-human / less-samey* B-vs-A that the deterministic eval is blind to by design"). The moment that lands, the project acquires an LLM-judge surface and inherits the entire `concept-trusting-the-surface` judge cluster. The KB has a ready-made checklist for it (chance-corrected kappa, position-swap/dark-current test, a "Judge Datasheet"). Build the judge *with* that checklist, not after.
2. **Goodhart / the freeze audit** (`concept-self-improving-agents`): prompt versions advance v8→v12 by human retune against the deterministic rubric. The rubric is the frozen meta-layer. It is a *floor* (safety, grounding, abstention) and explicitly **not** a quality ceiling — which is correct and self-aware (it's why #164 exists). The risk is subtle: every prompt that passes the eleven assertions reads as "no regression," but the rubric measures the safety surface, not whether the coaching got better or blander. The KB's "strengthen the metric before loosening anything" says: the human B-vs-A *is* the current quality metric, and it lives entirely in the owner's head. That's a single point of failure for "is the coach actually good," not just "is it safe."

### I. Agent failure modes — ✅ Aligned where applicable (mostly N/A)
KB: `concept-agent-failure-modes` — strategic defeatism, clean-slate/overconfidence (read-before-write), tool saturation. These target *tool-using* agents.

The coach is largely a **generate-validate-store** pipeline, not a multi-tool agent, so most of this is out of scope by design (a good thing — it avoids the failure family). Two touchpoints:
- **Strategic defeatism's inverse is implemented**: degrade-not-withhold + the policy-retry-then-fallback loop is the "retry once, don't quit at the first error" discipline, expressed for generation rather than tools.
- **Tool saturation** is structurally avoided: the LLM calls one strict tail tool, not a drawer of 100. The MCP-tool saturation lessons (`src-toolchoice-confusion`, `src-complexmcp`) apply to *you* (the dev agent, with dozens of deferred tools) more than to the product.

### J. Streaming — 🟡 Aligned-after-incident (lesson already paid for)
KB: `src-streaming-tool-use` — streaming hides latency only when intent stabilises early; buffering vs streaming is a real trade.

Project: the #340→#375 chat incident is this lesson learned the hard way — buffering the full reply for validation introduced a silent gap that severed the proxy connection, fixed with content-free heartbeats. The recorded lesson ("TestClient buffers the body → blind to streaming timing; verify via httpx ASGITransport or a real server") is precisely the kind of thing the KB exists to pre-load. Consider promoting it into the KB as a source page so the next streaming change doesn't re-discover it.

---

## 3. Gaps and recommendations (prioritised)

Ordered by KB-weighted risk. None are "the system is broken"; all are "the KB predicts this is where it will break next."

0. **Gate the durable-memory re-enable on a staleness/classifier fix, not a config flip.** Beliefs+narrative are off because the M7 adherence classifier mislabelled "rest day" advice and the loop reinforced a poisoned belief (the KB's confident-stale-memory failure, lived). Before flipping `COACH_BELIEFS_ENABLED`/`COACH_NARRATIVE_ENABLED` back on: fix the misclassification at M7, and add the KB's prescribed *staleness check* — a way for an opposing observation to *resolve a belief's direction* fast (the code has `acted/total` resolution; verify it actually reverses a poisoned belief within a few runs, not over a 90-day TTL). The auditability that diagnosed the incident is the asset; lean on it. (KB: `concept-agent-memory` staleness, `concept-silent-failure-mechanisms` Group A.)

1. **Retrieval reliability for user materials (Group-A provenance).** Before/while materials are live in prod, add the KB's specific missing piece: **conflict-surfacing**. When a distilled material's stance materially conflicts with the runner's measured data or declared goal, the coach should *name the disagreement* rather than silently weight the material. Today the rule is "materials lose to facts" (correct) but there is no handling of material-vs-goal stance conflict, which is the `src-confident-wrong-document` failure with extra authority. Cheap first step: a deterministic check that flags when a material's `emphasis_hints` contradict the runner's goal, surfaced as a caveat. (KB: `concept-agent-retrieval`, `src-confident-wrong-document`.)

2. **Attention budget for the growing pack + multi-turn chat (Group-B / closure).** The pack only grows. Two concrete moves: (a) measure/assert the *position* of the GROUNDING/SAFETY block in the assembled system prompt and **re-state the safety floor near the end** of long prompts; (b) for coach chat, **re-inject the authority/voice/safety disciplines every N turns** rather than once at thread start, sized against the crossover-turn estimate. The validator is the backstop, but the KB's point is to not rely on the backstop. (KB: `concept-context-window-and-attention`, `src-attention-closure`.)

3. **Pre-commit the LLM-judge checklist for #164.** When the "feels-human" B-vs-A is automated, ship it *with* chance-corrected agreement, a position-swap test, and a dark-current (equal-input) probe baked into the harness — not as a follow-up. A judge added without these is a new "trust the surface" surface in the one place (eval) that's supposed to be the guard. (KB: `src-grader-agrees-by-luck`, `src-llm-judges-dark-current`, `concept-self-improving-agents`.)

4. **Make the quality metric less frozen-in-the-owner's-head.** The deterministic rubric is a safety floor by design; "is the coaching good/varied" lives only in owner B-vs-A. That's a Goodhart/freeze-audit flag: the optimised-against metric (rubric) is not the metric you actually care about (quality). Lightweight mitigation: capture a small, durable labelled set of owner B-vs-A judgments so the quality signal survives outside one person's session memory and can later seed #164. (KB: `concept-self-improving-agents`, `concept-software-engineering-laws` / Goodhart.)

5. **Periodic adversarial sweep of the medical-scope regex.** The enumerated `_CONDITION_TERMS` / dose patterns have documented leaks (spelled-out numbers, gram-dosed compounds, unlisted conditions). Since this is the hard safety floor and the referral nudge leans on it, schedule a small red-team pass (the `aiw-security-testing` discipline) against spelled-out and obfuscated phrasings rather than only synthetic fixtures. (KB: `concept-prompt-injection` / Hyrum, `concept-trusting-the-surface`.)

6. **Two KB-worthy lessons to promote back into the wiki.** Your KB is missing two source pages this project has *paid for in production*: (a) the streaming-buffer-vs-proxy-timeout incident (#375), and (b) the writer-segregation pattern (deterministic facts and LLM voice never share a writer) as a cleaner answer to the KB's "actor attribution" open problem. Both would strengthen the wiki's "from practice" layer. (Offered as KB hygiene, not a code change.)

---

## 4. What the KB would call the top risks (re-ranked after code-check)

0. **Re-enabling durable memory without fixing the root cause.** This jumped to the top after verification: beliefs+narrative are kill-switched off in code today *because the KB's confident-stale-memory failure already happened in prod* (a mislabel poisoned a belief, the narrative replayed it). The LLM-free guarantee is intact, but the poison entered through the M7 adherence *classifier*, which the guarantee doesn't cover. Re-enable is a one-line config flip; doing it before the M7 misclassification is recalibrated re-arms the exact incident. The KB's "strengthen the metric / build staleness checks before trusting consolidated memory" is the literal to-do here.
1. **User materials as confident-wrong-documents** once fully exercised — contained at ingestion (containment is excellent), but unconditionally and *silently* outranking house philosophy at use, with zero trust-weighting and zero conflict-surfacing. This is the largest *unmitigated* surface, since the prod prompt (v12) already consumes materials.
2. **Attention drift in long chat threads** is the one live multi-turn closure surface with no re-injection discipline; the validator catches *unsafe* drift but not *off-character/off-instruction* drift.
3. **The quality metric lives only in the owner's head.** The deterministic rubric is a safety floor; "is the coaching good/varied" is the human B-vs-A, durable nowhere. Goodhart: every prompt that passes the eleven assertions reads as "no regression" while measuring nothing about quality. This is the surface #164 will turn into an LLM-judge — build it with the KB's judge checklist (chance-corrected kappa, position-swap, dark-current probe) from day one.

## 5. What the project gets conspicuously right (so it isn't lost)

- Reason-free-constrain-late as the *default* generation shape (not a patch).
- A hard deterministic validator that cannot be bypassed, policing every rendered surface and both output shapes and streamed chat.
- Containment-not-detection for untrusted input, fail-closed on any off-shape result.
- Deterministic facts and LLM voice that **never share a writer** — provenance by segregation.
- Background consolidation that re-grounds from facts and refuses to feed its own prior output back, pre-empting the exact staleness trap the KB flags.
- Choosing keyed lookup over vector similarity for the trusted corpus, sidestepping the whole poisoned-retrieval family for house content.

These six are the project living the KB's lessons before reading them. The recommendations above are all at the frontier the project is moving *toward*, not behind it.
