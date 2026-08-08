# ADR 0030: Voice is a rewrite of a finished report, not a steer on writing it

- Status: Accepted
- Date: 2026-08-08
- Issue: #822
- Supersedes the mechanism of ADR 0012 / ADR 0013; their guarantee is unchanged and now structural.

## Context

ADR 0013 states the rule voice has always had to obey: **voice flexes delivery only — never the facts, the grounding data, or the safety floor.** Until now that was enforced by instruction. The runner's declared voice was rendered into the report system prompt, and prompt rules plus a cross-voice invariance test were asked to keep it in its lane.

Measured on real seeded data (2026-08-07, one activity, one context pack, voice as the only variable, eleven generations under the live `coach_message_lean_grouped_v7`), it did not stay in its lane.

The safety floor held everywhere: the physio referral was relayed in all eleven. But on the runner's actual training problem — a 7-day load ~47% down with a detraining trend, against a half-marathon goal — the handling split by voice. The Analyst named detraining in three of four samples. The Roast asked whether it was "a choice or a drift". A dials-only voice said "next week needs to look more like your normal". **The Cornerman soft-pedalled it in two of four** — *"and that's fine… part of the long arc, not a gap in it"* — once advised training **lighter**, and once dropped the half-marathon goal entirely.

The mechanism was legible. Cornerman's and Sage's example messages are all affirming; Analyst, Drill Sergeant, Roast and Deadpan each carry a corrective one. The two characters with no example of their voice *delivering bad news* were exactly the two that softened bad news. Independently predicted by the house KB note on voice design: few-shot exemplars "should cover the awkward cases… not the easy ones".

Patching the example sets would have fixed those two characters. It would not have fixed the class: any voice steering generation can reach the verdict, and no test we had would notice. The cross-voice invariance test checks the *safety floor* is relayed, not that the *training assessment* is stable.

## Decision

**The report is generated with no voice input at all, and the runner's voice is then applied as a rewrite of that finished text.**

1. `build_system_prompt` no longer takes or appends a voice. The report prompt is voiceless on both two-stage modes.
2. A voice rewrite stage (`services/coach/voice_rewrite.py`) receives the completed prose and the runner's character brief, and returns the same report said in that voice.
3. **The rewrite may re-word and re-emphasise** — re-order points, change what leads, expand or compress emphasis. It **may not** introduce a fact the baseline did not state, drop or soften a concern, remove anything about pain/injury/illness/seeing a clinician, or change what the report recommends.
4. **Both versions are retained.** The voiceless baseline stays where every downstream consumer already reads it (digest, eval harness, adherence loop — all of which consume substance); the voiced rendering is a separate field, and is what the runner reads.
5. **Default means genuinely off.** A runner who has declared nothing gets no block, no second pass, and no charge — the current path exactly.
6. **Every failure serves the baseline.** Default voice, kill switch, over budget, transport error, a number the baseline did not contain, or a policy violation the baseline did not already carry: all resolve to the unvoiced text. A style pass must never cost a runner their coaching.

## Consequences

**The ADR 0013 guarantee becomes structural.** A warm voice can no longer decline to deliver an unwelcome verdict, because the verdict is settled before it sees anything. Re-measured after the change: Cornerman now says *"fresh doesn't build fitness on its own"* on the same run it previously called fine.

**Voice becomes auditable.** Every voiced report can be diffed against the text it came from, and the runner can read the original — it renders above the voiced version, because that is the order things happened in.

**Extremity becomes safe to ship.** A character can be as brutal or as comedic as the runner wants, because the worst it can do is say a correct thing badly. This is what makes the wide axis range viable rather than reckless.

**What is given up: voice-driven noticing.** ADR 0013's KB source argues voice is "a material, not a paint job — a blunt senior engineer and a patient teacher surface different information on the identical question." That is true, and we are deliberately refusing it. In the eleven-generation probe, The Roast alone spotted that the runner went sub-5:00/km at the finish and called it "basically a written confession that you sandbagged the whole thing". A rewrite cannot produce that, because the baseline never noticed it. We trade voice-driven insight for a guarantee we can prove, and take the insight loss knowingly.

**Cost.** The rewrite never receives the context pack, so it is a short call — measured at 1,214 input / 409 output tokens against a report sending ~23,000 characters of prompt and pack. That makes the model choice a quality decision, not a cost one: a Haiku-class model holds the contract but flattens the character (the Analyst voice returned near-verbatim), so `COACH_VOICE_MODEL_ID` defaults to the report model.

**Rollback.** `COACH_VOICE_BLOCK_ENABLED=false` disables the rewrite entirely, and every runner reads the baseline. Because that switch already withheld the voice block, the prod report prompt is byte-identical before and after this change — verified by hashing both modes and both playbook states against `HEAD` in a worktree.

**Not covered.** The conversational thread turn still steers at prompt time via `render_voice_block`, which is unchanged. Moving it to the same seam would add a second model call to the surface where latency is most felt, and is deliberately left as follow-up.
