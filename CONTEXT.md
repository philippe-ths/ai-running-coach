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
