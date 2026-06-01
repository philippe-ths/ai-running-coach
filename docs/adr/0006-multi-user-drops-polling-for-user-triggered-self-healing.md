# Multi-user sync drops the polling fallback in favour of user-triggered self-healing

[ADR 0004](0004-activity-notification-dual-source-pipeline.md) introduced a polling fallback alongside Strava webhooks and explicitly warned future architecture reviews against collapsing the two sources into "webhook-only". That guidance was correct for the single-user, local-first runtime it was written for. It does not survive the move to a multi-user deployed service, and this ADR supersedes it.

The problem is the Strava rate-limit budget. Strava's published limits — 100 requests per 15 minutes and 1000 per day — are scoped **per OAuth application**, not per athlete. ADR 0004's "~720 calls/day, well under the 1000/day limit" arithmetic is per-account and is correct in single-user. Multiplied across even a small multi-user population, the polling job alone exhausts the application's daily budget within minutes of any real usage and starves every other Strava call (initial OAuth, manual sync, stream fetches) of headroom. There is no per-user polling cadence that scales linearly: even one poll per user per hour against 100 users is 2,400 calls/day, well over the ceiling.

Webhooks remain the primary signal. The fallback for missed webhooks becomes user-triggered:

- On app-open (any authenticated activity-listing request), the backend fetches the user's most recent activity id from Strava (one API call), compares to the latest `Activity.strava_activity_id` stored for that user, and if Strava is ahead, enqueues `process_new_activity_job` for each missing id. This costs zero when nobody is using the app and converges immediately for any active user.
- A manual "Refresh" affordance in the UI calls the same self-healing path explicitly, for the user who knows they just finished a run and is impatient.
- Inactive users (no app-open in days) do not get caught up until they return. This is acceptable: a user who has not opened the app has not been waiting on their coach report. The webhook will have caught the vast majority of activities anyway; the self-healing check exists for the long tail.

The time-based polling job and its scheduler are deleted entirely:

- `app/jobs/polling.py` removed.
- `app/jobs/scheduler.py` removed.
- The `rqscheduler` process disappears from the runtime topology. The deployment runs two processes (web + worker) instead of three. `docker-compose.yml`'s scheduler service is removed for parity.
- `POLLING_INTERVAL_SECONDS` is dropped from settings.

ADR 0004's dedup mechanism (`coach_notification_sent_at` on `Activity`) is **retained unchanged**. The self-healing path and the webhook path are still two sources of the same `process_new_activity_job`, and the same race condition (webhook briefly delayed; self-healing catches it first, or vice versa) is still possible. The dedup column continues to be the single source of truth for "did this activity already get processed."

## Consequences

- ADR 0004's "do not re-suggest webhook-only" guidance is superseded for the multi-user runtime. Single-user local development can keep using the manual `POST /api/sync` path, which is unaffected.
- Activity-list endpoints gain a side effect: hitting Strava on open. This needs to be bounded — a per-user lock or a short Redis-cached "last-checked" timestamp so refreshing the page rapidly does not multiply Strava calls. Suggested ceiling: at most one self-healing check per user per minute.
- Strava rate-limit headroom under 100 users becomes roughly: (initial OAuth + ingestion bursts on signup) + (per-user app-opens × 1 call) + (per-activity ingestion). At expected usage this lives well inside the 1000/day budget with margin for traffic spikes.
- `project-context.md` requires an update when this change ships: the "Strava ingestion" paragraph removes the polling-fallback sentence, and the "Important Constraints" paragraph removes the `POLLING_INTERVAL_SECONDS` line. Both are tagged as current-truth fields; defer the edit until the code lands.
- Webhook reliability becomes load-bearing. Sentry alerting on webhook handler errors and on `process_new_activity_job` failures gains importance. The Phase 1 observability setup must already be in place when this lands.
- This decision is bounded to the multi-user runtime. If the project ever reverses course and reverts to a strictly local-first single-user product, the polling path can come back; the historical reasoning in ADR 0004 still applies in that world.
