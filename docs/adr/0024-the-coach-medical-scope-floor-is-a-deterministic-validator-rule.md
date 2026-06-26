# The coach medical-scope floor is a deterministic validator rule

This ADR records a safety invariant that has shipped since the first opinionated coach feature but never had a decision record of its own. Its specification lived only in `docs/coach-report-improvement-plan.md` §8 (the M0-M10 build plan), and three shipping citations pointed at that build-scaffolding doc: `validator.py` (the rule comment and the `check_medical_overreach` docstring) and `test_policy_validator.py` (the rule's test class). As that plan is retired into its ADRs, the floor needs a durable home. This ADR is that home; the citations now point here.

The coach is an LLM producing opinionated post-run analysis from a runner's data. It must give general-wellness coaching and never medical advice. The risk is concrete: a single wearable number (HR, especially) can be off by a large margin (~50% in some conditions), so a model that escalates one reading into a health claim is both wrong and unsafe. This was the Safety Skeptic's first non-negotiable (N2 in the plan): the medical-scope rule ships *before* any opinionated feature, and the deterministic gate is never bypassed (`ai-workflow.md`).

## Decision

The medical-scope floor is enforced **deterministically in code**, not by prompt alone. A prompt instruction is a request; this is a gate. It lives as rule 5 (`check_medical_overreach`) in `app/services/coach/validator.py`, runs after Pydantic schema validation, and is one of the shared `check_*` rule bodies applied to every coach output surface: the structured report (`validate_policy`), the A3 prose message plus its tail (`validate_message_policy`), and the streamed chat reply (`chat.py`, buffered before send).

The boundary it enforces:

- **Forbidden.** Dose advice (a pharmaceutical dose/dosage instruction, matched by unit); diagnosis verbs; directive medication advice; asserting or naming a clinical condition about the runner; escalating a single wearable number into a health claim.
- **Permitted.** Interpretive metric correction ("discount this HR drift, it was hot, so it overstates fatigue") and the non-diagnostic referral nudge ("consider seeing a clinician"). Medication *context* is allowed as interpretation and forbidden as direction: "your HR reads high partly due to X, so this drift overstates fatigue" is fine; "take X" is not.

High precision is the explicit design priority over recall: an over-firing rule rejects legitimate reports and forces a fallback, so the patterns are deliberately narrow. They exclude sports-nutrition grams and the idiomatic coaching sense of "dose" ("small doses of easy running"); a real dose instruction is caught by its pharmaceutical unit or by a change-verb targeting a "dose"/"dosage".

How a violation is handled depends on the surface, but the floor is the same:

- On the structured and prose report paths, a violation triggers a corrective retry; a medical overreach that survives the retry forces `is_fallback=True` (the prose renders verbatim, so a surviving overreach must not reach the runner).
- On the streamed chat path, a medical overreach withholds the raw reply and serves a fixed safe non-diagnostic redirect (itself written to pass the validator).

## Consequences

- The floor is invariant across every personalization layer. Voice, Stance, the coaching corpus, and uploaded user materials flex delivery, emphasis, and method-framing, but none may lower this floor. That invariance is guarded by the eval `*_preserved_safety_surface` assertions and the cross-voice / cross-stance / cross-material invariance tests (a fired referral nudge must still be relayed regardless of voice, school, or material), and structurally by the validator running last on every surface.
- `validator.py` and `test_policy_validator.py` cite this ADR as the oracle for the rule, in place of the retired build plan. The rule's behaviour is unchanged by this ADR; only its specification moved.
- Known limits (accepted, in service of precision): spelled-out dose numbers ("five hundred milligrams") and gram-dosed compounds are not matched. Widening them risks false positives that would force unnecessary fallbacks; revisit only with evidence of a real miss.
- The other §8 guardrails (deterministic confounder relay, the confidence router, load-ratios-as-framing, decay/recency-tagged memory) are separate concerns recorded elsewhere (the pipeline code, ADR 0008, and `project-context.md`); this ADR is scoped to the medical-scope floor specifically, which is the part with live code/test citations.
