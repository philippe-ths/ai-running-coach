# Project glossary

Definitions resolved during design discussions. Implementation details belong in code; this file is the shared vocabulary only.

## Coaching relationship

The durable, per-runner relationship the coach maintains over time: one continuous coaching memory and narrative per `User`, not a pile of per-activity artifacts. A finished `Activity` is an *event* within the relationship that may prompt the coach to speak, never the unit of the relationship itself. The relationship holds the runner-model the coach carries forward (the narrative so far, what it has learned via `Belief` and `Preference profile`, and the open threads), and every coach touchpoint reads from and writes back to this one shared memory, so each touchpoint is a continuation rather than a fresh start. Distinct from a single `Coach report generation`, which under this model is one *form* a turn in the relationship can take, not the relationship itself.
_Avoid_: treating the coach's output as a standalone per-activity report; the protagonist is the relationship, and a report is a move within an ongoing conversation, not the conversation itself.

## Exchange

One coach↔runner turn-or-burst within a `Coaching relationship`, anchored to an event (most often a finished `Activity`, but also a `CheckIn` or a chat reply). The default post-activity exchange is two-stage: an immediate light, input-free opener (a brief human reaction plus tappable `Perceived effort`/pain prompts, never blocking, never nagging for missing data), then a fuller turn triggered by the runner's reply or a timer, whichever comes first, folding in any input that arrived. The coach decides the depth of each stage from whether the event warrants it: silence or a one-liner on an unremarkable run, depth on an interesting one. Every exchange reads from and writes back to the relationship's memory. A `Coach report generation` is one heavyweight form an exchange can take, not a synonym for it.
_Avoid_: equating "exchange" with "report"; a report is one shape an exchange can take, and many exchanges are light or silent.

## Working context

The bounded set of information assembled into the prompt for one `Exchange`: the relationship's narrative summary plus this event's headline facts, kept deliberately lean. It is not the whole of what the coach can know: the coach pulls deeper detail from the raw store on demand (retrieval) rather than receiving a fixed, pre-decided pack. Distinct from `Durable memory` (what persists across exchanges) and the raw store (the append-only source of truth, never loaded wholesale). The thing context engineering protects: small by default, deep on demand.
_Avoid_: equating working context with "everything we know about the runner"; it is the lean slice assembled for one turn, not the memory itself.

## Durable memory

The per-runner relationship memory that persists across exchanges and is carried forward, split into two layers with different authority. The deterministic layer holds rule-derived, auditable facts the coach grounds claims on (`Belief`, `Preference profile`, training load, confirmed confounds) and is authoritative for facts. The narrative layer is an LLM-consolidated, bounded story of the relationship (the arc so far, tone that lands, soft observations, open threads) and is authoritative for voice but is colour, never fact. The boundary is absolute: the narrative layer can never override a re-derived `DerivedMetric` or a deterministic fact, and can never be the cited source of a factual claim. Maintained by `Consolidation`.
_Avoid_: letting the narrative layer harden into fact, or treating durable memory as a transcript of raw runs; it is a gated, decaying generalisation, and the raw store remains the source of truth the current run always re-derives from.

## Consolidation

The background process that updates `Durable memory` after an `Exchange`, decoupled from producing the exchange itself so the user-facing turn never waits on it. It re-derives the deterministic facts (auditable, as today) and re-consolidates the narrative layer from those facts plus recent exchange history, so the narrative self-corrects against ground truth rather than drifting. The compaction mechanism that keeps the relationship's history bounded without discarding it: detail stays queryable in the raw store via retrieval.
_Avoid_: coupling consolidation to exchange generation, or letting it invent facts the deterministic layer does not support.

## Voice

How the coach talks, as a personalization dial independent of `Coaching stance`: warm vs blunt, cheerleader vs drill-sergeant, terse vs expansive, playful vs clinical. Declared by the runner at onboarding and then adapted by the `Coaching relationship` from how the runner actually responds (the `Preference profile` loop generalised from advice-themes to tone). Voice may flex freely: it reshapes framing and delivery only, never the facts, the safety floor, or the data the coach grounds on.
_Avoid_: conflating voice with `Coaching stance`, or letting a preferred voice soften a data-warranted message; tone changes, substance does not.

## Coaching stance

What the coach focuses on and the training philosophy behind it, as a personalization dial independent of `Voice`: e.g. an ultra-endurance durability/fuelling lens, a "cardio serves the lifting" lens for a strength athlete, an enjoyment-and-consistency lens for a recreational runner. Declared at onboarding and refined over time, but unlike `Voice`, stance is tethered: it adapts only within the bounds of the runner's actual goal and what the data supports, and never overrides the safety/grounding floor. The coach does not give a runner training that contradicts their real goal because a stance was selected.
_Avoid_: letting stance license advice the data does not support, or treating it as fixed; it is goal-tethered and refined by the relationship.

