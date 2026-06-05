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
