# Strava ingestion does not call analyze

The previous `sync_recent_activities` did three things in one call: fetched activities from Strava, persisted them, and immediately invoked the processing pipeline (`engine.process_activity`). That coupled ingestion to analysis inside a single function and hid the dependency from any caller that wanted one without the other (e.g., backfill, replay, fixture-loading).

Ingestion stops at persistence. `ingest_recent_activities` returns the persisted `Activity` rows. Callers compose: the API sync endpoint and the webhook job each call `analyze(db, activity.id)` per returned activity, explicitly.

## Consequences

- Two existing callers gain one extra call each (`api/activities.py`, `jobs/strava_sync.py`).
- New use cases (ingest-without-analyze for backfill, analyze-without-ingest for re-processing) become first-class and need no boolean flag.
- Future architecture reviews should not re-suggest fusing the two back together: the composition is deliberate and the de-coupling is the point.