## Coaching corpus

The retrievable knowledge layer the coach grounds its coaching *judgment* in (as opposed to its *facts*, which come from the data): a compact set of always-present house coaching principles, a deeper retrievable library of training schools of thought (selected and weighted by `Coaching stance`), and the runner's own `User materials`. Distinct from `Durable memory` (what the relationship has learned about THIS runner) and the raw store (measured data): the corpus is coaching knowledge, not runner state.
_Avoid_: treating the corpus as fact (it informs judgment, not grounding), or letting corpus text issue instructions to the coach.

## User materials

Coaching content the runner supplies to the relationship: their own methodology, a human coach's plan, a physio rehab protocol, race-day plans, a book passage that resonates. Ingested into the `Coaching corpus` as a high-authority source for `Coaching stance` (it beats the house philosophy, since it is *their* coach), but per `Authority tiering` never overrides measured data or the safety floor, and always treated as reference data the coach reasons about, never as instructions it obeys (untrusted input).
_Avoid_: treating user materials as commands, as fact, or as able to lower the safety floor.

## Authority tiering

The explicit precedence order resolving conflicts between the coach's knowledge sources, highest first: the safety/grounding floor; this run's measured data (`DerivedMetric`); deterministic durable facts (`Belief`, training load); user-asserted facts; `User materials` and philosophy; house principles; the schools corpus; base-model generic knowledge. The load-bearing calls: user materials beat the house philosophy for `Coaching stance`, but never override measured data or the safety floor, and user-asserted facts yield to measured data on a factual conflict. Mirrors the standing rule that a `Belief` never overrides today's re-derived `DerivedMetric`.
_Avoid_: letting any lower tier (corpus, materials, user assertions, generic knowledge) override measured data or the safety floor.

## Notification

A side-channel delivery of a `CoachReport` to the runner — distinct from the in-app artifact. The `CoachReport` is the structured analysis stored in the DB and rendered by the frontend; a notification is one transmission of that report (or a representation of it) to an external channel such as email.

A notification is at-most-once per `Activity` per channel. The sentinel for "this activity has been notified" lives on the `Activity` row (see `Notified at`), not on the `CoachReport`, because re-generating a report (e.g., `force=true`) must not re-fire a notification.

## Notifier

The abstraction for sending a notification. Modelled as a port (`NotifierPort`) with adapters per channel, mirroring `StravaPort` (see [ADR 0002](docs/adr/0002-strava-adapter-is-pure-transport.md)). Today: `TelegramNotifier` (HTTPS Bot API) is the deployed channel because Railway blocks outbound SMTP, `SMTPNotifier` is the local/Pro-plan email fallback, `InMemoryNotifier` is for tests, and `NoOpNotifier` is used when no channel is configured. Adding a new channel means adding a new adapter, not modifying the port.

## Notified at

The single sentinel for notification dedup: a timestamp column on `Activity` indicating that a notification has been successfully sent for that activity. Null means "not yet notified." Set after a successful send; left null on failure so retries can re-send.

## Process new activity

The convergence pipeline triggered when a previously-unseen activity appears, regardless of source (Strava webhook `create` event or polling discovery). One pipeline: ingest → analyze → generate coach report → notify. Both source paths enqueue the same job; the `Notified at` sentinel makes re-entry safe.

## Polling job

A scheduled catch-up that periodically asks Strava for recent activities, diffs against the local DB, and enqueues `Process new activity` for each unseen one. Exists alongside the webhook path, not instead of it. Rationale in [ADR 0004](docs/adr/0004-activity-notification-dual-source-pipeline.md).

## Activity ingestion

Persisting a Strava activity (and its streams) into the local DB. Does not include analysis or notification. Composed with those steps at the job/orchestrator layer per [ADR 0003](docs/adr/0003-ingestion-does-not-call-analyze.md).

## Activity analysis

The deterministic processing pipeline that takes a persisted `Activity` + its `ActivityStream` rows and produces a `DerivedMetric`. Does not include coach report generation or notification.

## Coach report generation

The LLM-driven step that builds a context pack from a `DerivedMetric`, calls Anthropic, validates the response against the schema and the policy validator, and persists a `CoachReport`. Distinct from notification: a report can exist without ever being notified (e.g., older runs, low-confidence runs delivered only on-demand via the UI).

## Activity classification

