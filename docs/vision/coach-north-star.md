# Coach North-Star: Vision, Decisions, and Roadmap

Status: drafted 2026-06-09 from an interactive design session (grill-with-docs). Holds the owner's vision, the decisions taken, the layered map of how they link, and a dependency-ordered roadmap. Glossary terms resolved in this session live in `CONTEXT.md`; this doc is the why and the plan, not the vocabulary.

**Build status (updated 2026-06-17): COMPLETE.** Every milestone in the §5 roadmap, across Phases 0 through 3, is built and merged (epic #177), and production runs the final prompt `coach_message_v7`. The M0-M10 build was the prior baseline; this north-star's reframe (relationship spine, `Block` event unit, two-stage `Exchange` + receipt cadence, prose output, split `Durable memory` with pull retrieval, `Voice`/`Coaching stance`/`Coaching corpus`, `Training load`, `User materials`, conversation-first surface) is now the shipped system. The vision and decisions below are retained as the design record. Net-new work beyond this roadmap is tracked separately (the §6 open leaf-questions, follow-ons #339 cross-activity chat threading and #340 streamed-chat validation, the Phase-2 multi-user track, and deterministic-correctness as an ongoing discipline). §9 maps the original essence dump to where each concern landed.

---

## 1. The essence (preserved verbatim)

The owner's original brain-dump, kept word-for-word so nothing is lost in summarisation. Everything below in this doc is derived from it.

> I would like to discuss the AI system. I've read the latest running trace for v7 and the judge report, and reviewed the last week of activities with their reports. I'm not exactly sure how to articulate what we should talk about, so I'm just going to dump a load of thoughts in here, in no particular order, and you can help me sort them out.
>
> I've been thinking about how the AI coach should feel, its purpose, and what it could be. The reports and analysis are coming out very samey, and I would not say they are particularly useful, entertaining, or instructive. I think we need to think of this more as building a relationship, the same way a person would build any relationship with another person. I would like more personality, for it to feel like the best of what a machine can do and the best of what an AI can do. I think this means rethinking the interface between the AI system and the user.
>
> I have a belief that AI products like this should feel very personalised and flexible to the user. Case scenario: 64-year-old woman running with Run Club every week; 26-year-old man running as part of an active lifestyle and training for a half marathon; 35-year-old woman just trying to keep fit, primary focus is not running, just good cardiovascular fitness; 29-year-old man bodybuilder; 50-year-old man who enjoys ultra endurance events like ultramarathons and ultratriathlons. All these people require different types of AI coach. Some may want the coach to be more encouraging, kind, fun, less data and more sentiment-driven. Others may want more accountability, motivation, advice, data analysis.
>
> Another thing I've alluded to above is the interface between the user and the AI coach. I very much like the Telegram message coming in immediately. However, that interface with the message box right below the report instantly makes me think that I can chat with the coach; this feels like it would be a good addition. It makes me think that in fact any touch points for the AI should be a continuation of a conversation between the AI and the user. However, having had the Telegram running for a bit, I noticed that immediately after an activity my cognitive capacity is low. I am also wondering whether the report should in fact wait until the user has reported on key information: RPE, pain, and notes. The AI coach often currently seems to complain or note about not having this data. I think though it would be a shame, or a blocker, that this should be required. So I'm wondering if there's a way to do a light analysis or quick coach message that doesn't require the user input.
>
> Continuing with the theme of low cognitive effort on the user's part, I think it would be good if, when the AI coach has any questions, we should have pre-prepared options the user can just tap on, or a "chat about this" option.
>
> Another topic that comes to mind is the current training load. In Strava and Garmin they have deterministic measures of this. Strava has Fitness and Relative Effort. Garmin has Training Load, Exercise Load, and Load Focus. I think this would be a good metric to reproduce in our system, so our AI can more accurately determine the user's current condition.
>
> The next thing I've been thinking about is data and memory. This is a stateful system. I think the system is doing well at recording the raw data, but I'm not sure it's making the best use of it. We are condensing a lot of the stream data into one value or an amalgamated metric. However, we do need to be careful about context bloat. This is a context-engineering problem. I am training to become an AI engineer and how we manage data/memory in a sophisticated system is important. I also have a lot of learning and research I've been doing that would be very useful for this project but I'm not sure how exactly to utilise it. For example, I learned recently that forcing the AI to return a structured output before it's allowed to do its own thinking can hurt the response. That's just one example of many learnings and research I've been doing. Oh, one thing I forgot to say about memory and data is about summarisation and compaction, to protect the context window.
>
> We also need to be careful about some of our deterministic aspects of the system. I've been adding some issues about this to GitHub, and you have also noticed some things and added them to the judge report. Example: "interpretation": "This HR drift is likely inflated by terrain; discount it as a fatigue signal." And "Splits (5 min)" but in the table: Duration = 4 min.
>
> Another thing that comes to mind is what the AI coach relies upon for its philosophy. There are, of course, many schools of thought on training. I myself am currently listening to Born to Run on audiobook. There are many different avenues for learning and development, not just for how the AI responds but also what the user would respond to well. I think our system prompt is too conservative, too preoccupied with correctness, and doesn't feel human. There could also be an over-reliance on the deterministic values we are deriving, which again puts a strong need to make sure these deterministic mechanisms are correct.
>
> I myself often do multi-activity sessions, where I might do a walk and then a run and then some indoor rowing and indoor biking, all within the space of two hours. Currently the coach analysis makes no mention of this. I don't know exactly how it should, or should do anything with this, but I guess I'm wanting it to understand the pattern without being told. Everyone has their own training and we can't predict every situation.
>
> Moving forwards: the dump I just provided is a lot of information and is unstructured. I don't place priority, or indicate how we should use this. There's too much here to handle in one session or brief or report, but we do need to organise and scope into specific concerns. We need to decide what we are going to do next and how to handle the project going forwards. We need a clean understanding of how everything links together. This may lead to more research, briefs, milestones, tasks. What I don't want is to lose the essence of what I've dumped here.

---

## 2. The reframe, in one line

The coach is an ongoing **`Coaching relationship`**, not a per-activity report generator. A finished activity is one event within the relationship that may prompt the coach to speak. Almost every concern above resolves once this is the spine.

---

## 3. Decisions taken this session

Each maps to a glossary term in `CONTEXT.md`.

1. **Spine: relationship, not report.** The protagonist is the `Coaching relationship`; a `CoachReport` becomes one form a turn can take, not the system's output.
2. **Topology: hybrid.** One shared relationship memory, many activity-anchored entry points; every touchpoint reads from and writes back to the one memory.
3. **Cadence: two-stage + coach-decided depth.** The default post-event rhythm is a light, input-free opener now, then a fuller turn on the runner's reply or a timer (whichever first); the coach decides the depth of each (silence or a one-liner on an unremarkable event, depth on an interesting one). Unit: `Exchange`.
4. **Output: reason then structure, one call.** Extended thinking, then the human message (the product), then a thin structured tail (tappable options, optional citations). Belief/adherence write-back stays deterministic and off the LLM. Structure carries affordances and memory hooks, never content.
5. **Memory access: hybrid pull.** A lean `Working context` per exchange plus retrieval of deeper detail on demand from the raw store. Stops both context bloat and lossy pre-crushing.
6. **Durable memory: split.** Deterministic facts (auditable, authoritative for grounding) plus an LLM-consolidated narrative (authoritative for voice, never fact). Maintained by a background `Consolidation` job. Narrative never overrides measured data.
7. **Personalization: two dials, declared + adaptive.** `Voice` (how it talks) and `Coaching stance` (what it focuses on and its training philosophy) are independent dials, declared at onboarding and refined by the relationship. Voice flexes freely; stance stays tethered to the runner's real goal and the data. (Superseded in part by the P1 design session, `docs/vision/p1-voice-and-stance.md`: "adaptive" is reinterpreted as runner-sovereign explicit re-dialling, not secret LLM tone-inference. See §5 P1.)
8. **Knowledge: a `Coaching corpus`.** House principles (now) plus a retrievable schools-of-thought library (later) plus `User materials`. Conflicts resolved by an explicit `Authority tiering` (safety/data win; user materials beat house philosophy for stance; nothing lower overrides measured data or the safety floor). User-supplied content is untrusted input: data to reason about, never instructions to obey.
9. **Current condition: `Training load`.** Build our own deterministic acute/chronic/balance model from the per-activity load primitive (device-independent, auditable). Use platform numbers (Strava/Garmin) only for cold-start initialisation and validation, never as the source of truth. Depends on fixing `effort_score`.
10. **Event unit: `Block`.** Deterministically group temporally-contiguous activities into one training event; the coach reasons and speaks about the block (one exchange per block), not each sub-activity. Per-activity analysis is unchanged underneath.
11. **Sequencing: foundation-first.** Fix the deterministic substrate before the architecture reframe, because the new design leans on that substrate harder, not less.

---

## 4. How it all links (the layered map)

Four layers, bottom-up. Each depends on the one below.

1. **Foundation (correctness).** The deterministic mechanisms the whole product grounds on. Blocks everything above.
2. **Architecture (the reframe).** `Coaching relationship` -> `Exchange` -> prose output -> memory (`Working context` / split `Durable memory` / `Consolidation`, pull-based) -> `Block` as the event unit.
3. **Intelligence and personalization.** `Voice` + `Coaching stance` (declared + adaptive) -> `Coaching corpus` (house, schools, `User materials`) under `Authority tiering` -> `Training load`.
4. **Interaction.** Two-stage cadence, tappable low-effort input, chat-as-continuation, wait-for-input-with-timer.

---

## 5. Dependency-ordered roadmap

One concern per milestone, in the M0-M10 style (one issue + one PR each). Order respects the dependency map.

### Phase 0: Foundation (correctness). Ships on the current architecture.
- **F1. Per-activity load primitive (`effort_score`).** Use athlete-max HR, separate load from intensity. Foundation for `Training load`. (Issue #168.)
- **F2. discount_signals magnitude gate.** Do not instruct a discount when drift is not actually elevated; align the eval rubric assertion. (Judge-report finding, unfiled.)
- **F3. Confidence-reasons leak.** Keep interval/`no_intervals_detected` reasons out of overall confidence. (Issue #169.)
- **F4. Interval detection from recorded laps + framing.** Use Strava laps as the structure source; stop the coach over-indexing on low detection confidence. (Issues #170, #171.)
- **F5. Splits label/duration display bug.** "Splits (5 min)" vs Duration 4 min. (Unfiled.)

These are largely independent and parallelisable; several are already filed.

### Phase 1: Architecture reframe. Large; decomposed.
- **A1. Relationship + Block model.** Data model for `Coaching relationship`, `Block` grouping (time-gap detection, split/merge), and `Exchange` records.
- **A2. Memory architecture.** `Working context` / split `Durable memory` / `Consolidation` background job; pull/retrieval over the raw store. Reworks the M4-M10 learning loop into the new memory model (the loop needs updating here anyway). Verify and migrate the deterministic write-back seam.
- **A3. Output reframe.** Reason -> message -> thin tail; demote the structured `CoachReport` to a prose `Exchange`; confirm the policy validator governs prose; frontend renders a message. (ADR.)
- **A4. Cadence + pipeline.** Two-stage exchange, coach-decided depth, block-complete trigger, never block on input. Reworks the Process-new-activity pipeline.

### Phase 2: Intelligence and personalization.
- **P1. Voice + Coaching stance.** Re-cut during the P1 design session (`docs/vision/p1-voice-and-stance.md`) from one milestone into three sequential, dependency-ordered deliverables (one concern / one PR each). Choosing to ship full training philosophy via a small retrievable library — rather than emphasis dials alone — made P1 phase-sized and pulled the core of the old P2 corpus, and a partial slice of Authority tiering, forward into P1. Adaptive refinement is dropped in favour of runner-sovereign explicit re-dialling (the coach may raise the voice question out loud, but never secretly infers tone): under trust-the-user, secret refinement is the wrong feature, not a deferred one.
  - **P1.1 Voice dials.** Declared dials, prompt injection so the coach talks in the declared voice, the 4-pole personality radar graph, the 6-preset cast plus moderate default, free-text escape-hatch. No corpus dependency; fastest visible win; ships first and alone.
  - **P1.2 Corpus substrate.** Stored + retrieved philosophy library (keyed lookup over a small house-authored library, riding the existing `retrieval.py` seam, no vector DB), plus partial `Authority tiering` (safety/data outrank philosophy). This is the core of the old P2 pulled forward.
  - **P1.3 Stance dials.** Philosophy selection consuming the P1.2 corpus, plus the two emphasis axes (Data ↔ Sentiment, Process ↔ Outcome). Stance stays goal-tethered: philosophy reweights emphasis and method-framing, never licenses unsupported advice or overrides measured data.
- **P3. Training load model.** Acute/chronic/balance; platform numbers for init + validation. Depends on F1.
- **P4. User materials ingestion.** Untrusted-input-safe retrieval into the corpus. Carries the hardest `Authority tiering` tier (user materials beat house philosophy), deferred from P1.2 because it needs `User materials`, which still does not exist.

### Phase 3: Interaction surface.
- **I1. Tappable low-effort input.** Quick RPE/pain options on the opener.
- **I2. Chat-as-continuation.** Unify chat into the relationship (shared memory), not per-activity silos.
- **I3. Conversation-first frontend.**

---

## 6. Open leaf-questions (deferred, not lost)

- Stage-2 timer duration; `Block` time-gap threshold and tuning.
- Whether `Consolidation`'s narrative is LLM-written or templated, and how often it runs.
- Retrieval implementation (tool-use vs RAG); the exact contents of the lean default `Working context`.
- Onboarding flow for declaring `Voice` and `Coaching stance`.
- Scope of the frontend rework.
- The learning-loop write-back seam: confirm it reads only deterministic signals before A2 (deferred during the session).
- Exact `effort_score` reformulation; relationship to capturing resting HR / HR recovery (#166).
- How all of this interacts with the Phase-2 multi-user plan (ADR 0005).
- How to channel the owner's ongoing AI research (e.g. reason-before-structure, already adopted) into specific milestones.

---

## 7. ADR candidates

Write each when its milestone is picked up, not before. Each meets the bar (hard to reverse, surprising without context, a real trade-off):
- **The coach is a `Coaching relationship`, not per-activity reports** (the spine). Defines the domain going forward.
- **Coach output is a prose `Exchange`; the structured `CoachReport` is demoted to one optional shape.** Trades per-claim machine-traceability for humanity and reasoning quality. (Tied to A3.)
- Possible: **split `Durable memory` (deterministic facts vs LLM narrative) with pull-based retrieval.** (Tied to A2.)
- Possible: **`Block` as the event unit.** (Tied to A1.)

---

## 8. Glossary terms added this session

In `CONTEXT.md`: `Coaching relationship`, `Exchange`, `Working context`, `Durable memory`, `Consolidation`, `Voice`, `Coaching stance`, `Coaching corpus`, `User materials`, `Authority tiering`, `Training load`, `Block`.

Reconciliation pending: the existing `Notification`, `Process new activity`, and `CoachReport`-centric entries are written with the artifact as protagonist and will need updating once the reframe lands.

---

## 9. Essence coverage (reconciliation, 2026-06-17)

The owner's one stated fear in the §1 dump was "I don't want to lose the essence." This maps each substantive concern in that verbatim dump to where it landed. Every concern is addressed by shipped, merged work; the residual threads are net-new, not lost.

- **Samey / not useful / entertaining / instructive reports; build a relationship; more personality; rethink the interface.** The relationship spine (decision 1), A3 prose output (ADR 0009), `Voice` (P1.1), and the two-stage `Exchange` plus receipt cadence (A4 / ADR 0010 / 0018). Entertaining is literally dial-able (e.g. the high-humor "roast" voice preset).
- **Different users need different coaches (the five personas); sentiment vs data, encouragement vs accountability.** `Voice` dials (warmth / humor / directness / energy) plus `Coaching stance` emphasis axes (Data vs Sentiment, Process vs Outcome) plus `Coaching corpus` schools. Serving many distinct people at once is the separate multi-user track (ADR 0005), not this roadmap.
- **Immediate Telegram message; chat as a continuation; low cognitive capacity post-run; the report should not require RPE / pain but should offer them; a light input-free message.** A4 two-stage `Exchange` and the #296 receipt cadence (an instant deterministic receipt that needs no input, with the full report later); I1 tappable RPE / pain; I2 / I3 chat-as-continuation.
- **Pre-prepared tappable options / a "chat about this" option.** I1 Telegram inline buttons; the report's tappable question options; I3 starter chips on the chat surface.
- **Reproduce Strava / Garmin training load to read current condition.** P3 `Training load`, our own deterministic acute / chronic / form model (ADR 0016), built on the corrected F1 `effort_score` primitive; platform numbers stay validation-only.
- **Stateful memory; make best use of the raw data without context bloat; reason before structure; summarisation / compaction.** A2 four-layer pull memory (working context, split durable memory, `Consolidation`; ADR 0008); A2a consolidated stream view pulled on demand; A3 reason then message then thin tail (directly the owner's "structured output before thinking can hurt" research).
- **Deterministic correctness (the terrain-discount and splits-label examples).** Phase 0 (F2 / #176 magnitude gate for the terrain-discount; F5 / #174 splits label vs duration; F1 / F3 / F4). This stays an ongoing discipline, not a finished task: deterministic bugs are still found and fixed (#297, #310, #189, #325).
- **Coaching philosophy / schools of thought / Born to Run / system prompt too conservative and not human.** P1.2 `Coaching corpus` (house schools, including enjoyment-and-consistency) plus P1.3 `Coaching stance` selection plus `Voice`. Partial: retuning the base-prompt philosophy into the corpus house-core is still open (#266).
- **Multi-activity sessions understood without being told.** A1 `Block` (deterministic time-gap grouping; the coach reasons and speaks about the block). Verified live on the owner's own walk + row + bike block, where a v7 report discussed the combined ~98-minute session.

Net-new (tracked, not lost): channelling the owner's ongoing AI research into milestones (§6, open-ended); the full cross-activity conversation unification (#339); streamed-chat validation (#340); multi-user so the personas literally coexist (Phase-2 track); deterministic correctness as a permanent ongoing effort.
