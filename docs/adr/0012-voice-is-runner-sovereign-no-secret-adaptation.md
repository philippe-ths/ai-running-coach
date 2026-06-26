# Coach voice is a runner-declared, runner-sovereign dial; no secret adaptation

The pre-P1 plan (recorded in the old `CONTEXT.md` Voice entry and the north-star) was "declared at onboarding, then adapted by the relationship from how the runner responds" — a P1b adaptive-refinement loop that would silently re-infer the runner's preferred tone from their reactions, generalising the M10 `Preference profile` from advice-themes to voice. The P1 Voice design session reversed that. Under the project's standing "trust the user" principle, secret tone-inference is the *wrong* feature, not merely a deferred one, and it collides with two hard constraints already in the codebase.

**The decision.** Voice is **runner-sovereign**: the runner sets it, owns it, and re-dials it any time, and the system never second-guesses the chosen tone. Voice is *declared*, never *inferred*. A runner places their voice by choosing one of the house presets, then nudging the four operable dials (Clinical↔Warm, Earnest↔Playful, Gentle↔Blunt, Calm↔Fired-up), with a free-text escape-hatch on top. The declaration persists on the `coaching_relationship` row and resolves to a moderate default when unset.

**Refinement is the runner turning the knob, plus the coach noticing out loud.** There is no background LLM tone-inference. The coach *may* raise the voice question explicitly ("I've been blunt lately — want me to ease off?") and let the runner decide, but it never changes voice on its own. This replaces the previously-planned adaptive loop entirely.

**Why secret adaptation is wrong here, not just deferred.**
- It would violate the standing deterministic-write-back rule (ADR 0008 / the M8 belief loop): durable relationship state is derived only from deterministic facts, never LLM-inferred. A "the blunt voice is landing" belief is an LLM judgement about the runner's emotional reaction, exactly the kind of write the belief store is built to exclude.
- There is no ground-truth signal for "did a blunt voice land?" A flat or curt runner reply is not evidence the tone failed; inferring otherwise invents a signal the data does not carry.
- It contradicts trust-the-user: silently steering the runner's chosen tone away from what they picked is the system overriding a sovereign choice.

**The cautious-beginner risk is handled by the default and onboarding, not by override.** The worry that a runner picks savage Roast, gets torn down, and quits is answered by starting them at the moderate centre and leading them outward through onboarding — never by the coach refusing or softening the voice they chose. The coach delivers the declared voice; it does not protect the runner from their own dial.

## Considered options

- **Declared + secret adaptive refinement (the prior plan).** Rejected as the wrong feature: it needs an LLM-inferred durable write (banned by the deterministic-write-back rule), has no ground-truth signal to learn from, and overrides a sovereign choice. Reversing a shipped adaptive loop after the relationship is built around it would be far costlier than not building it.
- **Declared only, coach never mentions voice.** Rejected: loses the one legitimate, trust-preserving refinement path. Letting the coach *ask* and the runner *decide* keeps the runner sovereign while still allowing the relationship to evolve its tone.
- **Declared, with adaptation behind an explicit opt-in toggle.** Rejected for P1: it still needs the inference machinery and the ground-truth signal that does not exist; it is a heavier commitment than the explicit ask, for a benefit the explicit ask already delivers. Not foreclosed forever, but not P1.

## Consequences

- The `coaching_relationship` row (the thin singleton ADR 0011 shipped) gains the voice declaration columns; nothing writes voice except the runner's explicit action. No background job, no LLM call, ever mutates voice state.
- The M10 `Preference profile` loop is **not** generalised to tone, closing the "generalise to voice" forward-pointer from the north-star.
- "Refinement" in all P1 voice docs means the runner re-dialling, optionally prompted by the coach asking — never silent inference. The `CONTEXT.md` Voice entry is rewritten to runner-sovereign explicit re-dialling and its *Avoid* list bans calling explicit re-dialling "adaptive refinement".
- This ADR is hard to reverse once the relationship is built around a sovereign voice: a later adaptive loop would have to overcome the runner's expectation that the system never changes their tone behind their back. The reversal is recorded here deliberately so future work does not re-suggest secret adaptation as an obvious improvement.
- Free-text in the declaration is runner input the coach reasons over for *tone only*; its untrusted-input handling and the floor-invariance guarantee are the subject of ADR 0013.
- **The presets are house-original archetypes, never trademarked-coach impersonations.** We ship house-original presets that evoke archetypes plus a free-text escape-hatch for named characters. A runner typing "talk to me like Dr House" is *their* untrusted tone-input we reason about; *us* shipping a "Dr House" button would be the product impersonating trademarked IP at commercial scale. The runner still gets any character they want through the hatch; we do not ship the impersonation. The design goal is distinct, varied presets, not resemblance to any real person.