The set of independent descriptors `Activity analysis` assigns to an activity, replacing the former single mutually-exclusive `activity_class` label. An activity is described along several orthogonal axes at once, because a real effort is several things at once (a long run can be run at threshold; an interval session is short, structured, and hard). The axes are sport-agnostic by design so the coach can reason about every cardio effort on one timeline. Rationale in [ADR 0007](docs/adr/0007-activity-classification-is-orthogonal-axes.md).
_Avoid_: activity class, activity type — both implied a single label.

## Effort

The intensity axis of `Activity classification`: how hard the effort was, derived from heart rate relative to the runner's maximum. Universal — applied to any activity with heart-rate data, whatever the sport. Distinct from `Duration` (how long) and `Structure` (the shape of the effort).
_Avoid_: intensity (reserve for informal use).

## Duration class

The length axis of `Activity classification`: whether an effort is long or standard relative to the runner's own recent efforts of the same sport. Relative, not absolute — "long" is defined against the individual's recent history, never a fixed time. Indeterminate for the first efforts in a runner's history, before there is enough history to compare against.

## Structure

The shape axis of `Activity classification`: whether an effort is continuous or composed of repeated work/rest intervals. Independent of `Effort` — a steady run and an interval session can reach the same average intensity.

## Terrain

A modifier in `Activity classification` describing the route as flat or hilly. A qualifier on an effort, not a class of its own — any run can be hilly.

## Race

A modifier in `Activity classification` marking a competitive effort, taken from the runner's `Stated intent` or the activity name, never inferred from the data.

## Headline

The single human-readable label for an activity (e.g. "Long run (tempo)"), composed from the `Activity classification` axes at read time. Presentation only — derived from the axes, never stored as a source of truth. Replaces the former stored `activity_class`.

## Stated intent

What the runner meant a session to be (`user_intent`), as opposed to what the `Activity classification` axes show was executed. The two are separate and may legitimately disagree; that gap is coaching signal, not an error. Stated intent overrides the `Headline` the runner sees but never overwrites the measured axes.

## Perceived effort

How hard a session felt to the runner, captured as `CheckIn.rpe` (a Borg-style 1-10 rating), as opposed to `Effort`, which is the intensity measured from heart rate. The two can legitimately disagree, and that gap is coaching signal, not error: when a confounder suppresses heart rate (heat, for example) a run can feel hard while `Effort` reads easy. The coach weights perceived effort above the heart-rate read when a `discount_signals` confounder fired, because perception survives the distortion. This mirrors the `Stated intent` vs measured-axes gap, moved from intent-vs-execution to perception-vs-physiology.
_Avoid_: treating RPE as a synonym for `Effort`; they are the subjective and measured sides of the same question.

## Adherence

Whether the runner appears to have acted on the coach's prior advice, judged from their own subsequent runs at zero extra effort. The unit is the `Next-step outcome`: for each `next_step` the last report emitted, the runner's next comparable activity is labelled `acted-on`, `ignored`, or `contradicted` by re-deriving from its `Activity analysis`. Adherence is advisory and never a compliance score or a moral judgement: the coach uses it to advance the relationship (acknowledge follow-through, gently revisit a miss), never to scold.

A label fires only on a comparable, non-noisy run. "Comparable" means the subsequent run is a fair test of that advice: easy-discipline advice is judged against the next run that was not a race, a detected interval session, or a declared workout (a clearly-deliberate hard effort is never counted against easy advice), and the strong `contradicted` verdict is only asserted when the runner's `Stated intent` for that run was explicitly easy, so an unlabelled run that came out hard is softened to `ignored` (it may have been a deliberate session) rather than treated as defiance. A low-confidence `DerivedMetric` is noise and abstains; a window theme abstains until the runner has had enough comparable runs to fairly call a miss. Adherence is contrast about past advice and never overrides the re-derived `DerivedMetric`, which remains the ground truth about what happened today.
_Avoid_: framing adherence as compliance, obedience, or a score; it is a coaching observation, not a verdict on the runner.

## Disputed

The `Next-step outcome` label when the runner has explicitly pushed back on the prior advice (a `CheckIn` note or a chat reply saying it was off). Explicit feedback beats the noisy implicit read: a disputed outcome is not non-adherence but a legitimate correction the coach takes the runner's word on and adapts to. Mirrors the `Stated intent` precedent that the runner's stated meaning overrides what the data alone would imply.

## Belief

