# The coach base prompt is character + floor; training philosophy lives in the corpus

The owner's north-star brain-dump (`docs/vision/coach-north-star.md` §1) named the live
coach prompt as a problem: reports read "very samey", the system prompt is "too
conservative, too preoccupied with correctness", and it "doesn't feel human". #266 was
filed during the P1.2 corpus pass (ADR 0014 §5) to "extract and retune the implicit
coaching philosophy out of the base prompt into the corpus house-core". P1.2
deliberately *supplemented* the base prompt with `HOUSE_CORE` rather than extracting,
so the extraction was always going to be its own milestone with its own before/after
eval.

On inspection the issue's mechanism ("relocate the philosophy into the corpus") turned
out to be largely already done, and the real lever was elsewhere. The base message
prompt (`SYSTEM_PROMPT_MESSAGE_V1`) carries almost no *training-belief* prose: those
beliefs (most running easy, consistency compounds, gradual progression, recovery is
training) already live explicitly in `corpus.py` `HOUSE_CORE` and reach the model via
corpus rule 25. What the base actually holds, beyond the output protocol and the
safety/grounding floor, is the coach's **character** (the identity paragraphs) and a
conservative, hedge-forward **tone**. That character + tone is what reads as samey and
correctness-preoccupied. So #266 is not a mechanical move of training-belief text into
the corpus; it is (a) a retune of the base prose toward a warmer coach with a point of
view, and (b) an explicit deferral of *training philosophy* to the corpus that already
holds it.

**The decision.** Ship `coach_message_v8`, a new prompt version that — uniquely in the
message family — changes the BASE rather than appending an addendum. It carries exactly
v7's six capabilities (two-stage, voice, corpus, stance, training-load, user-materials),
the first message version to add none. Two things change in the base, fuller and opener
alike:

- the identity is retuned toward "their coach, who has a point of view", not "a report
  generator";
- a new `# HOW YOU SOUND` section instructs the coach to lead with the human read and
  let the numbers serve the story, to commit to a view where the data is clear and keep
  the caveat in a clause (not the headline), to vary its shape run-to-run so no two
  messages feel template-stamped, and to take its **training philosophy from the
  `corpus` section** rather than improvising a generic one.

**The coach character stays in the base; it is not moved into the corpus.** The training
*corpus* is the swappable school-of-thought library (house core + selected school + user
materials) under authority tiering; the coach's *character* is universal across every
school and every runner, and conceptually sits with voice, not with training method.
Putting character into the training corpus would both muddle the corpus / voice /
character separation (ADR 0020) and disturb the byte-stable corpus pack section under
v4-v7. So character lives in the v8 base, training philosophy lives in the corpus, and
delivery tone stays runner-sovereign under voice — three parallel homes, not one.

**The floor is invariant by construction, not by careful retyping.** Everything from
the output protocol onward — the three-movement protocol, the GROUNDING block, the
SAFETY block, and the reading/relationship data disciplines — is sliced verbatim out of
`coach_message_v1` (and the v2 opener), and the six addenda are reused byte-for-byte. So
v8 differs from v7 *only* in the leading identity + the HOW YOU SOUND section. The
deterministic policy validator (`validate_message_policy`) and the eval's
preserved-safety-surface assertions are unchanged and still hold; no new eval assertion
is added because the floor did not move.

**It ships inert; the flip is owner-gated.** The config default stays `coach_report_v10`
and production stays `coach_message_v7`; v8 exists but fires for no one until someone
sets `COACH_PROMPT_ID=coach_message_v8`. Rollback is a flip back to `coach_message_v7`
with zero code change (v8 reports regenerate under the new id, prior history retained,
the M0 versioned cache identity unchanged since schema stays 2.0).

## Considered options

- **Retune the base character + defer training philosophy to the existing corpus, floor
  sliced verbatim (chosen).** Targets the actual lever (character/tone), keeps the floor
  provably invariant, touches no corpus schema or pack, and is fully reversible. The
  smallest change that moves the owner's goal.
- **Move the coach character into `HOUSE_CORE` (or a new corpus field).** Rejected: the
  character is universal, not a training school; housing it in the swappable training
  corpus muddles the corpus / voice / character split (ADR 0020) and would change the
  corpus pack section for v4-v7, breaking their byte-stable shape for no gain.
- **Aggressively rewrite GROUNDING / evidence-strength routing to be less conservative.**
  Rejected for now: that edits the floor's wording exactly where the validator cannot
  catch over-confidence (it polices medical/zone/interval/evidence-path, not manufactured
  certainty). The HOW YOU SOUND section reframes the coach's *posture* toward that routing
  instead. A deeper cut is a follow-up the owner can direct after seeing the B-vs-A.
- **Enrich `HOUSE_CORE` training principles.** Deferred: the house core already covers the
  training philosophy the base used to imply; adding to it changes v4-v7 packs for no
  clear benefit to #266's goal.

## Consequences

- New `SYSTEM_PROMPT_MESSAGE_V8` / `coach_message_v8` (fuller + opener) + a manifest row
  carrying v7's feature set. No schema, no migration, no dependency change.
- The deterministic floor is unchanged and stays green (full suite + `make eval-selftest`);
  byte-stability of v1-v7 and the report chain is pinned by `test_message_prompt_v8`.
- The actual success criterion — "more human, less samey, less conservative" — is a
  subjective quality the deterministic eval is blind to by design (the documented #164
  blind spot). It is verified by an owner B-vs-A on real seeded runs (the same gate that
  cleared the #288 P4 flip), not by an automated number. #164 (a semantic-judge eval
  layer) is the recommended fast-follow to make that claim repeatable.
- Future prompt versions that only retune tone (not capability) should follow this idiom:
  a fresh base whose floor is sliced verbatim from the prior base, the addenda reused,
  and the change confined to character/tone. The "each version adds exactly one
  capability" invariant (`test_prompt_features`) legitimately ends at v7.
