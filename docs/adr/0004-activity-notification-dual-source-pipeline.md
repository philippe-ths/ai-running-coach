# Activity notification uses a dual-source pipeline with one convergence job

The runner wants the coach report delivered out-of-band (email) as soon as possible after completing a run, without depending on opening the laptop frontend. The Strava webhook is the fastest signal, but it requires a public HTTPS endpoint, which is not part of the local-first runtime. A polling fallback is needed for the case where no tunnel is configured.

Both paths converge on a single job: `process_new_activity_job(athlete_id, activity_id)`, which runs ingest → analyze → generate coach report → notify. The webhook handler dispatches `aspect_type=create` events to this job (and keeps the existing `sync_activity_job` for `update` events). A scheduled polling job (`rq-scheduler`, ~2 minute interval) calls `ingest_recent_activities`, diffs against the local DB, and enqueues `process_new_activity_job` for each previously-unseen activity.

Dedup is enforced by a single `coach_notification_sent_at` column on `Activity`. Webhook and polling can both fire for the same activity (e.g., webhook is briefly delayed and polling catches it first) without producing duplicate emails: whichever path finishes first sets the timestamp; the other observes it and skips the send.

The notifier is a port (`NotifierPort.send(subject, html, text, to)`) with two adapters: `SMTPNotifier` for production and `InMemoryNotifier` for tests. This mirrors the Strava port pattern established in [ADR 0002](0002-strava-adapter-is-pure-transport.md) and keeps the pipeline job free of transport concerns.

Failure semantics:
- SMTP failure raises out of the job; `coach_notification_sent_at` stays null; RQ retries per its bounded policy. Permanent failures surface in `rq info`.
- LLM failure produces the existing canned fallback `CoachReport`, marked with a new `is_fallback` flag. The notifier checks this flag and skips the send so the inbox does not fill with "analysis unavailable" emails on transient LLM hiccups.
- The notifier is a no-op when `SMTP_HOST` is unset, so the feature is implicitly off in dev environments without credentials.

## Consequences

- New dependency: `rq-scheduler`. A second worker process (`rqscheduler`) is added to docker-compose alongside the existing `rq worker`.
- Schema change: a new `coach_notification_sent_at` column on `activity` and an `is_fallback` boolean on `coach_report`, both via alembic migration.
- New env vars: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `NOTIFY_TO`, `APP_BASE_URL`.
- The webhook handler now branches by `aspect_type`. `create` enqueues the new pipeline job; `update` keeps the existing thin re-ingest job; `delete` is unchanged.
- The polling job hits Strava ~720 times/day, well under the documented 1000/day account limit. Per-15-minute spikes are bounded (~7 calls).
- Future architecture reviews should not re-suggest collapsing the two source paths into "webhook-only" — the polling path is the local-first runtime's only reliable trigger when no tunnel is set up. Likewise, do not re-suggest promoting `coach_notification_sent_at` to a separate `ActivityNotification` table until there is a second channel that needs distinct dedup state.