A durable, per-runner fact the coaching relationship has learned and the coach carries forward: a confirmed HR confound ("this runner's HR reads inflated in heat"), an `Adherence` tendency ("responds to easy-day discipline"). Beliefs are what turn the `CoachReport` history from a pile of artifacts into a runner-model. They are **deterministically derived** from the pipeline's own signals (`discount_signals`, adherence outcomes), never free LLM text, so they stay auditable. Each belief lives in the `CoachingContext` store, written through gates (a single observation is not yet a belief; observations reinforce, and an opposing observation resolves the belief's direction in place rather than stacking a contradiction), and ages out by a TTL tier unless reinforced. Every retrieved belief carries `confidence` and recency tags so the coach hedges stale or thin ones. A belief is **prior context, never an override**: when a belief and this run's re-derived `Activity analysis` conflict, today's measured data wins.
_Avoid_: treating a belief as ground truth, or as a memory of raw runs; it is a gated, decaying generalisation the current run can always correct.

## CoachingContext

The durable, user-scoped store of `Belief` rows (one row per belief, identity `(user, kind, key)`). Written after each non-fallback `Coach report generation` (the belief write-back) and read into every later context pack with confidence/recency tags. The persistent-context half of the moat: the read-back layer that makes confound-correction and adherence awareness fire automatically on later runs instead of being re-derived from nothing each time. Distinct from `RunnerBaseline` (numeric rolling norms) and from `Activity analysis` (this run's re-derived metrics, which always override it).

## Calibrated correction

Reading a run's signal against the runner's OWN typical value for the same conditions rather than a population rule of thumb: "your HR drift was 12%, vs your typical ~5% for these conditions" instead of "drift over 5% means fatigue". Computed at read time from the runner's prior comparable runs in the same `effort|terrain|temperature-band` bucket. It abstains to a LABELED population heuristic until enough comparable history accrues, so a confident personal claim never outruns the evidence. It refines interpretation and never overrides the re-derived `Activity analysis`.
_Avoid_: stating a population threshold as if it were this runner's established norm before the baseline is sufficient.

## Referral nudge

A deterministic, pipeline-owned suggestion to consider a healthcare professional, fired only on a computable red-flag pattern (several strain signals together; pain persisting across runs) and surfaced as a non-diagnostic prompt. It is the permitted form of the medical-scope boundary: it never names a condition, uses a diagnosis verb, or asserts what a pattern means, and its text is written to pass the deterministic policy validator (which still governs it). It abstains rather than ever fabricating a health concern. This keeps the product inside the general-wellness lane.
_Avoid_: treating the referral nudge as a diagnosis, a screening result, or a clinical claim; it is a general "worth getting this looked at" prompt, nothing more.

## Preference profile

The per-runner signal of which kinds of advice the runner demonstrably acts on, ignores, or is mixed on, derived from the accumulated `Adherence` record. The coach uses it to RERANK and FRAME its `next_steps` toward what lands: lead with advice in themes the runner acts on, reframe (not just repeat) advice in themes they ignore. It is preference-conditioned framing, not a trained reward model and not a base-model fine-tune; the "reward signal" is the runner's own follow-through. It biases selection and framing ONLY: it never overrides the re-derived `Activity analysis`, never fabricates advice the data does not support (a needed session the runner usually skips is still given, reframed), and safety/grounding always win over preference. It is already explicit-feedback-aware, because a `Disputed` outcome never reinforced the adherence beliefs it is built from.
_Avoid_: letting preference suppress data-warranted advice, or treating it as a model that generates advice; it only reranks and frames advice the data already supports.

## Training load

The deterministic read of the runner's current condition (fresh, fatigued, ramping, detraining), built as our own acute (≈7d) / chronic (≈28-42d) / balance model from the per-activity load primitive (`effort_score`), so it is device-independent and auditable. A tier-3 deterministic durable fact (see `Authority tiering`): it grounds the coach's judgment of readiness but never overrides this run's measured data or the safety floor. Platform numbers (Strava Relative Effort/Fitness, Garmin Training Load) are used only to cold-start the chronic baseline for a new runner and to validate our computation, never as the authoritative value, and a divergence is a signal to fix our model, not to defer to theirs. Depends on a correct per-activity load primitive (the `effort_score` fix).
_Avoid_: treating a platform's load number as our source of truth, or building the model on the unfixed per-activity primitive.

## Block

A deterministically-detected group of temporally-contiguous activities treated as one training event: the walk→run→row→bike done back-to-back in a morning is one block; a solo run is a block of one. Grouping is by time-gap clustering (auditable); per-activity `Activity analysis` is unchanged underneath (each activity's measured metrics remain the truth), and the block adds an aggregate layer (combined load, the sequence and shape of the bout). The coach reasons and speaks about the block, not each sub-activity: one `Exchange` per block, fired once the block looks complete. The runner can split or merge a grouping the detector got wrong.
_Avoid_: treating a block as a replacement for per-activity analysis (it composes it, never overrides it), or assuming the time-gap grouping is always right.
