# Project glossary

Definitions resolved during design discussions. Implementation details belong in code; this file is the shared vocabulary only.

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
